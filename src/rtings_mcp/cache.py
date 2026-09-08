"""The JSON-file cache (SPEC §8).

**Files, not a database.** RTINGS' API is parameterized and ``table_tool__test_results``
has no product filter, so one request is ``(test_bench_ids[], original_ids[])`` and one
response covers *every* product on those benches for those tests. Store the response as
fetched and **the file path is the coverage record** — which is the entire reason the
ported SQLite design needed a separate ``fetch_coverage`` table, and why this one does not.

Four rules here are load-bearing rather than housekeeping:

1. **``cache_tier`` (stored, probe-derived) is not ``data_tier`` (derived, data-derived).**
   Storing a data-derived tier and requiring ``data_tier >= configured`` deadlocks forever
   on public tests, free accounts and all-``na`` slices. The hit rule is probe-vs-probe.
2. **``fetched_at`` is in the filename.** One slot per tier cannot express "freshest among
   those", ``superseded_at``, or "``refresh=true`` appends, never promotes" — and a
   same-tier refetch would be a whole-file overwrite, letting an all-blurred response
   clobber a good one.
3. **Reads merge across tier files, per row.** A gift link or metered preview unblurs one
   product inside an otherwise-blurred response, so no whole-file label is correct.
4. **Coverage carries a time dimension.** A slice fetched on day 1 covers the products that
   existed on day 1. A product outside that catalog generation is ``coverage_stale`` — a
   miss — never ``not_tested``.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors
from .config import CACHE_FORMAT_VERSION, Config
from .errors import RtingsError
from .fsutil import atomic_write_bytes, ensure_dir, sweep_temp_files

log = logging.getLogger(__name__)

ANONYMOUS = "anonymous"
FREE = "free"
MEMBER = "member"

#: ``free`` exists **only** on ``reviews/``. A free account unlocks nothing on the table
#: path, so a ``free`` probe fetching ``tests/``/``ratings/`` would miss every ``anonymous``
#: slice and write a byte-identical ``free`` copy.
TIER_ORDER: dict[str, int] = {ANONYMOUS: 0, FREE: 1, MEMBER: 2}

_ID_RE = re.compile(r"^\d{1,10}$")
_SILO_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# A best-of list path can be multi-segment: `/tv/reviews/best/by-size/65-inch`
# (verified 2026-09-03 — 20 lists on TV, several two-segment). A single-segment pattern
# would silently reject most of them.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}(?:/[a-z0-9][a-z0-9-]{0,63}){0,2}$")
_VERSIONED_RE = re.compile(
    r"^(?P<key>.+)\.(?P<tier>anonymous|free|member)\.(?P<stamp>\d+)\.json(?P<gz>\.gz)?$"
)
_GENERATION_RE = re.compile(r"^(?P<key>.+)\.(?P<stamp>\d+)\.json$")

#: How often `flush_lru` may run the size-limit pass. The first pass is stat-only but
#: still walks the tree, and a cold multi-silo run writes hundreds of files.
SIZE_CHECK_INTERVAL_S = 300.0

OUTCOME_OK = "ok"
OUTCOME_GRAPH_NOT_AVAILABLE = "graph_not_available"
OUTCOME_EMPTY = "empty"


def now() -> float:
    return time.time()


def _stamp(fetched_at: float) -> str:
    return str(int(fetched_at * 1000))


def _unstamp(stamp: str) -> float:
    return int(stamp) / 1000.0


def tier_rank(tier: str) -> int:
    return TIER_ORDER.get(tier, 0)


def validate_id(value: Any, *, what: str) -> str:
    """Every caller-influenced path segment is validated before a path is built."""
    text = str(value)
    if not _ID_RE.match(text):
        raise RtingsError(errors.UNKNOWN_TEST, f"invalid {what}: {text!r}")
    return text


def validate_silo(value: str) -> str:
    text = str(value).strip().lower()
    if not _SILO_RE.match(text):
        raise RtingsError(errors.UNKNOWN_SILO, f"invalid silo: {value!r}")
    return text


def validate_slug(value: str) -> str:
    # NOT stripped of slashes: stripping would quietly turn "/absolute" into "absolute" and
    # accept a path the caller did not mean. A slug is relative, or it is invalid.
    text = str(value).strip().lower()
    if not _SLUG_RE.match(text):
        raise RtingsError(errors.UNKNOWN_PRODUCT, f"invalid list slug: {value!r}")
    return text


def slug_to_key(slug: str) -> str:
    """Flatten a multi-segment slug into ONE filename component.

    A slug is caller-influenced, so it never becomes a path segment: `/` would let
    `by-size/65-inch` create a directory, and a crafted value could climb out of the cache
    root. The value is validated first and flattened second — belt and braces.
    """
    return validate_slug(slug).replace("/", "__")


@dataclass(slots=True)
class Envelope:
    """One cached response, as stored.

    Never a bare API body: a cache read must be able to serve a unit and derive a
    ``data_tier`` without a second fetch. ``data_tier`` itself is **never** a field here —
    it is recomputed per response from the rows actually served.
    """

    fetched_at: float
    source_url: str
    cache_tier: str
    request: dict[str, Any] = field(default_factory=dict)
    payload: Any = None
    outcome: str = OUTCOME_OK
    catalog_generation: str | None = None
    product_ids: list[str] | None = None
    unpublished_product_ids: list[str] | None = None
    bench_id: str | None = None
    silo: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
            "cache_tier": self.cache_tier,
            "request": self.request,
            "outcome": self.outcome,
            "payload": self.payload,
        }
        for key in (
            "catalog_generation",
            "product_ids",
            "unpublished_product_ids",
            "bench_id",
            "silo",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Envelope:
        return cls(
            fetched_at=float(raw["fetched_at"]),
            source_url=str(raw.get("source_url", "")),
            cache_tier=str(raw.get("cache_tier", ANONYMOUS)),
            request=raw.get("request") or {},
            payload=raw.get("payload"),
            outcome=str(raw.get("outcome", OUTCOME_OK)),
            catalog_generation=raw.get("catalog_generation"),
            product_ids=raw.get("product_ids"),
            unpublished_product_ids=raw.get("unpublished_product_ids"),
            bench_id=raw.get("bench_id"),
            silo=raw.get("silo"),
            notes=raw.get("notes") or {},
        )

    def age(self) -> float:
        return max(0.0, now() - self.fetched_at)

    def is_stale(self, ttl: int) -> bool:
        """``stale`` means past-TTL and nothing else."""
        return self.age() > ttl


@dataclass(slots=True, frozen=True)
class Variant:
    """One file on disk for a versioned key."""

    path: Path
    tier: str
    fetched_at: float
    key: str


class Cache:
    """Owns the tree under ``RTINGS_CACHE_DIR``."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = config.cache_dir
        ensure_dir(self.root)
        self._check_format_version()
        removed = sweep_temp_files(self.root)
        if removed:
            log.debug("swept %d orphaned temp files", removed)
        self._lru_path = self.root / "_lru.json"
        self._lru: dict[str, float] = self._load_lru()
        self._lru_dirty = False
        #: Wall-clock of the last size-limit pass; see `_maybe_enforce_size_limit`.
        #: Zero so an already-oversized cache is trimmed on the first write, not 5 min in.
        self._last_size_check = 0.0

    # -- paths ----------------------------------------------------------------------

    def resolve(self, *segments: str) -> Path:
        """Join and guard. The final check is the one that matters: a path that escapes
        the cache root is refused whatever produced the segments."""
        path = self.root
        for segment in segments:
            if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
                raise RtingsError(errors.UNKNOWN_SILO, f"invalid path segment: {segment!r}")
            path = path / segment
        resolved = path.resolve()
        root = self.root.resolve()
        if not resolved.is_relative_to(root):
            raise RtingsError(errors.UNKNOWN_SILO, "path escapes the cache root")
        return path

    def _check_format_version(self) -> None:
        meta_path = self.root / "meta.json"
        version: int | None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            version = int(meta["format_version"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            # `None` means "no usable meta file" — a fresh cache, with nothing to clear.
            # It must stay distinct from a stored version that happens to be 0, or a real
            # format change would silently skip the wipe and serve incompatible payloads.
            version = None
        if version == CACHE_FORMAT_VERSION:
            return
        if version is not None:
            log.warning(
                "cache format %d != %d; clearing cached payloads", version, CACHE_FORMAT_VERSION
            )
            for name in (
                "silos.json",
                "bench",
                "schema",
                "catalog",
                "tests",
                "ratings",
                "reviews",
                "verdicts",
                "graphs",
                "recs",
                # `articles/` holds extractor output like `recs/` does, so it goes stale
                # in exactly the same way when the parser changes (added 2026-09-08).
                "articles",
                "observed",
            ):
                target = self.root / name
                self._remove_tree(target)
        atomic_write_bytes(
            meta_path,
            json.dumps({"format_version": CACHE_FORMAT_VERSION}).encode("utf-8"),
        )

    @staticmethod
    def _remove_tree(target: Path) -> None:
        try:
            if target.is_dir():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                target.rmdir()
            elif target.exists():
                target.unlink(missing_ok=True)
        except OSError:
            log.debug("could not clear %s", target, exc_info=True)

    # -- raw io ---------------------------------------------------------------------

    def _read_envelope(self, path: Path) -> Envelope | None:
        """A truncated or unparseable file is a **miss**, and it is unlinked."""
        try:
            if path.suffix == ".gz":
                raw = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            else:
                raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, ValueError, EOFError, gzip.BadGzipFile):
            log.debug("unreadable cache file %s; unlinking", path)
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return None
        try:
            envelope = Envelope.from_json(raw)
        except (KeyError, TypeError, ValueError):
            return None
        self._touch(path)
        return envelope

    def read_path(self, path: Path) -> Envelope | None:
        """Read one already-located file. Used when a caller has a :class:`Variant`."""
        return self._read_envelope(path)

    def _write_envelope(self, path: Path, envelope: Envelope, *, compress: bool = False) -> None:
        body = json.dumps(envelope.to_json(), separators=(",", ":")).encode("utf-8")
        if compress:
            body = gzip.compress(body, compresslevel=6)
        atomic_write_bytes(path, body, mtime=envelope.fetched_at)
        self._touch(path)

    # -- LRU ------------------------------------------------------------------------

    def _load_lru(self) -> dict[str, float]:
        try:
            raw = json.loads(self._lru_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def _touch(self, path: Path) -> None:
        """File mtime is ``fetched_at``, so it is not a recency signal and ``atime`` is
        unreliable. The LRU index is therefore explicit."""
        try:
            rel = str(path.relative_to(self.root))
        except ValueError:
            return
        self._lru[rel] = now()
        self._lru_dirty = True

    def flush_lru(self) -> None:
        if not self._lru_dirty:
            return
        try:
            atomic_write_bytes(
                self._lru_path, json.dumps(self._lru, separators=(",", ":")).encode("utf-8")
            )
            self._lru_dirty = False
        except OSError:
            log.debug("could not persist the LRU index", exc_info=True)
        self._maybe_enforce_size_limit()

    def _maybe_enforce_size_limit(self) -> None:
        """Bound the cache from the one hook every write batch already ends with.

        ``enforce_size_limit`` was fully implemented, documented as THE growth bound in
        SPEC §8, unit-tested — and called from nowhere in the serving path, so
        ``RTINGS_CACHE_MAX_MB`` had no effect and ``reviews/`` grew without limit (442 KB x
        548 TVs is 242 MB for one silo). ``prune_variants`` bounds variants per key, never
        total size.

        Throttled rather than run per write: the first pass is ``stat``-only but still walks
        the tree, and a cold multi-silo run writes hundreds of files.
        """
        checked_at = time.time()
        if checked_at - self._last_size_check < SIZE_CHECK_INTERVAL_S:
            return
        self._last_size_check = checked_at
        try:
            freed = self.enforce_size_limit()
        except OSError:
            log.debug("could not enforce the cache size limit", exc_info=True)
            return
        if freed:
            log.debug("evicted %d bytes to stay under RTINGS_CACHE_MAX_MB", freed)

    # -- untiered surfaces ----------------------------------------------------------

    def get(self, *segments: str) -> Envelope | None:
        return self._read_envelope(self.resolve(*segments))

    def put(self, envelope: Envelope, *segments: str) -> Path:
        path = self.resolve(*segments)
        self._write_envelope(path, envelope)
        return path

    # -- versioned surfaces (immutable generations; no tier) -------------------------

    def list_generations(self, *dir_segments: str, key: str) -> list[Variant]:
        directory = self.resolve(*dir_segments)
        if not directory.is_dir():
            return []
        out: list[Variant] = []
        for path in directory.iterdir():
            match = _GENERATION_RE.match(path.name)
            if not match or match.group("key") != key:
                continue
            out.append(
                Variant(
                    path=path,
                    tier=ANONYMOUS,
                    fetched_at=_unstamp(match.group("stamp")),
                    key=key,
                )
            )
        out.sort(key=lambda v: v.fetched_at, reverse=True)
        return out

    def newest_generation(self, *dir_segments: str, key: str) -> Envelope | None:
        for variant in self.list_generations(*dir_segments, key=key):
            envelope = self._read_envelope(variant.path)
            if envelope is not None:
                return envelope
        return None

    def get_generation(self, *dir_segments: str, key: str, fetched_at: float) -> Envelope | None:
        path = self.resolve(*dir_segments, f"{key}.{_stamp(fetched_at)}.json")
        return self._read_envelope(path)

    def put_generation(self, envelope: Envelope, *dir_segments: str, key: str) -> Path:
        path = self.resolve(*dir_segments, f"{key}.{_stamp(envelope.fetched_at)}.json")
        self._write_envelope(path, envelope)
        return path

    def prune_generations(self, *dir_segments: str, key: str, keep: int = 3) -> None:
        """Generations are immutable and pruned only when unreferenced. ``keep`` bounds the
        chain so a 3-day catalog TTL does not accumulate indefinitely; the newest is always
        retained because live slices point at it."""
        variants = self.list_generations(*dir_segments, key=key)
        for variant in variants[keep:]:
            with contextlib.suppress(OSError):
                variant.path.unlink(missing_ok=True)

    # -- tier-keyed surfaces --------------------------------------------------------

    def list_variants(self, *dir_segments: str, key: str) -> list[Variant]:
        """Every tier variant of a key, newest first."""
        directory = self.resolve(*dir_segments)
        if not directory.is_dir():
            return []
        out: list[Variant] = []
        for path in directory.iterdir():
            match = _VERSIONED_RE.match(path.name)
            if not match or match.group("key") != key:
                continue
            out.append(
                Variant(
                    path=path,
                    tier=match.group("tier"),
                    fetched_at=_unstamp(match.group("stamp")),
                    key=key,
                )
            )
        out.sort(key=lambda v: v.fetched_at, reverse=True)
        return out

    def read_variants(
        self, *dir_segments: str, key: str, min_tier: str = ANONYMOUS
    ) -> list[Envelope]:
        """All usable envelopes for a key at ``cache_tier >= min_tier``, newest first.

        The caller merges rows across them (rule 3). Returning a single "best" file here
        would defeat the merge: a 1-of-97 preview unblur lives in a file whose other 96
        rows are worse than the freshest anonymous copy.
        """
        wanted = tier_rank(min_tier)
        out: list[Envelope] = []
        for variant in self.list_variants(*dir_segments, key=key):
            if tier_rank(variant.tier) < wanted:
                continue
            envelope = self._read_envelope(variant.path)
            if envelope is not None:
                out.append(envelope)
        return out

    def put_variant(
        self, envelope: Envelope, *dir_segments: str, key: str, compress: bool = False
    ) -> Path:
        suffix = ".json.gz" if compress else ".json"
        name = f"{key}.{envelope.cache_tier}.{_stamp(envelope.fetched_at)}{suffix}"
        path = self.resolve(*dir_segments, name)
        self._write_envelope(path, envelope, compress=compress)
        return path

    def prune_variants(self, *dir_segments: str, key: str) -> None:
        """Keep, per key: the newest file per tier, **plus** the newest file holding any
        ``unblurred:true`` on an ``insider_only`` test.

        Age-pruning alone would delete exactly what never-downgrade preserves.
        """
        variants = self.list_variants(*dir_segments, key=key)
        if len(variants) <= 1:
            return
        keep: set[Path] = set()
        seen_tiers: set[str] = set()
        for variant in variants:  # newest first
            if variant.tier not in seen_tiers:
                seen_tiers.add(variant.tier)
                keep.add(variant.path)
        for variant in variants:  # newest first — first match is the newest unblurred
            envelope = self._read_envelope(variant.path)
            if envelope is not None and envelope_has_unblurred_insider(envelope):
                keep.add(variant.path)
                break
        for variant in variants:
            if variant.path not in keep:
                with contextlib.suppress(OSError):
                    variant.path.unlink(missing_ok=True)

    # -- eviction -------------------------------------------------------------------

    def enforce_size_limit(self) -> int:
        """LRU eviction to ``RTINGS_CACHE_MAX_MB``. Returns bytes freed.

        Two exemptions, both from SPEC §8: never evict a ``reviews/`` file whose
        ``cache_tier`` is ``free`` (it cost a metered preview and refetching spends another),
        and never the newest file holding an ``unblurred:true`` on an ``insider_only`` test
        for a key. ``member`` is deliberately **not** exempt as a tier — a member refetch is
        free, and exempting it would un-bound the cache for the primary user.

        The size pass is ``stat``-only and runs first: the exemption checks parse files, so
        doing them before knowing whether anything must be evicted would make every call
        expensive for nothing.
        """
        limit = self.config.cache_max_mb * 1024 * 1024
        candidates: list[tuple[float, int, Path]] = []
        total = 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            if path.name in {"meta.json", "_lru.json"} or path.parent.name in {
                "cooldown",
                "telemetry",
                "probe",
                "locks",
            }:
                continue
            rel = str(path.relative_to(self.root))
            candidates.append((self._lru.get(rel, 0.0), size, path))
        if total <= limit:
            return 0

        exempt = self._eviction_exempt_paths(candidates)
        entries = [c for c in candidates if c[2] not in exempt]
        entries.sort(key=lambda item: item[0])  # least recently read first
        freed = 0
        for _last_read, size, path in entries:
            if total - freed <= limit:
                break
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            freed += size
            self._lru.pop(str(path.relative_to(self.root)), None)
            self._lru_dirty = True
        self.flush_lru()
        return freed

    def _eviction_exempt_paths(
        self, candidates: list[tuple[float, int, Path]]
    ) -> set[Path]:
        """Resolve both exemptions in one pass over the versioned files.

        The newest-unblurred exemption is **tier-independent**. Gating it on ``free``/
        ``member`` would protect nothing today: 16 of 28 silos serve their ``insider_only``
        values unblurred *anonymously*, so almost every unblurred file on disk is
        ``anonymous``-tier — and evicting the only copy of a scored row silently downgrades
        later reads from ``tested_visible`` to ``not_tested``, which is the safety property
        failing by a different route.
        """
        exempt: set[Path] = set()
        by_key: dict[tuple[tuple[str, ...], str], list[tuple[float, Path, str]]] = {}
        for _last_read, _size, path in candidates:
            match = _VERSIONED_RE.match(path.name)
            if not match:
                continue
            parts = path.relative_to(self.root).parts
            if parts and parts[0] == "reviews" and match.group("tier") == FREE:
                exempt.add(path)  # bought with a metered preview
            by_key.setdefault((parts[:-1], match.group("key")), []).append(
                (_unstamp(match.group("stamp")), path, match.group("tier"))
            )
        for variants in by_key.values():
            for _fetched_at, path, _tier in sorted(variants, reverse=True):
                envelope = self._read_envelope(path)
                if envelope is not None and envelope_has_unblurred_insider(envelope):
                    exempt.add(path)
                    break
        return exempt


def envelope_has_unblurred_insider(envelope: Envelope) -> bool:
    """True when the stored payload holds at least one ``unblurred:true`` row on an
    ``insider_only`` test — the bit never-downgrade and pruning both key on."""
    return bool(envelope.notes.get("has_unblurred_insider"))
