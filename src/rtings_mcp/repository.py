"""Fetch-and-cache orchestration per surface (SPEC §8).

Everything above this layer works with parsed objects and never touches the network or the
filesystem. Everything below it is transport, files and locks. This is where the two meet,
so it is also where the coverage rules live:

* the response **is** the coverage record, and the file path **is** the key;
* coverage carries a **time** dimension — a slice fetched on day 1 covers the products that
  existed on day 1, and a product outside that catalog generation is ``coverage_stale``,
  never ``not_tested``;
* the generation's ``product_ids`` are embedded in the slice envelope, because a bare
  generation id dangles once the 3-day catalog TTL overwrites it.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import unescape
from typing import Any

from . import api, errors
from .auth import AuthManager, envelope_notes_for, verdicts_contradict_tier
from .cache import (
    ANONYMOUS,
    OUTCOME_GRAPH_NOT_AVAILABLE,
    OUTCOME_OK,
    Cache,
    Envelope,
    slug_to_key,
    tier_rank,
    validate_id,
    validate_silo,
    validate_slug,
)
from .config import (
    BASE_URL,
    SILO_HINT_SET,
    TTL_BENCH,
    TTL_CATALOG,
    TTL_GRAPH_NEGATIVE,
    TTL_GRAPH_POSITIVE,
    TTL_RECS,
    TTL_SCHEMA,
    TTL_SILOS,
    Config,
)
from .errors import RtingsError
from .fsutil import file_lock
from .htmlprobe import (
    SessionProbe,
    extract_bench_list,
    extract_data_props,
    extract_globals,
    extract_silos,
    page_title,
)
from .http import SingleFlight, Transport
from .normalize import strip_html
from .observations import ObservationStore
from .schema import (
    SiloSchema,
    cacheable_is_current,
    parse_column_options,
    schema_from_cacheable,
    schema_to_cacheable,
)

log = logging.getLogger(__name__)

_REC_LINK_RE = re.compile(r'href="(/([a-z0-9-]+)/reviews/best/([a-z0-9-]+))"')

#: Warnings are scoped to ONE tool call, not to the process.
#:
#: The repository is built once and shared by every call, so a plain instance list would
#: accumulate forever: a catalog warning raised on the first `rt_ratings` would still be
#: attached to an unrelated `rt_search` an hour later, and the list would grow without
#: bound. A ContextVar also keeps concurrent calls from bleeding into each other — tool
#: bodies interleave even at `RTINGS_CONCURRENCY=1`, which only bounds in-flight requests.
_WARNINGS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "rtings_warnings", default=None
)

#: Slices fetched during THIS call that the anonymous-label guard refused to write
#: (:meth:`AuthManager.anonymous_write_refusal`). A refused write must still be served —
#: the rows are real and the caller asked for them — so they are held here, keyed exactly
#: as the cache would key them, and the read path consults this before the disk. Scoped to
#: one call for the same reason warnings are: the repository is process-wide, and an
#: instance dict would serve one call's member-only rows to the next call as a "hit".
_UNCACHED: contextvars.ContextVar[dict[tuple[str, str, str], Envelope] | None] = (
    contextvars.ContextVar("rtings_uncached_slices", default=None)
)


@dataclass(slots=True)
class Slice:
    """One cached ``(bench, test)`` or ``(bench, usage)`` slice, ready to read from."""

    envelope: Envelope
    rows: list[dict[str, Any]]

    @property
    def product_ids(self) -> set[str]:
        return set(self.envelope.product_ids or ())

    @property
    def unpublished_ids(self) -> set[str]:
        return set(self.envelope.unpublished_product_ids or ())


#: Stand-in `original_id` for a best-of page's featured rows, which carry an inline test
#: stub with no `original_id` (verified 2026-09-03) but do carry `insider_only`.
FEATURED_ID = "featured"


def _recommendation_insider_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The gate-relevant rows of a best-of page, in the shape the tier predicates read.

    Each pick's ``featured_test_results`` has its own ``status``/``unblurred``; only the
    ``insider_only`` ones say anything about the session.
    """
    rows: list[dict[str, Any]] = []
    for pick in payload.get("product_recommendations") or []:
        if not isinstance(pick, dict):
            continue
        for row in pick.get("featured_test_results") or []:
            if not isinstance(row, dict):
                continue
            stub = row.get("test") if isinstance(row.get("test"), dict) else {}
            if not stub.get("insider_only"):
                continue
            rows.append(
                {
                    "original_id": FEATURED_ID,
                    "product_id": str(pick.get("product_id") or ""),
                    "status": row.get("status"),
                    "unblurred": bool(row.get("unblurred")),
                }
            )
    return rows


@dataclass(slots=True, frozen=True)
class ProductRef:
    """A resolved product: the numeric id is the key, the URL is only how we fetch it."""

    product_id: str
    url_path: str
    silo: str
    name: str | None = None
    bench_id: str | None = None
    published: bool | None = None


@dataclass(slots=True)
class MergedRow:
    """A row selected by the per-row merge across tier variants."""

    row: dict[str, Any]
    envelope: Envelope
    superseded_at: float | None = None


@dataclass(slots=True)
class BenchInfo:
    """The page-embedded bench list. Bench *ids* come from here, *definitions* from the
    schema — the two lists differ in length (TV: 18 on the page, 14 in the schema)."""

    silo: str
    latest_id: str | None
    benches: list[dict[str, Any]]
    fetched_at: float

    @property
    def recent_ids(self) -> list[str]:
        """The set the site itself renders together. **Derived from ``is_recent``, never
        hardcoded** — ``[197,210,227]`` is the TV answer, not the rule."""
        ids = [str(b["id"]) for b in self.benches if b.get("is_recent")]
        if ids:
            return ids
        return [self.latest_id] if self.latest_id else []

    @property
    def all_ids(self) -> list[str]:
        return [str(b["id"]) for b in self.benches if b.get("id")]

    def current_id(self, schema: SiloSchema | None = None) -> str | None:
        """The newest bench the site actually renders, preferring one with a schema.

        **``latest_test_bench_id`` is not always usable.** Measured 2026-09-03: on 5 of 28
        silos it names a bench that is absent from ``column_options`` *and* absent from the
        ``is_recent`` set — air-conditioner 258 (recent: 39), air-fryer 265 (231, 201),
        laptop 285 (242, 198, 194), router 269 (253, ...), toaster-oven 266 (247, 148).
        Those look like benches under development.

        Taking it at face value costs real correctness: nothing ever matches it, so every
        slice falls to the 30-day legacy TTL instead of the 7-day current-bench one — which
        is the named mitigation for the coverage hole that has no other signal.
        """
        # `recent_ids` preserves the page's own order, which is newest-first in every
        # measured silo (tv 227,210,197; headphones 252,244,230,183; mouse 233,199). That
        # ordering is an observation, not a documented guarantee — but it is the same one
        # `recent_ids` already rests on, so this adds no new assumption.
        ordered: list[str] = []
        if self.latest_id:
            ordered.append(self.latest_id)
        ordered.extend(b for b in self.recent_ids if b != self.latest_id)
        if schema is not None:
            for bench_id in ordered:
                if schema.bench(bench_id) is not None:
                    return bench_id
        return ordered[0] if ordered else None


@dataclass(slots=True)
class CatalogGeneration:
    """An immutable catalog snapshot for one bench."""

    silo: str
    bench_id: str
    fetched_at: float
    products: list[dict[str, Any]]

    @property
    def generation_id(self) -> str:
        return f"{self.bench_id}:{int(self.fetched_at * 1000)}"

    @property
    def product_ids(self) -> list[str]:
        return [str(p["id"]) for p in self.products if p.get("id") is not None]

    @property
    def unpublished_ids(self) -> list[str]:
        return [
            str(p["id"])
            for p in self.products
            if p.get("id") is not None and p.get("published") is False
        ]

    def index(self) -> dict[str, dict[str, Any]]:
        return {str(p["id"]): p for p in self.products if p.get("id") is not None}


class Repository:
    def __init__(
        self,
        config: Config,
        cache: Cache,
        transport: Transport,
        auth: AuthManager,
    ) -> None:
        self.config = config
        self.cache = cache
        self.transport = transport
        self.auth = auth
        self._flight = SingleFlight()
        self._schema_memo: dict[str, SiloSchema] = {}
        self._schema_meta: dict[str, tuple[float, bool]] = {}
        self._previews_spent = 0
        # The per-product file lock cannot bound a per-PROCESS budget: two calls for two
        # different products take different locks, both read `_previews_spent == 0`, and both
        # spend. The reservation below is what actually enforces RTINGS_MAX_PREVIEW_SPEND.
        self._preview_lock = asyncio.Lock()

    @contextlib.contextmanager
    def warning_scope(self):
        """Collect warnings for the duration of one tool call.

        **Re-entrant.** The server opens a scope around the whole call (so the error path
        can report the warnings that explain the failure) and each service opens one too
        (so a direct call still collects). A nested scope that reset the list would hide the
        outer one and lose everything on exit, so an inner scope is a no-op.
        """
        if _WARNINGS.get() is not None:
            yield
            return
        token = _WARNINGS.set([])
        uncached_token = _UNCACHED.set({})
        try:
            yield
        finally:
            _UNCACHED.reset(uncached_token)
            _WARNINGS.reset(token)

    @property
    def warnings(self) -> list[str]:
        return list(_WARNINGS.get() or ())

    def warn(self, message: str) -> None:
        collected = _WARNINGS.get()
        if collected is None:
            # Outside a tool call (a CLI command, a test): log rather than drop it.
            log.warning("%s", message)
            return
        if message not in collected:
            collected.append(message)

    # -- refused writes, held for this call only -----------------------------------

    @staticmethod
    def _uncached_slice(directory: str, bench_id: str, key: str) -> Envelope | None:
        held = _UNCACHED.get()
        if not held:
            return None
        return held.get((directory, bench_id, key))

    def _hold_uncached(self, envelope: Envelope, directory: str, bench_id: str, key: str) -> None:
        """Serve a refused write for the rest of this call without writing it.

        Outside a call scope (the CLI, a bare repository call in a test) there is nowhere to
        hold it: the rows are dropped and the read path reports ``coverage_unknown`` for
        them, which is honest — nothing was cached and nothing was served.
        """
        held = _UNCACHED.get()
        if held is None:
            return
        held[(directory, bench_id, key)] = envelope

    CAUSE_MEMBER_MODE_OFF = "member_mode_off"
    CAUSE_DEMOTED = "demoted"

    def _label_cause(self, *, demoted: bool, probe: SessionProbe | None) -> str:
        """Why a write is about to carry the ``anonymous`` label.

        Three distinct reasons, and the warning must name the real one: the flag is off
        (the remedy is to turn it on), the response contradicted the probe and was demoted
        (the remedy is to check the session — nothing to enable), or the probe itself did
        not justify a higher tier (``free`` on the table path, ``expired``, ``unknown``).
        The first wording used to be hardcoded, so with the flag on it told the user to
        enable a flag that was already enabled.
        """
        if not self.config.member_mode:
            return self.CAUSE_MEMBER_MODE_OFF
        if demoted:
            return self.CAUSE_DEMOTED
        return f"probe:{probe.session if probe else 'none'}"

    def _refuse_uncached_warning(
        self, silo: str, directory: str, reason: str, count: int, *, cause: str
    ) -> None:
        what = f"{count} {directory} slice(s)" if count != 1 else f"a {directory} slice"
        if reason == "early_access_unblurred":
            why = (
                "it holds an Early Access (published:false) row that came through unblurred, "
                "which only a signed-in session sees on any category"
            )
        else:
            why = (
                f"it holds unblurred insider-only rows and no signed-out fetch has shown "
                f"{silo} to serve them anonymously, so they may be member-only"
            )
        if cause == self.CAUSE_MEMBER_MODE_OFF:
            label = "because RTINGS_MEMBER_MODE is off"
            remedy = (
                "Enable RTINGS_MEMBER_MODE to cache member data under its own tier, or "
                "fetch this category once signed out so the server can record what "
                "anonymous gets."
            )
        elif cause == self.CAUSE_DEMOTED:
            label = (
                "because the response contradicted the session probe — its gate-able rows "
                "came back withheld, so the write was demoted"
            )
            remedy = (
                "Check rt_auth_status — the session may have lapsed mid-fetch — or fetch "
                "this category once signed out so the server can record what anonymous gets."
            )
        else:
            session = cause.partition(":")[2] or "unknown"
            label = (
                f"because the session probe read {session!r}, which does not justify a tier "
                "above anonymous on this surface"
            )
            remedy = (
                "Check rt_auth_status, or fetch this category once signed out so the server "
                "can record what anonymous gets."
            )
        self.warn(
            f"not_cached: {what} for {silo} came back on a signed-in session and would have "
            f"been cached under the `anonymous` label {label} — a label it cannot honestly "
            f"carry, since {why}. The rows ARE in this response; they were not written to "
            f"the cache and the next call will fetch them again. {remedy}"
        )

    # -- pages ----------------------------------------------------------------------

    async def _page_globals(self, path: str) -> tuple[dict[str, Any], str]:
        result = await self.transport.api_get_html(path)
        return extract_globals(result.text), result.url

    # -- silos ----------------------------------------------------------------------

    async def silos(self, *, refresh: bool = False) -> tuple[list[dict[str, Any]], Envelope]:
        """All 28 silos in one fetch. No crawl, no A-Z page."""
        cached = self.cache.get("silos.json")
        if cached is not None and not refresh and not cached.is_stale(TTL_SILOS):
            return list(cached.payload or []), cached

        async def do_fetch() -> tuple[list[dict[str, Any]], Envelope]:
            async with file_lock(self.cache.root / "locks" / "silos.lock"):
                again = self.cache.get("silos.json")
                if again is not None and not refresh and not again.is_stale(TTL_SILOS):
                    return list(again.payload or []), again
                try:
                    globals_obj, url = await self._page_globals("/")
                    raw = extract_silos(globals_obj)
                except RtingsError:
                    if cached is not None:
                        self.warn("silo list refresh failed; serving the cached list")
                        return list(cached.payload or []), cached
                    raise
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=url,
                    cache_tier=ANONYMOUS,
                    request={"page": "/"},
                    payload=raw,
                )
                self.cache.put(envelope, "silos.json")
                return raw, envelope

        return await self._flight.run("silos", do_fetch)

    async def silo_index(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        rows, _ = await self.silos(refresh=refresh)
        return {str(s["url_part"]).lower(): s for s in rows if s.get("url_part")}

    async def resolve_silo(self, silo: str) -> dict[str, Any]:
        """Validate **server-side against the live list only**.

        The 28 ``url_part``s ship as a ``description``/``examples`` hint on the tool
        parameter, never a JSON-Schema ``enum``: an ``enum`` is enforced client-side, so a
        silo RTINGS adds mid-release would be unreachable until a new release ships, and it
        establishes a second allowlist that can disagree with the live list.
        """
        key = validate_silo(silo)
        index = await self.silo_index()
        found = index.get(key)
        if found is None:
            index = await self.silo_index(refresh=True)
            found = index.get(key)
        if found is None:
            raise RtingsError(
                errors.UNKNOWN_SILO,
                f"{silo!r} is not one of RTINGS' silos",
                details={"known": sorted(index)},
            )
        if key not in SILO_HINT_SET:
            # Promised by SPEC §7 and CLAUDE.md and previously never emitted. The silo is
            # real — it is in the live list — so this is not an error; it says the tool
            # description shipped in this release is out of date, which is the signal that
            # RTINGS added a category.
            self.warn(
                f"silo_hint_drift: {key!r} is served by RTINGS but is not in this release's "
                "silo hint; the tool description is stale, the data is fine"
            )
        return found

    # -- benches --------------------------------------------------------------------

    async def bench_info(self, silo: str, *, refresh: bool = False) -> BenchInfo:
        key = validate_silo(silo)
        cached = self.cache.get("bench", f"{key}.json")
        if cached is not None and not refresh and not cached.is_stale(TTL_BENCH):
            return BenchInfo(
                silo=key,
                latest_id=(cached.payload or {}).get("latest_test_bench_id"),
                benches=(cached.payload or {}).get("test_benches") or [],
                fetched_at=cached.fetched_at,
            )

        async def do_fetch() -> BenchInfo:
            async with file_lock(self.cache.root / "locks" / f"bench-{key}.lock"):
                again = self.cache.get("bench", f"{key}.json")
                if again is not None and not refresh and not again.is_stale(TTL_BENCH):
                    return BenchInfo(
                        silo=key,
                        latest_id=(again.payload or {}).get("latest_test_bench_id"),
                        benches=(again.payload or {}).get("test_benches") or [],
                        fetched_at=again.fetched_at,
                    )
                globals_obj, url = await self._page_globals(f"/{key}/tools/table")
                bench_list = extract_bench_list(globals_obj)
                if bench_list is None:
                    if cached is not None:
                        self.warn(
                            f"bench list missing from the {key} page; serving the cached set"
                        )
                        return BenchInfo(
                            silo=key,
                            latest_id=(cached.payload or {}).get("latest_test_bench_id"),
                            benches=(cached.payload or {}).get("test_benches") or [],
                            fetched_at=cached.fetched_at,
                        )
                    raise RtingsError(
                        errors.PAYLOAD_MISSING,
                        f"no is_recent bench list on the {key} table page",
                    )
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=url,
                    cache_tier=ANONYMOUS,
                    request={"page": f"/{key}/tools/table"},
                    payload=bench_list,
                    silo=key,
                )
                self.cache.put(envelope, "bench", f"{key}.json")
                return BenchInfo(
                    silo=key,
                    latest_id=bench_list.get("latest_test_bench_id"),
                    benches=bench_list.get("test_benches") or [],
                    fetched_at=envelope.fetched_at,
                )

        return await self._flight.run(f"bench:{key}", do_fetch)

    async def resolve_benches(
        self, silo: str, bench_ids: Sequence[str] | None
    ) -> tuple[list[str], BenchInfo]:
        info = await self.bench_info(silo)
        if not bench_ids:
            return info.recent_ids, info
        known = set(info.all_ids)
        resolved: list[str] = []
        for raw in bench_ids:
            bench_id = validate_id(raw, what="bench_id")
            if bench_id not in known:
                raise RtingsError(
                    errors.INVALID_BENCH,
                    f"bench {bench_id} is not one of {silo}'s benches",
                    details={"known": sorted(known)},
                )
            resolved.append(bench_id)
        return resolved, info

    # -- schema ---------------------------------------------------------------------

    async def schema(self, silo: str, *, refresh: bool = False) -> SiloSchema:
        key = validate_silo(silo)
        if not refresh and key in self._schema_memo:
            return self._schema_memo[key]
        cached = self.cache.get("schema", f"{key}.json")
        # An older cacheable form is a miss, whatever its age: it lacks fields the
        # normalizer now reads, and serving it mislabels units for the rest of the TTL.
        if cached is not None and not cacheable_is_current(cached.payload):
            cached = None
        if cached is not None and not refresh and not cached.is_stale(TTL_SCHEMA):
            return self._remember_schema(key, cached, schema_from_cacheable(cached.payload or {}))

        async def do_fetch() -> SiloSchema:
            async with file_lock(self.cache.root / "locks" / f"schema-{key}.lock"):
                again = self.cache.get("schema", f"{key}.json")
                if again is not None and not cacheable_is_current(again.payload):
                    again = None
                if again is not None and not refresh and not again.is_stale(TTL_SCHEMA):
                    return self._remember_schema(
                        key, again, schema_from_cacheable(again.payload or {})
                    )
                try:
                    raw = await api.column_options(self.transport, key)
                except RtingsError:
                    if cached is not None:
                        self.warn(f"refresh_failed: serving a past-TTL {key} schema")
                        return self._remember_schema(
                            key, cached, schema_from_cacheable(cached.payload or {})
                        )
                    raise
                parsed = parse_column_options(key, raw)
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=f"{BASE_URL}/{key}/tools/table",
                    cache_tier=ANONYMOUS,
                    request={"query": api.Q_COLUMN_OPTIONS, "silo": key},
                    # The parse is stored, not the 357 KB body: nothing needs the raw body
                    # again once the definitions are indexed.
                    payload=schema_to_cacheable(parsed),
                    silo=key,
                )
                self.cache.put(envelope, "schema", f"{key}.json")
                return self._remember_schema(key, envelope, parsed)

        return await self._flight.run(f"schema:{key}", do_fetch)

    def _remember_schema(
        self, silo: str, slice_env: Envelope, parsed: SiloSchema
    ) -> SiloSchema:
        self._schema_memo[silo] = parsed
        self._schema_meta[silo] = (slice_env.fetched_at, slice_env.is_stale(TTL_SCHEMA))
        return parsed

    def schema_meta(self, silo: str) -> tuple[float | None, bool]:
        """``(fetched_at, stale)`` for the schema currently in hand.

        Without this the schema envelope reports ``fetched_at: now, from_cache: false`` for a
        29-day-old cached file, which is exactly the kind of quiet dishonesty the envelope
        exists to prevent.
        """
        return self._schema_meta.get(silo, (None, False))

    # -- catalog --------------------------------------------------------------------

    async def catalog(
        self, silo: str, bench_ids: Sequence[str], *, refresh: bool = False
    ) -> dict[str, CatalogGeneration]:
        """Newest generation per bench, fetching only the benches that are missing or stale.

        Partitioned **by bench, not by silo**: each product sits on exactly one bench, and a
        single ``catalog/{silo}.json`` could not represent a caller who widened past the
        recent set without either overwriting the recent-set catalog or losing the wider one.
        """
        key = validate_silo(silo)
        wanted = [validate_id(b, what="bench_id") for b in bench_ids]
        out: dict[str, CatalogGeneration] = {}
        missing: list[str] = []
        for bench_id in wanted:
            envelope = self.cache.newest_generation("catalog", key, key=bench_id)
            if envelope is not None and not refresh and not envelope.is_stale(TTL_CATALOG):
                out[bench_id] = CatalogGeneration(
                    silo=key,
                    bench_id=bench_id,
                    fetched_at=envelope.fetched_at,
                    products=list(envelope.payload or []),
                )
            else:
                missing.append(bench_id)
        if not missing:
            return out

        lock_key = _request_hash(key, missing, ["catalog"])

        async def do_fetch() -> dict[str, CatalogGeneration]:
            async with file_lock(self.cache.root / "locks" / f"catalog-{key}-{lock_key}.lock"):
                fresh: dict[str, CatalogGeneration] = {}
                still: list[str] = []
                for bench_id in missing:
                    envelope = self.cache.newest_generation("catalog", key, key=bench_id)
                    if envelope is not None and not refresh and not envelope.is_stale(TTL_CATALOG):
                        fresh[bench_id] = CatalogGeneration(
                            silo=key,
                            bench_id=bench_id,
                            fetched_at=envelope.fetched_at,
                            products=list(envelope.payload or []),
                        )
                    else:
                        still.append(bench_id)
                if still:
                    try:
                        products = await api.products_list(self.transport, key, still)
                    except RtingsError:
                        dropped: list[str] = []
                        for bench_id in still:
                            stale_gen = self.cache.newest_generation(
                                "catalog", key, key=bench_id
                            )
                            if stale_gen is None:
                                dropped.append(bench_id)
                                continue
                            fresh[bench_id] = CatalogGeneration(
                                silo=key,
                                bench_id=bench_id,
                                fetched_at=stale_gen.fetched_at,
                                products=list(stale_gen.payload or []),
                            )
                        if not fresh:
                            raise
                        if dropped:
                            # Naming them matters: without this the response looks complete
                            # — the bench is still listed in `test_benches` and its group
                            # comes back `matched: 0` — while every one of its products is
                            # silently missing. Same class of failure as dropping a row.
                            self.warn(
                                f"refresh_failed: no catalog available for {key} bench(es) "
                                f"{', '.join(sorted(dropped))}; their products are NOT in "
                                "this response"
                            )
                        else:
                            self.warn(
                                f"refresh_failed: serving a past-TTL {key} catalog generation"
                            )
                        return fresh
                    fetched_at = time.time()
                    by_bench: dict[str, list[dict[str, Any]]] = {b: [] for b in still}
                    for product in products:
                        bench_id = _product_bench(product)
                        if bench_id is None:
                            continue
                        by_bench.setdefault(bench_id, []).append(product)
                    for bench_id, rows in by_bench.items():
                        if bench_id not in still:
                            continue  # a bench we did not request; ignore rather than file
                        envelope = Envelope(
                            fetched_at=fetched_at,
                            source_url=f"{BASE_URL}/{key}/tools/table",
                            cache_tier=ANONYMOUS,
                            request={
                                "query": api.Q_PRODUCTS_LIST,
                                "silo": key,
                                "test_bench_ids": still,
                            },
                            payload=rows,
                            bench_id=bench_id,
                            silo=key,
                        )
                        self.cache.put_generation(envelope, "catalog", key, key=bench_id)
                        self.cache.prune_generations("catalog", key, key=bench_id)
                        fresh[bench_id] = CatalogGeneration(
                            silo=key,
                            bench_id=bench_id,
                            fetched_at=fetched_at,
                            products=rows,
                        )
                return fresh

        out.update(await self._flight.run(f"catalog:{key}:{lock_key}", do_fetch))
        return out

    # -- test results ---------------------------------------------------------------

    def _slice_is_fresh(
        self, slice_env: Envelope, *, current_bench_id: str | None, bench_id: str
    ) -> bool:
        """Current-bench slices get the shorter TTL — the named mitigation for the open
        coverage-staleness hole, since new products land on the current bench.

        ``current_bench_id`` is passed in from the page-embedded ``latest_test_bench_id``,
        never inferred from ``column_options.test_benches[]`` list order: that ordering has
        never been measured, and the schema's ``test_bench`` object carries no id of its own.
        """
        is_current = current_bench_id is not None and current_bench_id == bench_id
        return not slice_env.is_stale(self.config.ttl_results(is_current_bench=is_current))

    async def ensure_test_slices(
        self,
        silo: str,
        bench_ids: Sequence[str],
        test_ids: Sequence[str],
        *,
        refresh: bool = False,
    ) -> None:
        await self._ensure_slices(
            silo,
            bench_ids,
            test_ids,
            surface="tests",
            directory="tests",
            fetcher=api.test_results,
            refresh=refresh,
        )

    async def ensure_rating_slices(
        self,
        silo: str,
        bench_ids: Sequence[str],
        usage_ids: Sequence[str],
        *,
        refresh: bool = False,
    ) -> None:
        await self._ensure_slices(
            silo,
            bench_ids,
            usage_ids,
            surface="ratings",
            directory="ratings",
            fetcher=api.ratings,
            refresh=refresh,
        )

    async def _ensure_slices(
        self,
        silo: str,
        bench_ids: Sequence[str],
        ids: Sequence[str],
        *,
        surface: str,
        directory: str,
        fetcher: Any,
        refresh: bool,
    ) -> None:
        key = validate_silo(silo)
        benches = [validate_id(b, what="bench_id") for b in bench_ids]
        wanted = [validate_id(i, what="original_id") for i in ids]
        if not benches or not wanted:
            return

        probe = self.auth.cached_probe()
        demand = self.auth.demand_tier(surface, probe)

        info = await self.bench_info(key)
        current_bench_id = info.current_id(await self.schema(key))
        missing = self._missing_pairs(
            benches,
            wanted,
            directory=directory,
            demand=demand,
            refresh=refresh,
            current_bench_id=current_bench_id,
        )
        if not missing:
            return

        # One request writes many files, so the lock is at the REQUEST grain — taking one
        # lock per written file would contradict "never hold two locks".
        for chunk in _chunks(sorted(missing), api.MAX_TESTS_PER_REQUEST):
            lock_key = _request_hash(key, benches, chunk)
            await self._flight.run(
                f"{directory}:{key}:{lock_key}",
                lambda c=chunk, lk=lock_key: self._fetch_slice_chunk(
                    key,
                    benches,
                    c,
                    surface=surface,
                    directory=directory,
                    fetcher=fetcher,
                    lock_key=lk,
                    refresh=refresh,
                    current_bench_id=current_bench_id,
                ),
            )

    def _any_pair_has_some_file(
        self, directory: str, benches: Sequence[str], ids: Sequence[str]
    ) -> bool:
        """True when *any* requested ``(bench, id)`` has a file on disk, however old.

        **Any, not every.** One chunk mixes up to 60 ids, so a single never-fetched test
        alongside 59 cached-but-stale ones is routine — and requiring all of them would fail
        the whole chunk on a transient 503 when 59 of the answers were sitting on disk. The
        uncached pairs are not lost either: with no slice to read, they normalize to
        ``coverage_unknown``, which is the honest answer for them.
        """
        for original_id in ids:
            for bench_id in benches:
                if self.cache.list_variants(directory, bench_id, key=original_id):
                    return True
        return False

    def slice_is_stale(self, slice_env: Envelope, *, current_bench_id: str | None) -> bool:
        return not self._slice_is_fresh(
            slice_env,
            current_bench_id=current_bench_id,
            bench_id=slice_env.bench_id or "",
        )

    def _missing_pairs(
        self,
        benches: Sequence[str],
        ids: Sequence[str],
        *,
        directory: str,
        demand: str,
        refresh: bool,
        current_bench_id: str | None,
    ) -> set[str]:
        """Ids for which at least one requested bench has no usable file.

        A hit requires every requested ``(bench, id)`` to have a file at
        ``cache_tier >= demand``, within TTL. Both sides of that comparison come from the
        probe, never from the data.
        """
        if refresh:
            return set(ids)
        missing: set[str] = set()
        for original_id in ids:
            for bench_id in benches:
                if self._uncached_slice(directory, bench_id, original_id) is not None:
                    # Fetched earlier in this same call and refused a write: served from
                    # the in-call hold, never refetched within the call.
                    continue
                variants = self.cache.list_variants(directory, bench_id, key=original_id)
                usable = False
                for variant in variants:
                    if tier_rank(variant.tier) < tier_rank(demand):
                        continue
                    slice_env = self.cache.read_path(variant.path)
                    if slice_env is None:
                        continue
                    if self._slice_is_fresh(
                        slice_env, current_bench_id=current_bench_id, bench_id=bench_id
                    ):
                        usable = True
                        break
                if not usable:
                    missing.add(original_id)
                    break
        return missing

    async def _fetch_slice_chunk(
        self,
        silo: str,
        benches: Sequence[str],
        ids: Sequence[str],
        *,
        surface: str,
        directory: str,
        fetcher: Any,
        lock_key: str,
        refresh: bool,
        current_bench_id: str | None,
    ) -> None:
        lock_path = self.cache.root / "locks" / f"{directory}-{silo}-{lock_key}.lock"
        async with file_lock(lock_path):
            probe = self.auth.cached_probe()
            demand = self.auth.demand_tier(surface, probe)
            still = self._missing_pairs(
                benches,
                ids,
                directory=directory,
                demand=demand,
                refresh=refresh,
                current_bench_id=current_bench_id,
            )
            if not still:
                return

            # The catalog is refreshed FIRST: a refetched slice filed against the same stale
            # generation repeats the miss forever.
            generations = await self.catalog(silo, benches)
            schema = await self.schema(silo)
            insider_ids = {t.original_id for t in schema.tests.values() if t.insider_only}

            try:
                rows = await fetcher(self.transport, silo, benches, sorted(still))
            except RtingsError as exc:
                # A cached row is served with error:null whenever one exists, even past TTL
                # and even when the refetch failed. `error` is non-null only when there is
                # nothing to return — so this re-raises only for pairs with no cached file
                # at all.
                if self._any_pair_has_some_file(directory, benches, still):
                    self.warn(
                        f"refresh_failed: could not refresh {silo} {directory} "
                        f"({exc.code}); serving what is cached, and anything not cached "
                        "is reported as coverage_unknown rather than not_tested"
                    )
                    return
                raise
            fetched_at = time.time()

            # The tier is resolved ONCE, here, for every file this response produces —
            # the catalogued buckets and the `_unassigned` ones alike. It used to be
            # computed after the unassigned block, which therefore hardcoded `anonymous`
            # and, with member mode on, was refused by the label guard on every call while
            # the catalogued buckets from the same response were written `member` and hit.
            #
            # Placement matters: the re-probe is unthrottled and must run AFTER the fetch
            # (a lapse during the fetch is what write-time demotion catches) and BEFORE any
            # tier-keyed write. Both hold here, exactly as they did lower down; the probe
            # lock it takes is a leaf, so holding the surface key lock across it is the
            # ordering already in force.
            write_tier = self.auth.write_tier(surface, probe)
            if write_tier != ANONYMOUS:
                # A successful probe is a precondition for any tier-keyed write, and this
                # probe is NEVER throttled: the read-path throttle exists so a cache hit is
                # cheap, not so a write can be labelled from a stale answer.
                probe = await self.auth.session_probe(force=True)
                write_tier = self.auth.write_tier(surface, probe)
            may_unblur = self.auth.session_may_unblur(probe)
            observations = ObservationStore(self.cache)
            refused: dict[str, tuple[int, str]] = {}

            def refuse(reason: str, cause: str) -> None:
                count, _ = refused.get(reason, (0, cause))
                refused[reason] = (count + 1, cause)

            product_bench: dict[str, str] = {}
            for bench_id, generation in generations.items():
                for product_id in generation.product_ids:
                    product_bench[product_id] = bench_id

            # Partition: (bench, id) -> rows. Every requested pair gets a file, including
            # the empty ones — an empty payload is real `not_tested` for every product on
            # that bench, and a reader must never read empty as a miss.
            buckets: dict[tuple[str, str], list[dict[str, Any]]] = {
                (bench_id, original_id): []
                for bench_id in benches
                for original_id in sorted(still)
            }
            unassigned: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                original_id = str(row.get("original_id") or "")
                if original_id not in still:
                    continue
                product_id = str(row.get("product_id") or "")
                bench_id = product_bench.get(product_id)
                if bench_id is None:
                    # Structural, not evidence the catalog is behind (`RECON.md` §12.2 —
                    # see the warning below). Never silently dropped: a dropped row
                    # becomes a false `not_tested` later.
                    unassigned.setdefault(original_id, []).append(row)
                    continue
                buckets.setdefault((bench_id, original_id), []).append(row)

            if unassigned:
                # Measured 2026-09-03: `test_results` has a WIDER product population than
                # `products_list` — 9 TV product ids returned rows while appearing in no
                # catalog across all 18 benches, every row blurred. So an unassigned row is
                # a structural property of the API, not evidence the catalog is stale. It
                # is still filed rather than dropped: a dropped row becomes a false
                # `not_tested` if the catalog later catches up.
                orphans = {
                    str(r.get("product_id")) for rows_ in unassigned.values() for r in rows_
                }
                # Not a warning: the response's `coverage: uncatalogued` group already says
                # this, scoped to what survived the caller's filters. As a warning it fired
                # on a 3-product `product_ids` request about 19 products it did not return.
                log.debug(
                    "%d %s product(s) returned %s rows but are absent from the listing",
                    len(orphans),
                    silo,
                    directory,
                )
                for original_id, rows_for_id in unassigned.items():
                    # Same response, same tier, same demotion rule as the catalogued
                    # buckets. An uncatalogued product is in no generation, so nothing can
                    # mark it Early Access; the empty set is the honest input.
                    tier = write_tier
                    demoted = self.auth.should_demote(
                        tier=tier,
                        surface=surface,
                        rows=rows_for_id,
                        insider_ids=insider_ids,
                        unpublished_product_ids=set(),
                        probe=probe,
                    )
                    if demoted:
                        tier = ANONYMOUS
                    envelope = Envelope(
                        fetched_at=fetched_at,
                        source_url=f"{BASE_URL}/{silo}/tools/table",
                        cache_tier=tier,
                        request={"silo": silo, "test_bench_ids": list(benches)},
                        payload={"rows": rows_for_id},
                        silo=silo,
                        notes={"unassigned": True},
                    )
                    # The label guard applies whenever the label is `anonymous`. An
                    # uncatalogued product sits on no known bench, so the proof has to hold
                    # for every bench the request spanned.
                    reason = (
                        self.auth.anonymous_write_refusal(
                            surface=surface,
                            rows=rows_for_id,
                            insider_ids=insider_ids,
                            unpublished_product_ids=set(),
                            probe=probe,
                            anonymous_serves=all(
                                observations.anonymous_serves(silo, b, surface=surface)
                                for b in benches
                            ),
                        )
                        if tier == ANONYMOUS and may_unblur
                        else None
                    )
                    if reason is not None:
                        refuse(reason, self._label_cause(demoted=demoted, probe=probe))
                        self._hold_uncached(envelope, directory, "_unassigned", original_id)
                        continue
                    self.cache.put_variant(
                        envelope, directory, "_unassigned", key=original_id
                    )

            for (bench_id, original_id), bucket in buckets.items():
                generation = generations.get(bench_id)
                if generation is None:
                    continue
                unpublished = set(generation.unpublished_ids)
                tier = write_tier
                demoted = self.auth.should_demote(
                    tier=tier,
                    surface=surface,
                    rows=bucket,
                    insider_ids=insider_ids,
                    unpublished_product_ids=unpublished,
                    probe=probe,
                )
                if demoted:
                    tier = ANONYMOUS
                # The anonymous-label guard. `tier` is `anonymous` here because member mode
                # is off, because the probe justified nothing higher, or because the
                # response contradicted the probe; in each case a signed-in session may
                # have unblurred rows a signed-out one never sees, and an `anonymous` file
                # holding them is served to the next signed-out caller as a hit. Refuse the
                # write, keep the rows for this call, and say why. Anonymous sessions never
                # reach the predicate (`may_unblur` is false by construction), so nothing
                # changes for them — no probe, no observation read, no warning.
                reason = (
                    self.auth.anonymous_write_refusal(
                        surface=surface,
                        rows=bucket,
                        insider_ids=insider_ids,
                        unpublished_product_ids=unpublished,
                        probe=probe,
                        anonymous_serves=observations.anonymous_serves(
                            silo, bench_id, surface=surface
                        ),
                    )
                    if tier == ANONYMOUS and may_unblur
                    else None
                )
                envelope = Envelope(
                    fetched_at=fetched_at,
                    source_url=f"{BASE_URL}/{silo}/tools/table",
                    cache_tier=tier,
                    request={
                        "silo": silo,
                        "test_bench_ids": list(benches),
                        "original_ids": sorted(still),
                    },
                    payload={"rows": bucket},
                    outcome=OUTCOME_OK,
                    catalog_generation=generation.generation_id,
                    product_ids=generation.product_ids,
                    unpublished_product_ids=generation.unpublished_ids,
                    bench_id=bench_id,
                    silo=silo,
                    notes=envelope_notes_for(bucket, insider_ids, surface=surface),
                )
                if reason is not None:
                    refuse(reason, self._label_cause(demoted=demoted, probe=probe))
                    self._hold_uncached(envelope, directory, bench_id, original_id)
                    continue
                self.cache.put_variant(envelope, directory, bench_id, key=original_id)
                self.cache.prune_variants(directory, bench_id, key=original_id)
            for reason, (count, cause) in refused.items():
                self._refuse_uncached_warning(silo, directory, reason, count, cause=cause)
            self.cache.flush_lru()

    def read_slice(
        self, directory: str, bench_id: str, original_id: str, *, demand: str
    ) -> dict[str, MergedRow] | None:
        """Merge across **every** tier variant, per row.

        A gift link or metered preview unblurs one product inside an otherwise-blurred
        response (1 of 97 observed), so no whole-file label is correct: take the row with
        ``unblurred:true`` from any variant (freshest among those), else the row from the
        freshest variant that has one. Never-downgrade lives on the row's ``unblurred``
        bit, where it was always correct.

        ``demand`` deliberately does **not** filter this merge — that is the hit rule's job
        (``_missing_pairs``). Filtering here would drop a retained lower-tier row holding
        the only unblurred value for a product the higher-tier response did not cover. The
        tier is provenance; the row's own ``unblurred`` bit is the selector.
        """
        variants = self.cache.read_variants(directory, bench_id, key=original_id)
        held = self._uncached_slice(directory, bench_id, original_id)
        if held is not None:
            # The refused write is the freshest observation of this pair by construction —
            # it was fetched during this call — so it leads the merge.
            variants = [held, *variants]
        if not variants:
            return None
        merged: dict[str, MergedRow] = {}
        newest_for_product: dict[str, float] = {}
        for slice_env in variants:  # newest first
            for row in (slice_env.payload or {}).get("rows", []):
                product_id = str(row.get("product_id") or "")
                if not product_id:
                    continue
                if product_id not in newest_for_product:
                    newest_for_product[product_id] = slice_env.fetched_at
                current = merged.get(product_id)
                if current is None or (
                    not current.row.get("unblurred") and row.get("unblurred")
                ):
                    merged[product_id] = MergedRow(row=row, envelope=slice_env)
        for product_id, entry in merged.items():
            # superseded_at is per row and means "a NEWER file also covers this product".
            # Stamping it from the freshest file overall would imply a fresher observation
            # of this row that may not exist.
            newest = newest_for_product.get(product_id)
            if newest is not None and entry.envelope.fetched_at < newest:
                entry.superseded_at = newest
        # An empty payload inside a written file is a real answer (not_tested for every
        # product), not a miss — so an empty mapping is returned rather than None.
        return merged

    def uncatalogued_rows(
        self, directory: str, original_ids: Sequence[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Rows filed under ``_unassigned`` — products that returned results but appear in no
        catalog generation.

        Measured 2026-09-04: this is not a rare edge. ``products_list`` returns 69 mattresses
        while ``test_results`` returns 109, and all 40 extras are ``unblurred:true`` with real
        values. **Identified 2026-09-06** by resolving four of them through the compare tool:
        they are RTINGS' internal copies and retests — "LG G5 OLED (Copy)", "Samsung QN90F
        (Copy)", "Boring Mattress - TBF 1.0.1", "Sleep On Latex Pure Green Organic - TBF
        1.0.1" — deliberately absent from the product listing, not products for sale. They
        are reported as a summary of ids rather than dropped (a dropped row is a false
        ``not_tested`` if the listing ever picks one up) and never ranked by default.

        Returns ``{product_id: {original_id: row}}``.
        """
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for original_id in original_ids:
            variants = self.cache.read_variants(directory, "_unassigned", key=original_id)
            held = self._uncached_slice(directory, "_unassigned", original_id)
            if held is not None:
                variants = [held, *variants]
            for slice_env in variants:
                for row in (slice_env.payload or {}).get("rows", []):
                    product_id = str(row.get("product_id") or "")
                    if product_id:
                        out.setdefault(product_id, {}).setdefault(original_id, row)
        return out

    def slice_meta(
        self, directory: str, bench_id: str, original_id: str, *, demand: str
    ) -> Envelope | None:
        """The freshest variant at ``cache_tier >= demand``.

        This one **does** honour ``demand``: it supplies the coverage record
        (``product_ids``, ``unpublished_product_ids``, ``fetched_at``) and
        ``scores_available``'s "freshest response", both of which must reflect the tier the
        hit rule accepted.
        """
        held = self._uncached_slice(directory, bench_id, original_id)
        if held is not None and tier_rank(held.cache_tier) >= tier_rank(demand):
            return held
        variants = self.cache.read_variants(directory, bench_id, key=original_id, min_tier=demand)
        if variants:
            return variants[0]
        if held is not None:
            return held
        fallback = self.cache.read_variants(directory, bench_id, key=original_id)
        return fallback[0] if fallback else None

    # -- graphs ---------------------------------------------------------------------

    async def graph(self, silo: str, product_id: str, test_original_id: str) -> Envelope:
        """Curve for one ``(product, test)``, fetched from the CDN **uncredentialed**.

        Negative results are files too: 397 of 402 rows on TV bench 227 have no curve, so
        without a written ``graph_not_available`` every call on those pairs would cost a POST
        forever.
        """
        key = validate_silo(silo)
        product = validate_id(product_id, what="product_id")
        test_id = validate_id(test_original_id, what="original_id")
        cached = self.cache.get("graphs", product, f"{test_id}.json")
        if cached is not None:
            ttl = (
                TTL_GRAPH_NEGATIVE
                if cached.outcome == OUTCOME_GRAPH_NOT_AVAILABLE
                else TTL_GRAPH_POSITIVE
            )
            if not cached.is_stale(ttl):
                return cached

        async def do_fetch() -> Envelope:
            async with file_lock(self.cache.root / "locks" / f"graph-{product}-{test_id}.lock"):
                url = await api.graph_data_url(self.transport, key, product, test_id)
                if not url:
                    envelope = Envelope(
                        fetched_at=time.time(),
                        source_url=f"{BASE_URL}/{key}/graph",
                        cache_tier=ANONYMOUS,
                        request={"product_id": product, "test_original_id": test_id},
                        payload=None,
                        outcome=OUTCOME_GRAPH_NOT_AVAILABLE,
                        silo=key,
                    )
                    self.cache.put(envelope, "graphs", product, f"{test_id}.json")
                    return envelope
                curve = await self.transport.cdn_get_json(url)
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=url if url.startswith("http") else f"https://i.rtings.com{url}",
                    cache_tier=ANONYMOUS,
                    request={"product_id": product, "test_original_id": test_id},
                    payload=curve,
                    outcome=OUTCOME_OK,
                    silo=key,
                )
                self.cache.put(envelope, "graphs", product, f"{test_id}.json")
                return envelope

        return await self._flight.run(f"graph:{product}:{test_id}", do_fetch)

    # -- reviews (the metered path) --------------------------------------------------

    async def review(
        self,
        silo: str,
        product_id: str,
        url_path: str,
        *,
        consume_preview: bool = False,
        refresh: bool = False,
    ) -> tuple[Envelope, bool]:
        """One product review, with the preview budget enforced **before** the POST.

        ``rt_product`` is ``app/product_vue_page__page_body``. It was believed to be the
        endpoint the free/preview meter counts; **measured 2026-09-07 (RECON §14) it is not**
        — the meter counts the review page's HTML GET, which this server never issues, and a
        free account has no budget on the gated silos anyway. The guard below therefore arms
        only when the probe reports a real ``access_limit``, and stays as insurance against
        RTINGS metering the API later. Spacing calls apart protects nothing; it just spends
        the user's previews more slowly. Time is the wrong axis, **count** is the right one. So:
        check the budget before spending, require an explicit opt-in for a call that would
        spend, never auto-refetch a past-TTL review, hold the cross-process lock so two
        clients cannot double-spend, and re-probe afterwards.

        Returns ``(envelope, stale)``.
        """
        key = validate_silo(silo)
        product = validate_id(product_id, what="product_id")
        demand = self.auth.demand_tier("reviews", self.auth.cached_probe())
        variants = self.cache.read_variants("reviews", key=product, min_tier=demand)
        ttl = self.config.ttl_reviews()
        if variants and not refresh:
            newest = variants[0]
            if not newest.is_stale(ttl):
                return newest, False
            if self.auth.preview_would_spend(product):
                # A past-TTL review on a free session is served stale, never silently
                # re-bought: a refetch here would spend another metered preview.
                self.warn(
                    "serving a stale cached review rather than spending another metered "
                    "preview; pass refresh=true with consume_preview=true to re-buy it"
                )
                return newest, True

        lock_path = self.cache.root / "locks" / f"review-{product}.lock"
        async with file_lock(lock_path):
            # Re-check inside the lock, and read the probe inside it too: a second process
            # reading a stale probe would let both believe one preview remains.
            variants = self.cache.read_variants("reviews", key=product, min_tier=demand)
            if variants and not refresh and not variants[0].is_stale(ttl):
                return variants[0], False

            probe = await self.auth.session_probe()
            would_spend = self.auth.preview_would_spend(product, probe)
            if would_spend:
                # Before refusing, look for a cached copy at ANY tier. Turning member mode on
                # raises the demand tier to `free`, which turns a perfectly good anonymous
                # review — all the prose, the public fields, and on an open silo every value
                # — into a miss and then a `preview_exhausted` error with `data: null`.
                # Serving it with a warning is strictly better than erroring.
                any_tier = self.cache.read_variants("reviews", key=product)
                if any_tier and not refresh:
                    self.warn(
                        "serving a cached review rather than spending a metered preview; "
                        "pass consume_preview=true with refresh=true to buy the unblurred one"
                    )
                    return any_tier[0], any_tier[0].is_stale(ttl)
                remaining = self.auth.previews_remaining(probe)
                if remaining is not None and remaining <= 0:
                    raise RtingsError(
                        errors.PREVIEW_EXHAUSTED,
                        "this free account has no metered review previews left",
                        details={"previews_remaining": 0},
                    )
                if self.config.max_preview_spend <= 0:
                    raise RtingsError(
                        errors.PREVIEW_EXHAUSTED,
                        "RTINGS_MAX_PREVIEW_SPEND is 0, so spending a preview is forbidden",
                        details={"previews_remaining": remaining},
                    )
                if not consume_preview:
                    raise RtingsError(
                        errors.PREVIEW_EXHAUSTED,
                        "fetching this review would spend one of your metered previews; "
                        "pass consume_preview=true to allow it",
                        details={"previews_remaining": remaining},
                    )
                # Claim the slot now, not after the fetch: the check and the increment must
                # not straddle an await, or two concurrent calls for different products both
                # pass a budget of 1.
                async with self._preview_lock:
                    if self._previews_spent >= self.config.max_preview_spend:
                        raise RtingsError(
                            errors.PREVIEW_EXHAUSTED,
                            "this process has reached RTINGS_MAX_PREVIEW_SPEND",
                            details={
                                "spent": self._previews_spent,
                                "limit": self.config.max_preview_spend,
                            },
                        )
                    self._previews_spent += 1

            try:
                page = await api.page_body(self.transport, url_path)
            except BaseException:
                if would_spend:
                    # The request never reached the meter, so release the reservation.
                    # (If it *did* reach it and the response was lost, the post-call re-probe
                    # below never runs and the next call's server-side check catches it.)
                    async with self._preview_lock:
                        self._previews_spent = max(0, self._previews_spent - 1)
                raise
            fetched_at = time.time()

            product_obj = page.get("product") if isinstance(page, dict) else None
            product_obj = product_obj if isinstance(product_obj, dict) else {}
            review = product_obj.get("review")
            review = review if isinstance(review, dict) else {}
            rows = review.get("test_results") or []
            bench = review.get("test_bench")
            bench_id = str(bench.get("id")) if isinstance(bench, dict) and bench.get("id") else None

            schema = await self.schema(key)
            insider_ids = {t.original_id for t in schema.tests.values() if t.insider_only}
            row_ids = [
                {
                    "original_id": (r.get("test") or {}).get("original_id"),
                    "unblurred": r.get("unblurred"),
                    "status": r.get("status"),
                    "product_id": product,
                }
                for r in rows
                if isinstance(r, dict)
            ]

            tier = self.auth.write_tier("reviews", probe)
            if tier != ANONYMOUS:
                probe = await self.auth.session_probe(force=True)
                tier = self.auth.write_tier("reviews", probe)
            unpublished: set[str] = set()
            if bench_id:
                generations = await self.catalog(key, [bench_id])
                generation = generations.get(bench_id)
                if generation is not None:
                    unpublished = set(generation.unpublished_ids)
            demoted = self.auth.should_demote(
                tier=tier,
                surface="reviews",
                rows=row_ids,
                insider_ids=insider_ids,
                unpublished_product_ids=unpublished,
                probe=probe,
                product_id=product,
            )
            if demoted:
                tier = ANONYMOUS

            envelope = Envelope(
                fetched_at=fetched_at,
                source_url=f"{BASE_URL}{url_path}",
                cache_tier=tier,
                request={"silo": key, "product_id": product, "url_path": url_path},
                payload=page,
                bench_id=bench_id,
                silo=key,
                unpublished_product_ids=sorted(unpublished & {product}),
                notes=envelope_notes_for(row_ids, insider_ids, surface="reviews"),
            )
            # The anonymous-label guard, as on the table path: a review unblurred by a
            # membership (or a free account's metered preview) must not be filed as what
            # anonymous gets. The envelope is still returned — the caller asked for it —
            # it simply is not written.
            reason = (
                self.auth.anonymous_write_refusal(
                    surface="reviews",
                    rows=row_ids,
                    insider_ids=insider_ids,
                    unpublished_product_ids=unpublished,
                    probe=probe,
                    anonymous_serves=bool(bench_id)
                    and ObservationStore(self.cache).anonymous_serves(
                        key, bench_id or "", surface="reviews"
                    ),
                )
                if tier == ANONYMOUS and self.auth.session_may_unblur(probe)
                else None
            )
            if reason is not None:
                self._refuse_uncached_warning(
                    key, "reviews", reason, 1, cause=self._label_cause(demoted=demoted, probe=probe)
                )
            else:
                self.cache.put_variant(envelope, "reviews", key=product, compress=True)
                self.cache.prune_variants("reviews", key=product)
                self.cache.flush_lru()
            if would_spend:
                # Refresh previewed_products so the next call's budget check is current.
                await self.auth.session_probe(force=True)
            return envelope, False

    async def side_by_side(
        self, silo: str, product_id: str, *, refresh: bool = False
    ) -> tuple[Envelope, bool]:
        """RTINGS' verdicts and pros/cons for one product. Returns ``(envelope, stale)``.

        Tier-keyed like ``reviews/``: the payload carries `user_has_access`, so what it
        contains can differ by session. Unlike ``reviews/`` it is not believed to be metered
        (see :func:`api.side_by_side_review`), so a past-TTL copy is refreshed normally.
        """
        key = validate_silo(silo)
        product = validate_id(product_id, what="product_id")
        demand = self.auth.demand_tier("reviews", self.auth.cached_probe())
        ttl = self.config.ttl_reviews()

        variants = self.cache.read_variants("verdicts", key=product, min_tier=demand)
        if variants and not refresh and not variants[0].is_stale(ttl):
            return variants[0], False

        async def do_fetch() -> tuple[Envelope, bool]:
            async with file_lock(self.cache.root / "locks" / f"verdicts-{product}.lock"):
                again = self.cache.read_variants("verdicts", key=product, min_tier=demand)
                if again and not refresh and not again[0].is_stale(ttl):
                    return again[0], False
                try:
                    review = await api.side_by_side_review(self.transport, key, product)
                except RtingsError:
                    if again:
                        self.warn(
                            "refresh_failed: serving cached verdicts for this product"
                        )
                        return again[0], True
                    raise
                probe = self.auth.cached_probe()
                tier = self.auth.write_tier("reviews", probe)
                demoted = False
                if tier != ANONYMOUS:
                    probe = await self.auth.session_probe(force=True)
                    tier = self.auth.write_tier("reviews", probe)
                    # This surface had no demotion at all, so a withheld payload written
                    # under a member probe was served as member data for the full 30-day
                    # TTL. See `verdicts_contradict_tier` for why the predicate is the
                    # usage scores and NOT `user_has_access`.
                    demoted = verdicts_contradict_tier(review, tier)
                    if demoted:
                        tier = ANONYMOUS
                reason: str | None = None
                if tier == ANONYMOUS and self.auth.session_may_unblur(probe):
                    # The anonymous-label guard for verdicts. The scores here are usage
                    # ratings by another name, so they are judged as a ratings surface:
                    # any non-null score under a session that may have unblurred it, on a
                    # silo no signed-out fetch has shown to serve usage scores, is
                    # member-only until proven otherwise; an Early Access product's scores
                    # are member-only on every silo.
                    bench = review.get("test_bench")
                    bench_id = (
                        str(bench.get("id"))
                        if isinstance(bench, dict) and bench.get("id")
                        else None
                    )
                    unpublished: set[str] = set()
                    if bench_id:
                        generation = (await self.catalog(key, [bench_id])).get(bench_id)
                        if generation is not None:
                            unpublished = set(generation.unpublished_ids)
                    score_rows = [
                        {"product_id": product, "unblurred": entry.get("score") is not None}
                        for entry in (review.get("product_score_sets") or [])
                        if isinstance(entry, dict)
                    ]
                    reason = self.auth.anonymous_write_refusal(
                        surface="ratings",
                        rows=score_rows,
                        insider_ids=set(),
                        unpublished_product_ids=unpublished,
                        probe=probe,
                        anonymous_serves=bool(bench_id)
                        and ObservationStore(self.cache).anonymous_serves(
                            key, bench_id or "", surface="verdicts"
                        ),
                    )
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=f"{BASE_URL}/{key}/tools/compare",
                    cache_tier=tier,
                    request={"query": api.Q_SIDE_BY_SIDE, "product_id": product},
                    payload=review,
                    silo=key,
                    notes={
                        "user_has_access": bool(review.get("user_has_access")),
                        # THE key `envelope_has_unblurred_insider` reads. Writing only
                        # `user_has_access` meant never-downgrade and the eviction exemption
                        # silently never fired for this surface, so a later gated fetch could
                        # prune away the one file holding real scores.
                        "has_unblurred_insider": bool(review.get("user_has_access")),
                    },
                )
                if reason is not None:
                    self._refuse_uncached_warning(
                        key,
                        "verdicts",
                        reason,
                        1,
                        cause=self._label_cause(demoted=demoted, probe=probe),
                    )
                    return envelope, False
                self.cache.put_variant(envelope, "verdicts", key=product, compress=True)
                self.cache.prune_variants("verdicts", key=product)
                self.cache.flush_lru()
                return envelope, False

        return await self._flight.run(f"verdicts:{product}", do_fetch)

    # -- product resolution ----------------------------------------------------------

    async def resolve_product(self, product: str, silo: str | None = None) -> ProductRef:
        """Accept a review URL, a numeric product id, or a free-text model name.

        A URL is **never** turned into a cache path: the numeric id is what keys the cache,
        and it comes from the catalog or the live search index, never from parsing the URL.
        """
        text = str(product).strip()
        if not text:
            raise RtingsError(errors.UNKNOWN_PRODUCT, "no product given")

        if "/" in text:
            path = text
            if path.startswith("http"):
                path = re.sub(r"^https?://[^/]+", "", path)
            if not path.startswith("/"):
                path = "/" + path
            path = path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            # An Early Access review's URL is `/early-access/{silo}/reviews/...`, so the
            # first segment is not the silo. The catalog stores the URL with the prefix, so
            # the lookup uses the path verbatim and only the silo is derived from the rest.
            segments = [s for s in path.split("/") if s]
            silo_part = (segments[1] if segments[0] == "early-access" else segments[0]).lower()
            found = await self._product_from_url(silo_part, path)
            if found is not None:
                return found
            # **Never fall back to search for a URL.** A search hit is ranked by relevance,
            # not identity: `/early-access/tv/reviews/lg/b6-oled-2026` matched the *2016* LG
            # B6 and returned a nine-year-old TV's blurred rows as the answer. A URL the
            # catalog does not know is `unknown_product`, not a near miss.
            raise RtingsError(
                errors.UNKNOWN_PRODUCT,
                f"no product in the {silo_part} catalog has the URL {path!r} on ANY bench "
                "(every bench with a published schema was searched, not just the recent "
                "set). Check the path, or pass the numeric product id — "
                "`test_results` carries products the catalog does not list.",
            )

        if text.isdigit():
            found = await self._product_from_id(text, silo)
            if found is not None:
                return found
            if silo:
                # `test_results` carries products the catalog does not list (RECON §12.2:
                # 9 on tv, 41 on mattress), and the uncatalogued group's notice sends the
                # caller here to identify one. The compare tool answers by bare id and its
                # `product` block carries the review URL, name, silo and bench.
                found = await self._product_from_verdicts(text, silo)
                if found is not None:
                    return found
            raise RtingsError(
                errors.UNKNOWN_PRODUCT,
                (
                    f"product id {text} is not in the {silo} catalog"
                    if silo
                    else f"product id {text} is not in any cached catalog; pass silo= or "
                    "the review URL so it can be looked up"
                ),
            )

        hit = await self._search_product(text, silo)
        if hit is not None:
            return hit
        raise RtingsError(
            errors.UNKNOWN_PRODUCT,
            f"no RTINGS review matched {text!r}"
            + (f" in {silo}" if silo else "")
            + "; try rt_search to see the candidates",
        )

    async def _product_from_url(self, silo: str, path: str) -> ProductRef | None:
        """Exact URL match, recent benches first and legacy benches second.

        Measured 2026-09-08: scanning only ``recent_ids`` made every LEGACY-bench review
        unresolvable **by URL** while the same product resolved by its numeric id — the
        Samsung TU7000 (bench 124) failed on the URL RTINGS' own catalog gives for it, and
        the error said "no product in the tv catalog has the URL", which reads as "no such
        product" for a review that is right there. A review URL is this tool's documented
        primary input, so the scan widens rather than the promise narrowing.

        Searching by *search* is still forbidden on this path (a relevance hit is not an
        identity — see the caller); this widens the EXACT-match scan only, and only after
        the cheap set has missed. tv is 3 recent benches and 11 legacy ones, so a genuine
        miss costs those catalog fetches once per TTL.
        """
        try:
            await self.resolve_silo(silo)
        except RtingsError:
            return None
        info = await self.bench_info(silo)
        recent = list(info.recent_ids)
        found = await self._url_in_benches(silo, recent, path)
        if found is not None:
            return found
        schema = await self.schema(silo)
        older = [b for b in schema.bench_order if b not in recent]
        return await self._url_in_benches(silo, older, path) if older else None

    async def _url_in_benches(
        self, silo: str, benches: list[str], path: str
    ) -> ProductRef | None:
        if not benches:
            return None
        generations = await self.catalog(silo, benches)
        for generation in generations.values():
            for entry in generation.products:
                page = entry.get("page")
                url = page.get("url") if isinstance(page, dict) else None
                if url and str(url).rstrip("/") == path:  # verbatim, prefix included
                    return ProductRef(
                        product_id=str(entry["id"]),
                        url_path=str(url),
                        silo=silo,
                        name=entry.get("fullname"),
                        bench_id=_product_bench(entry),
                        published=entry.get("published"),
                    )
        return None

    async def _product_from_id(self, product_id: str, silo: str | None) -> ProductRef | None:
        silos = [silo] if silo else None
        if silos is None:
            index = await self.silo_index()
            silos = list(index)
        for candidate in silos:
            try:
                info = await self.bench_info(candidate) if silo else None
            except RtingsError:
                continue
            if info is None:
                # Without a named silo, only already-cached catalogs are consulted: probing
                # 28 silos to find one id would be a burst of requests for a lookup.
                found = self._cached_product(candidate, product_id)
                if found is not None:
                    return found
                continue
            generations = await self.catalog(candidate, info.recent_ids)
            for generation in generations.values():
                for entry in generation.products:
                    if str(entry.get("id")) == product_id:
                        page = entry.get("page")
                        url = page.get("url") if isinstance(page, dict) else None
                        return ProductRef(
                            product_id=product_id,
                            url_path=str(url) if url else "",
                            silo=candidate,
                            name=entry.get("fullname"),
                            bench_id=_product_bench(entry),
                            published=entry.get("published"),
                        )
        return None

    async def _product_from_verdicts(self, product_id: str, silo: str) -> ProductRef | None:
        """Identify a product the catalog does not list through ``side_by_side__review``.

        Its ``product`` block (measured 2026-09-06) carries ``fullname``,
        ``product_page__url`` (the full review path, brand segment included),
        ``silo__url_part`` and ``product_page__early_access``; ``test_bench.id`` is the
        bench the row was tested on. None of that is inferred from the id.
        """
        try:
            envelope, _stale = await self.side_by_side(silo, product_id)
        except RtingsError:
            return None
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
        url = product.get("product_page__url")
        if not url:
            return None
        bench = payload.get("test_bench") if isinstance(payload.get("test_bench"), dict) else {}
        early_access = product.get("product_page__early_access")
        return ProductRef(
            product_id=product_id,
            url_path=str(url),
            silo=str(product.get("silo__url_part") or silo),
            name=product.get("fullname"),
            bench_id=str(bench["id"]) if bench.get("id") is not None else None,
            published=(not early_access) if isinstance(early_access, bool) else None,
        )

    def _cached_product(self, silo: str, product_id: str) -> ProductRef | None:
        directory = self.cache.root / "catalog" / silo
        if not directory.is_dir():
            return None
        for path in sorted(directory.iterdir(), reverse=True):
            envelope = self.cache.read_path(path)
            if envelope is None:
                continue
            for entry in envelope.payload or []:
                if str(entry.get("id")) == product_id:
                    page = entry.get("page")
                    url = page.get("url") if isinstance(page, dict) else None
                    return ProductRef(
                        product_id=product_id,
                        url_path=str(url) if url else "",
                        silo=silo,
                        name=entry.get("fullname"),
                        bench_id=_product_bench(entry),
                        published=entry.get("published"),
                    )
        return None

    async def _search_product(self, query: str, silo: str | None = None) -> ProductRef | None:
        """Resolve free text through RTINGS' live index.

        Only a **review** URL counts: the index also returns articles, tool pages and
        best-of lists, and any of those with a `product_id` would otherwise be handed back
        as "the product". When a silo is named, hits outside it are skipped too.
        """
        result = await api.search(self.transport, query, count=10)
        for hit in result.get("results") or []:
            if not isinstance(hit, dict):
                continue
            product_id = hit.get("product_id")
            url = str(hit.get("url") or "")
            if not product_id or not url:
                continue
            if "/reviews/" not in url or "/reviews/best/" in url:
                continue
            silo_part = url.strip("/").split("/", 1)[0].lower()
            if silo_part == "early-access":
                parts = [s for s in url.split("/") if s]
                silo_part = parts[1].lower() if len(parts) > 1 else silo_part
            if silo and silo_part != silo:
                continue
            return ProductRef(
                product_id=str(product_id),
                url_path=url,
                silo=silo_part,
                name=hit.get("title"),
                bench_id=None,
                # Search hits DO carry `published`, but it describes the *page*, not the
                # product's review row — so it is recorded and never trusted for the blur
                # decision. That still comes from the bench catalog (SPEC §7 step 4a).
                published=None,
            )
        return None

    # -- recommendations ------------------------------------------------------------

    async def recommendation_lists(self, silo: str, *, refresh: bool = False) -> Envelope:
        """Discover a silo's best-of lists from its landing page.

        The one page-extraction path, isolated deliberately: its own parser, its own drift
        alarm, and it never touches the table/graph code path. There is **no**
        recommendations API — a full browser capture of a best-of page fires exactly two
        API POSTs, neither carrying recommendation content.

        Discovery reads ``silo_layout.best`` out of the landing page's ``data-props``
        (verified 2026-09-03: 20 lists on TV). The href scan is only a fallback — it finds
        5 of the 20, because most list paths are **two segments**
        (``/tv/reviews/best/by-size/65-inch``).
        """
        key = validate_silo(silo)
        cached = self.cache.get("recs", key, "_lists.json")
        if cached is not None and not refresh and not cached.is_stale(TTL_RECS):
            return cached

        async def do_fetch() -> Envelope:
            async with file_lock(self.cache.root / "locks" / f"recs-{key}.lock"):
                again = self.cache.get("recs", key, "_lists.json")
                if again is not None and not refresh and not again.is_stale(TTL_RECS):
                    return again
                result = await self.transport.api_get_html(f"/{key}")
                lists = _extract_best_lists(result.text, key)
                if not lists:
                    if cached is not None:
                        self.warn(
                            f"best-of discovery failed for {key}; serving the cached index"
                        )
                        return cached
                    raise RtingsError(
                        errors.RECOMMENDATIONS_MISSING,
                        f"no best-of lists found on the {key} landing page",
                    )
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=result.url,
                    cache_tier=ANONYMOUS,
                    request={"page": f"/{key}"},
                    payload={"lists": lists},
                    silo=key,
                )
                self.cache.put(envelope, "recs", key, "_lists.json")
                return envelope

        return await self._flight.run(f"recs:{key}", do_fetch)

    async def article(self, silo: str, slug: str, *, refresh: bool = False) -> Envelope:
        """One ``/{silo}/learn/{slug}`` prose page.

        The second page-extraction path, and the only other one. RTINGS' lineup and
        explainer articles answer questions no measurement can ("does Sony sell a bigger
        OLED this year"), and nothing here is gated — the page carries no ``unblurred``
        bit and no test row, so it is cached at ``ANONYMOUS`` like the best-of index.

        ``/learn/`` is required by :func:`article_path`, and that is a paywall guard:
        a product review page is ``/{silo}/reviews/{brand}/{model}`` and fetching one as
        HTML spends a preview (RECON.md §14.2). No path this builds can name one.
        """
        key = validate_silo(silo)
        clean = validate_slug(slug)
        file_key = slug_to_key(clean)
        cached = self.cache.get("articles", key, f"{file_key}.json")
        if cached is not None and not refresh and not cached.is_stale(TTL_RECS):
            return cached

        async def do_fetch() -> Envelope:
            async with file_lock(self.cache.root / "locks" / f"art-{key}-{file_key}.lock"):
                again = self.cache.get("articles", key, f"{file_key}.json")
                if again is not None and not refresh and not again.is_stale(TTL_RECS):
                    return again
                path = article_path(key, clean)
                try:
                    result = await self.transport.api_get_html(path)
                except RtingsError:
                    if again is not None:
                        self.warn("refresh_failed: serving the cached article")
                        return again
                    raise
                payload = _extract_article(result.text)
                if payload is None:
                    raise RtingsError(
                        errors.RECOMMENDATIONS_MISSING,
                        f"no article body found at {result.url} — RTINGS answers an "
                        "unknown learn slug with another page, so this is most likely a "
                        "slug that does not exist",
                    )
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=result.url,
                    cache_tier=ANONYMOUS,
                    request={"page": path},
                    payload=payload,
                    silo=key,
                )
                self.cache.put(envelope, "articles", key, f"{file_key}.json")
                return envelope

        return await self._flight.run(f"article:{key}:{file_key}", do_fetch)

    async def recommendation(
        self, silo: str, list_slug: str, *, refresh: bool = False
    ) -> Envelope:
        """One ranked best-of list, extracted from ``page_data.page.recommendation``.

        Best-of slugs **redirect** (``/tv/reviews/best/tvs`` -> ``.../tvs-on-the-market``),
        so the canonical URL is cached alongside the payload.
        """
        key = validate_silo(silo)
        slug = validate_slug(list_slug)
        file_key = slug_to_key(slug)
        # Tier-keyed since 2026-09-06. The page was believed to carry no gated field, and
        # it does: each pick's `featured_test_results` and `ratings` have their own
        # `unblurred` bit. Untiered, a member was served a two-day-old anonymous copy —
        # every featured score `tested_gated` — with nothing to say a refresh would help.
        demand = self.auth.demand_tier("tests", self.auth.cached_probe())
        variants = self.cache.read_variants("recs", key, key=file_key, min_tier=demand)
        if variants and not refresh and not variants[0].is_stale(TTL_RECS):
            return variants[0]

        async def do_fetch() -> Envelope:
            async with file_lock(self.cache.root / "locks" / f"rec-{key}-{file_key}.lock"):
                again = self.cache.read_variants("recs", key, key=file_key, min_tier=demand)
                if again and not refresh and not again[0].is_stale(TTL_RECS):
                    return again[0]
                # Two shapes, tried in order (see :func:`recommendation_paths`), and a
                # shape only counts as *found* when a template matches: RTINGS answers an
                # unknown best-of slug with a 200 landing page as readily as a 404, so
                # stopping at the first successful GET would never reach the brand page.
                result = None
                payload = None
                last: RtingsError | None = None
                for path in recommendation_paths(key, slug):
                    try:
                        fetched = await self.transport.api_get_html(path)
                    except RtingsError as exc:
                        last = exc
                        continue
                    result = fetched
                    # Two templates are legitimate (see `_extract_recommendation_static`),
                    # so the alarm fires only when NEITHER matches — that, not "the props
                    # are missing", is the drift signal now.
                    payload = _extract_recommendation(fetched.text) or (
                        _extract_recommendation_static(fetched.text)
                    )
                    if payload is not None:
                        break
                if result is None:
                    if again:
                        self.warn("refresh_failed: serving the cached best-of list")
                        return again[0]
                    raise last or RtingsError(
                        errors.RECOMMENDATIONS_MISSING, f"no best-of page for {slug!r}"
                    )
                if payload is None:
                    raise RtingsError(
                        errors.RECOMMENDATIONS_MISSING,
                        f"neither best-of template matched {result.url}",
                    )
                rows = _recommendation_insider_rows(payload)
                probe = self.auth.cached_probe()
                tier = self.auth.write_tier("tests", probe)
                demoted = False
                if tier != ANONYMOUS:
                    probe = await self.auth.session_probe(force=True)
                    tier = self.auth.write_tier("tests", probe)
                    demoted = self.auth.should_demote(
                        tier=tier,
                        surface="tests",
                        rows=rows,
                        insider_ids={FEATURED_ID},
                        unpublished_product_ids=set(),
                        probe=probe,
                    )
                    if demoted:
                        tier = ANONYMOUS
                reason: str | None = None
                if tier == ANONYMOUS and self.auth.session_may_unblur(probe):
                    # A best-of page is not bench-scoped, so "anonymous is proven to serve
                    # this" is read off the silo's current bench — the one its picks are
                    # ranked on.
                    info = await self.bench_info(key)
                    current = info.current_id(await self.schema(key))
                    reason = self.auth.anonymous_write_refusal(
                        surface="tests",
                        rows=rows,
                        insider_ids={FEATURED_ID},
                        unpublished_product_ids=set(),
                        probe=probe,
                        anonymous_serves=bool(current)
                        and ObservationStore(self.cache).anonymous_serves(
                            key, current or "", surface="tests"
                        ),
                    )
                envelope = Envelope(
                    fetched_at=time.time(),
                    source_url=result.url,
                    cache_tier=tier,
                    request={"page": f"/{key}/reviews/best/{slug}", "slug": slug},
                    payload=payload,
                    silo=key,
                    notes={
                        "has_unblurred_insider": any(r.get("unblurred") for r in rows),
                    },
                )
                if reason is not None:
                    self._refuse_uncached_warning(
                        key,
                        "recs",
                        reason,
                        1,
                        cause=self._label_cause(demoted=demoted, probe=probe),
                    )
                    return envelope
                self.cache.put_variant(envelope, "recs", key, key=file_key)
                self.cache.prune_variants("recs", key, key=file_key)
                self.cache.flush_lru()
                return envelope

        return await self._flight.run(f"rec:{key}:{file_key}", do_fetch)


def _extract_best_lists(html: str, silo: str) -> list[dict[str, Any]]:
    """``silo_layout.best`` first, an href scan second.

    The Best nav carries two shapes and discovery kept only the first: the ``/best/``
    lists, and the per-BRAND pages one segment up (``/tv/reviews/sony``, "The 4 Best Sony
    TVs"), which the ``brands`` list's own prose links to. Dropping them turned
    ``list="sony"`` into ``unknown_list`` (shopper round 3, 2026-09-07), so they are kept
    and tagged ``kind: "brand"``.
    """
    prefix = f"/{silo}/reviews/best/"
    brand_re = re.compile(rf"^/{re.escape(silo)}/reviews/([a-z0-9][a-z0-9-]*)/?$")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for blob in extract_data_props(html, contains="silo_layout"):
        layout = blob.get("silo_layout")
        if not isinstance(layout, dict):
            continue
        for entry in layout.get("best") or []:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "")
            if url.startswith(prefix):
                slug, kind = url[len(prefix) :].strip("/"), "best"
            else:
                brand = brand_re.match(url)
                if not brand:
                    continue
                slug, kind = brand.group(1), "brand"
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(
                {
                    "list": slug,
                    "kind": kind,
                    "title": entry.get("title"),
                    "short": entry.get("short"),
                    "url": url,
                }
            )
    if out:
        return out
    for _href, silo_part, slug in _REC_LINK_RE.findall(html):
        if silo_part == silo and slug not in seen:
            seen.add(slug)
            out.append(
                {
                    "list": slug,
                    "kind": "best",
                    "title": None,
                    "short": None,
                    "url": f"{prefix}{slug}",
                }
            )
    return out


def article_path(silo: str, slug: str) -> str:
    """Where a ``learn`` article lives. The ``/learn/`` segment is not decoration: it is
    what makes this path incapable of naming a product review page, whose HTML GET spends
    a preview (RECON.md §14.2)."""
    return f"/{silo}/learn/{slug}"


def _extract_article(html: str) -> dict[str, Any] | None:
    """Pull ``page.article`` out of the page props.

    Shallow and defensive, like the best-of extractor: RTINGS serves *something* for an
    unknown slug (``/tv/learn`` answers with ``/research``), so "no article object" is the
    only honest signal that the page asked for is not there.
    """
    for blob in extract_data_props(html, contains="article"):
        page_data = blob.get("page_data")
        page = page_data.get("page") if isinstance(page_data, dict) else blob.get("page")
        if not isinstance(page, dict):
            continue
        article = page.get("article")
        if not isinstance(article, dict) or not article.get("title"):
            continue
        return {
            "title": article.get("title"),
            "url": page.get("url"),
            "introduction": article.get("introduction"),
            # `text_with_anchors` is the same prose with the heading anchors the table of
            # contents links to; it is what `section` is sliced out of.
            "text": article.get("text_with_anchors") or article.get("text"),
            "conclusion": article.get("conclusion_with_anchors") or article.get("conclusion"),
            "toc_items": article.get("toc_items") or [],
            "updated_at": article.get("latest_update_date") or page.get("updated_at"),
            "created_at": article.get("created_at"),
            "meta_description": article.get("meta_description"),
            "authors": [
                a.get("name")
                for a in (page.get("authors") or [])
                if isinstance(a, dict) and a.get("name")
            ],
        }
    return None


def recommendation_paths(silo: str, slug: str) -> list[str]:
    """Where a best-of ``list`` may live, in the order to try.

    ``/{silo}/reviews/best/{slug}`` is the list shape; a brand page is one segment up.
    The brand shape is offered ONLY for a single-segment slug, and that is a paywall
    guard, not tidiness: a product review page is ``/{silo}/reviews/{brand}/{model}``
    (RECON.md §14.2), and fetching one as HTML spends a preview. A slug carrying a slash
    could name one, so it never reaches the second form.
    """
    paths = [f"/{silo}/reviews/best/{slug}"]
    if "/" not in slug and slug != "best":
        paths.append(f"/{silo}/reviews/{slug}")
    return paths


def _extract_recommendation(html: str) -> dict[str, Any] | None:
    """Pull the ranked picks out of the page props.

    Kept deliberately shallow and defensive: this is the one surface with no API behind it,
    so a layout change must surface as ``recommendations_missing`` rather than a plausible
    empty list.
    """
    for blob in extract_data_props(html, contains="recommendation"):
        page_data = blob.get("page_data")
        if not isinstance(page_data, dict):
            continue
        page = page_data.get("page")
        if not isinstance(page, dict):
            continue
        recommendation = page.get("recommendation")
        if not isinstance(recommendation, dict):
            continue
        picks = recommendation.get("product_recommendations")
        if not isinstance(picks, list):
            continue
        return {
            "title": recommendation.get("seasonal_title") or blob.get("title"),
            "url": page.get("url"),
            "published_at": page.get("published_at"),
            "updated_at": page.get("updated_at"),
            "introduction": recommendation.get("introduction"),
            "conclusion": recommendation.get("conclusion"),
            "product_recommendations": picks,
            "recommendation_mentions": recommendation.get("recommendation_mentions") or [],
            # Which of the two legitimate templates answered (RECON.md §12.17). The
            # migration state was a dated manual note; `rtings-mcp drift` reads this.
            "template": "props",
        }
    return None


# ---------------------------------------------------------------------------------
# The second best-of template (server-rendered)
# ---------------------------------------------------------------------------------
#
# RTINGS is migrating best-of pages off the monolithic `RecommendationVuePage` (one big
# `data-props` blob) onto a server-rendered template whose only Vue parts are small islands
# — `RecommendationPagePrices`, `BookmarkControls`, `DistributionTooltip`. Measured
# 2026-09-05: mattress and running-shoes have moved, the other 12 silos sampled have not,
# and it does not track silo age (refrigerator is the newest silo and still on the old one).
# So this is a rollout in progress and BOTH shapes are legitimate; `recommendations_missing`
# now means neither matched, which is the real drift signal.
#
# The page's own bundle is `recommendation-page-static-*.js`, 3 KB with no `/api/v2/safe/`
# reference at all, so there is no API behind this template — extraction is the only route.

#: The pick container. Matched by class TOKEN with attributes allowed either side, so an
#: added class or attribute does not silently drop every pick on the page.
_PICK_RE = re.compile(
    r'<li[^>]*\sclass="[^"]*(?<![\w-])recommendation_vue_page-pr(?![\w-])[^"]*"[^>]*>'
)


def _tag_inner(text: str, start: int, tag: str) -> str:
    """Inner HTML of the ``<tag>`` whose ``<`` sits at ``start``, counting nested ``tag``s.

    A non-greedy regex stops at the first ``</div>``, which on these blocks is the end of a
    nested tooltip rather than the end of the section — it truncated every description at
    the first inline element.
    """
    open_end = text.find(">", start)
    if open_end == -1:
        return ""
    opener, closer = f"<{tag}", f"</{tag}>"
    depth = 1
    cursor = open_end + 1
    while depth:
        nxt_open = text.find(opener, cursor)
        nxt_close = text.find(closer, cursor)
        if nxt_close == -1:
            return text[open_end + 1 :]
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            cursor = nxt_open + len(opener)
            continue
        depth -= 1
        if depth == 0:
            return text[open_end + 1 : nxt_close]
        cursor = nxt_close + len(closer)
    return ""


def _block(text: str, cls: str) -> str | None:
    """The element carrying ``cls`` as one of its classes, by class TOKEN not exact match.

    RTINGS compounds these — the page intro is ``class="recommendation_vue_page-intro
    e-rich_content"`` and the update stamp is a ``<span>``, not a ``<div>`` — so an exact
    ``class="X"`` div-only match silently found neither.
    """
    match = re.search(
        r'<(div|span)[^>]*\sclass="[^"]*(?<![\w-])' + re.escape(cls) + r'(?![\w-])[^"]*"',
        text,
    )
    return _tag_inner(text, match.start(), match.group(1)) if match else None


def _prop_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text(value: str | None) -> str | None:
    cleaned = strip_html(value)
    return cleaned or None


def _props_in(text: str, component: str) -> dict[str, Any] | None:
    # Attributes may sit between the two; requiring them adjacent made this depend on
    # RTINGS' attribute ORDER, which nothing guarantees.
    for match in re.finditer(
        r'data-vue="' + re.escape(component) + r'"[^>]*?\sdata-props="([^"]*)"', text
    ):
        try:
            parsed = json.loads(unescape(match.group(1)))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _static_featured(block: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The featured strip beside a pick, split into test rows and usage ratings.

    The tooltip beside each item carries ``target_label`` and ``target_type``, which is how
    a usage is told from a test. Its ``target_id`` is deliberately NOT used as an
    ``original_id``: measured 2026-09-05, "Side Sleeping" is ``38309`` here and ``36553`` in
    the schema, so emitting it would be a confidently wrong join key. A null id and a real
    name is the honest pair.
    """
    tests: list[dict[str, Any]] = []
    ratings: list[dict[str, Any]] = []
    for match in re.finditer(r'<div class="recommendation_featured_list-item"[ >]', block):
        item = _tag_inner(block, match.start(), "div")
        # The rendered label carries its own punctuation ("Bed-In-A-Box:&nbsp;"); the
        # tooltip's `target_label` and the props template both give the bare name, so trim
        # it rather than ship one field in two shapes.
        name = _text(_block(item, "recommendation_featured_list-item-name"))
        if name:
            name = name.rstrip(":").strip() or None
        display = _text(_block(item, "recommendation_featured_list-item-value"))
        score_match = re.search(r'class="score_box-value">([^<]*)<', item)
        score = None
        if score_match:
            try:
                score = float(score_match.group(1).strip())
            except ValueError:
                score = None
        tooltip = _props_in(item, "DistributionTooltip") or {}
        label = _prop_str(tooltip.get("target_label")) or name
        if not label:
            continue
        # Nothing rendered at all: say so rather than inventing a paywall. `_featured_results`
        # maps an unrecognised status to `unknown_row_status`, which is exactly right here.
        seen = score is not None or display is not None
        if _prop_str(tooltip.get("target_type")) == "usage":
            ratings.append(
                {
                    "usage": {"original_id": None, "name": label},
                    "unblurred": seen,
                    "score": score,
                }
            )
            continue
        tests.append(
            {
                "test": {"name": label, "kind": None, "insider_only": False},
                "status": "tested" if seen else "unknown",
                "unblurred": seen,
                "rendered_value": display,
                "score": score,
            }
        )
    return tests, ratings


_MENTION_ITEM_RE = re.compile(r"<li\b[^>]*>")


def _static_mentions(html: str) -> list[dict[str, Any]]:
    """The "Notable Mentions" section, emitted in the props template's shape.

    Measured 2026-09-08: the section is plainly on the server-rendered page (an
    ``<a id="mentions">`` anchor, an ``<h2>Notable Mentions</h2>`` and a
    ``recommendation_vue_page-mentions`` block listed in the page's own table of contents),
    while ``recommendation_mentions`` came back ``[]`` there and populated on the props
    template. Each item is ``<strong>Name:&nbsp;</strong>`` + a rich-content span + a "See
    our review" link.

    ``<li>`` is frequently unclosed on these pages (69 opens to 26 closes, measured
    2026-09-05), so items are cut at the next ``<li``, never at ``</li>``.
    """
    block = _block(html, "recommendation_vue_page-mentions")
    if not block:
        return []
    starts = [m.start() for m in _MENTION_ITEM_RE.finditer(block)]
    out: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(block)
        item = block[start:end]
        name_match = re.search(r"<strong\b[^>]*>(.*?)</strong>", item, re.S)
        name = _text(name_match.group(1)) if name_match else None
        if name:
            # The rendered label carries its own punctuation ("Purple RestorePlus Hybrid:"),
            # and the props template gives the bare fullname.
            name = name.rstrip(":").strip() or None
        description = _block(item, "e-rich_content_inline")
        href = re.search(r'<a\b[^>]*\shref="([^"]+)"[^>]*>\s*See our review', item)
        if not name and not description:
            continue
        out.append(
            {
                "description": (description or "").strip() or None,
                "sku": None,
                # No `brand` on this template: the props one nests `product.brand.name` and
                # the server-rendered page never renders it separately. A null beats a guess.
                "product": {
                    "fullname": name,
                    "page": {"url": href.group(1) if href else None},
                },
            }
        )
    return out


def _extract_recommendation_static(html: str) -> dict[str, Any] | None:
    """Parse the server-rendered best-of template into the props template's shape.

    Emitting the same payload keeps every consumer — the pick mapper, the featured-row tier
    derivation, the cached envelope — unchanged, so the two templates differ only here.
    """
    starts = [m.end() for m in _PICK_RE.finditer(html)]
    if not starts:
        return None

    picks: list[dict[str, Any]] = []
    # The picks sit in one <ol>, so the last one ends there rather than at end-of-document.
    # Unbounded, it would absorb any later section that reuses these classes — a "Notable
    # Mentions" block or a comparison table would silently become the last pick's featured
    # strip. (Verified 2026-09-05 that nothing does so today; <li> is frequently unclosed on
    # these pages, 69 opens to 26 closes, so counting </li> is not an option.)
    closing = html.find("</ol>", starts[-1])
    bounds = [*starts[1:], closing if closing != -1 else len(html)]
    for index, start in enumerate(starts):
        block = html[start : bounds[index]]
        prices = _props_in(block, "RecommendationPagePrices") or {}
        bookmark = _props_in(block, "BookmarkControls") or {}
        product_id = _prop_str(prices.get("product_id")) or _prop_str(bookmark.get("product_id"))
        # Class TOKEN with attributes in any order: the name and review URL are the two
        # fields a caller reads first, and pinning them to `class="… t-h3" href=…` in that
        # exact order would blank both the moment RTINGS reorders an attribute.
        link = re.search(
            r'<a([^>]*\sclass="[^"]*(?<![\w-])recommendation_vue_page-pr-name(?![\w-])'
            r'[^"]*"[^>]*)>(.*?)</a>',
            block,
            re.S,
        )
        href = re.search(r'\shref="([^"]+)"', link.group(1)) if link else None
        heading = re.search(
            r'<h2[^>]*class="[^"]*e-page_section_title"[^>]*>(.*?)</h2>', block, re.S
        )
        description = _block(block, "recommendation_vue_page-pr-description")
        # The props template's `description` is bare prose; this one wraps it in a
        # rich-content div. Unwrap so a caller sees one shape, not two.
        inner = _block(description or "", "e-rich_content")
        if inner is not None:
            description = inner
        tests, ratings = _static_featured(block)
        if product_id is None and link is None:
            continue
        picks.append(
            {
                "title": _text(heading.group(1)) if heading else None,
                "subtitle": None,
                "description": description.strip() if description else None,
                "product_id": product_id,
                "product": {
                    "id": product_id,
                    "fullname": _text(link.group(2)) if link else None,
                    "page": {"url": href.group(1) if href else None},
                },
                "featured_test_results": tests,
                "ratings": ratings,
            }
        )

    if not picks:
        return None

    heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    canonical = re.search(r'<link rel="canonical" href="https://www\.rtings\.com([^"]+)"', html)
    # The stamp renders as "Updated Aug 26, 2026 at 12:45 pm"; the props template supplies a
    # bare timestamp, so drop the label rather than ship two shapes for one field.
    updated = _text(_block(html, "recommendation_vue_page-hero-update"))
    if updated:
        updated = re.sub(r"^updated\s*", "", updated, flags=re.I).strip() or None
    intro_html = _block(html[: starts[0]], "recommendation_vue_page-intro")
    intro = (intro_html or "").strip() or None

    return {
        "title": _text(heading.group(1)) if heading else page_title(html),
        "url": canonical.group(1) if canonical else None,
        "published_at": None,
        "updated_at": updated,
        "introduction": intro,
        "conclusion": None,
        "product_recommendations": picks,
        "recommendation_mentions": _static_mentions(html),
        "template": "static",
    }


def _product_bench(product: dict[str, Any]) -> str | None:
    review = product.get("review")
    if not isinstance(review, dict):
        return None
    bench = review.get("test_bench")
    if not isinstance(bench, dict):
        return None
    bench_id = bench.get("id")
    return None if bench_id is None else str(bench_id)


def _request_hash(silo: str, benches: Iterable[str], ids: Iterable[str]) -> str:
    payload = "|".join([silo, ",".join(sorted(benches)), ",".join(sorted(ids))])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _chunks(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)] or [[]]
