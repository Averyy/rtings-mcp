"""Per-(silo, bench) enforcement observations — what ``rt_silos()`` routes on.

``has_paywall`` is ``true`` on all 28 silos and therefore carries no information. What an
agent needs is whether *this* silo's numbers are answerable right now, and the only signal
for that is what came back ``unblurred`` on a real fetch. So every fetch folds its rows into
an observation, and ``rt_silos()`` reports it as ``data_completeness``.

**Never a hardcoded map.** The 12/16 split tracks RTINGS' business decisions, moves silently,
and is a dated snapshot even in ``docs/enforcement-snapshot.json`` — which is a release-time
diff baseline and documentation only, never read at runtime.

**An observation carries its provenance (added 2026-09-06).** ``data_completeness`` is a
claim about what *anonymous* gets, so only a signed-out fetch can establish ``full``. A
member's fetch of tv comes back 588/588 unblurred, and recording that unqualified made this
machine tell every agent — permanently, because with a stored credential no signed-out fetch
of equal width ever happens again — that a gated silo is fully answerable. So each entry
records whether the session could have unblurred it (:data:`PROVENANCE_LOGGED_IN`) or not
(:data:`PROVENANCE_ANONYMOUS`); a logged-in observation never replaces an anonymous one, and
its own ``completeness`` is ``unknown`` unless it saw nothing unblurred at all (``gated`` —
a membership cannot *add* blur, so that reading is true of the silo).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .cache import ANONYMOUS, Cache, Envelope, validate_id, validate_silo
from .envelope import ScoresAvailable

__all__ = [
    "PROVENANCE_ANONYMOUS",
    "PROVENANCE_LOGGED_IN",
    "PROVENANCE_UNKNOWN",
    "Observation",
    "ObservationStore",
    "ScoresAvailable",
]

COMPLETENESS_FULL = "full"
COMPLETENESS_GATED = "gated"
COMPLETENESS_PARTIAL = "partial"
COMPLETENESS_UNKNOWN = "unknown"

#: The session could not have unblurred anything: no credential configured, or the probe
#: read it as dead. What came back is what anonymous gets.
PROVENANCE_ANONYMOUS = "anonymous"
#: A credential was sent and the probe did not rule it live-out (``member``, ``free``,
#: ``unknown``, or a CloudFront-cached reading): unblurred rows may be the membership's doing.
PROVENANCE_LOGGED_IN = "logged_in"
#: An entry written before provenance was recorded. On a machine that had a credential it
#: may already be a member's reading, so it is trusted for nothing and replaced by the next
#: tagged observation of any width.
PROVENANCE_UNKNOWN = "unknown"

#: An observation older than this is reported but flagged; enforcement changes silently, so
#: a months-old reading should not be presented as current.
OBSERVATION_FRESH_S = 30 * 86_400

#: Which surface's counts prove "anonymous is served here" for a given cache surface.
#: Verdict scores are usage ratings by another name, so ``verdicts`` reads the usage counts.
_PROOF_SURFACE: dict[str, str] = {
    "tests": "insider",
    "reviews": "insider",
    "ratings": "usage",
    "verdicts": "usage",
}


@dataclass(slots=True, frozen=True)
class Observation:
    silo: str
    bench_id: str
    insider_total: int
    insider_unblurred: int
    public_total: int
    public_unblurred: int
    usage_total: int
    usage_unblurred: int
    observed_at: float
    provenance: str = PROVENANCE_UNKNOWN

    @property
    def completeness(self) -> str:
        if self.insider_total == 0:
            # Nothing gate-able was observed, so nothing can be said about enforcement.
            return COMPLETENESS_UNKNOWN
        ratio = self.insider_unblurred / self.insider_total
        if self.provenance != PROVENANCE_ANONYMOUS:
            # A signed-in fetch can explain every unblurred row, so it cannot say `full`
            # or `partial`; it CAN say `gated` — nothing about a membership adds blur, so
            # a fully-blurred reading is true of the silo whoever fetched it.
            return COMPLETENESS_GATED if ratio <= 0.0 else COMPLETENESS_UNKNOWN
        if ratio >= 1.0:
            return COMPLETENESS_FULL
        if ratio <= 0.0:
            return COMPLETENESS_GATED
        return COMPLETENESS_PARTIAL

    @property
    def insider_ratio(self) -> float | None:
        if self.insider_total == 0:
            return None
        return round(self.insider_unblurred / self.insider_total, 4)

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.observed_at) <= OBSERVATION_FRESH_S

    def to_json(self) -> dict[str, Any]:
        return {
            "bench_id": self.bench_id,
            "insider_total": self.insider_total,
            "insider_unblurred": self.insider_unblurred,
            "public_total": self.public_total,
            "public_unblurred": self.public_unblurred,
            "usage_total": self.usage_total,
            "usage_unblurred": self.usage_unblurred,
            "observed_at": self.observed_at,
            "provenance": self.provenance,
        }


def _entry_provenance(entry: dict[str, Any]) -> str:
    value = entry.get("provenance")
    if value in (PROVENANCE_ANONYMOUS, PROVENANCE_LOGGED_IN):
        return str(value)
    return PROVENANCE_UNKNOWN


class ObservationStore:
    def __init__(self, cache: Cache) -> None:
        self.cache = cache

    def record(
        self, silo: str, bench_id: str, scores: ScoresAvailable, *, provenance: str
    ) -> None:
        """Replace the stored counts when this observation is at least as informative.

        **Not cumulative.** Adding forever means an open->gated flip reads ``partial``
        indefinitely (the old unblurred rows never age out).

        So a new observation replaces the old one whenever it saw at least as many gate-able
        rows. A narrow projection (one test) does not erase a wide one; a wide one always
        wins, and a re-run of the same width always wins because it is newer.

        **Provenance outranks width.** ``provenance`` is required rather than defaulted
        because getting it wrong is silent — the exact defect this guards against. An
        anonymous observation is the ground truth ``data_completeness`` describes, so a
        logged-in one never replaces it however wide (with a stored credential no signed-out
        fetch will ever come along to put it back), and an anonymous one replaces a logged-in
        or untagged one at any width. Between two of the same provenance, width decides.
        """
        if provenance not in (PROVENANCE_ANONYMOUS, PROVENANCE_LOGGED_IN):
            raise ValueError(f"unknown observation provenance: {provenance!r}")
        key = validate_silo(silo)
        bench = validate_id(bench_id, what="bench_id")
        existing = self.cache.get("observed", f"{key}.json")
        payload: dict[str, Any] = dict(existing.payload or {}) if existing else {}
        current = dict(payload.get(bench) or {})
        current_provenance = _entry_provenance(current) if current else None
        if current_provenance == PROVENANCE_ANONYMOUS and provenance != PROVENANCE_ANONYMOUS:
            return
        if current_provenance == provenance and (
            scores.insider_tests.total < int(current.get("insider_total", 0))
        ):
            return
        merged = {
            "insider_total": scores.insider_tests.total,
            "insider_unblurred": scores.insider_tests.unblurred,
            "public_total": scores.public_tests.total,
            "public_unblurred": scores.public_tests.unblurred,
            "usage_total": scores.usage_ratings.total,
            "usage_unblurred": scores.usage_ratings.unblurred,
            "observed_at": time.time(),
            "provenance": provenance,
        }
        payload[bench] = merged
        envelope = Envelope(
            fetched_at=time.time(),
            source_url="",
            cache_tier=ANONYMOUS,
            request={"silo": key},
            payload=payload,
            silo=key,
        )
        self.cache.put(envelope, "observed", f"{key}.json")

    def anonymous_serves(self, silo: str, bench_id: str, *, surface: str) -> bool:
        """Has a **signed-out** fetch shown this (silo, bench) to serve ``surface`` in full?

        This is the tie-break the anonymous-label guard needs: a member's 100%-unblurred
        response and an open silo's 100%-unblurred anonymous response are the same bytes,
        and only a prior anonymous reading of the same bench separates them. Nothing else
        counts — not a logged-in observation (it is the thing in question), not an untagged
        one (it may be exactly that), not a ``partial`` one (some products were withheld),
        and not a stale one: enforcement flips silently, a silo that opens tomorrow can gate
        next quarter, and a signed-in machine never refreshes this reading on its own, so a
        30-day-old proof is treated as no proof — the guard then refuses (loudly) rather than
        labelling member data on the strength of last season's paywall map.
        """
        observation = self.read(silo, bench_id)
        if observation is None or observation.provenance != PROVENANCE_ANONYMOUS:
            return False
        if not observation.is_fresh:
            return False
        which = _PROOF_SURFACE.get(surface)
        if which == "insider":
            return (
                observation.insider_total > 0
                and observation.insider_unblurred >= observation.insider_total
            )
        if which == "usage":
            return (
                observation.usage_total > 0
                and observation.usage_unblurred >= observation.usage_total
            )
        return False

    def read(self, silo: str, bench_id: str | None = None) -> Observation | None:
        key = validate_silo(silo)
        existing = self.cache.get("observed", f"{key}.json")
        if existing is None or not isinstance(existing.payload, dict):
            return None
        payload: dict[str, Any] = existing.payload
        if bench_id is not None:
            entry = payload.get(validate_id(bench_id, what="bench_id"))
            chosen_bench = str(bench_id)
        else:
            # Prefer the bench that actually SAYS something, then the freshest. A bench
            # with zero insider rows — or one only ever fetched signed in — says nothing
            # about enforcement, and picking it by timestamp alone reports `unknown` for a
            # silo already measured as open — the recent-bench set routinely includes
            # benches with no published schema (mattress: 4 recent benches, 1 with
            # definitions), so those empty observations are normal.
            best: tuple[str, dict[str, Any]] | None = None
            for candidate_bench, candidate in payload.items():
                if not isinstance(candidate, dict):
                    continue
                if best is None:
                    best = (candidate_bench, candidate)
                    continue
                rank = (
                    self._from_entry(key, candidate_bench, candidate).completeness
                    != COMPLETENESS_UNKNOWN,
                    candidate.get("observed_at", 0),
                )
                best_rank = (
                    self._from_entry(key, best[0], best[1]).completeness
                    != COMPLETENESS_UNKNOWN,
                    best[1].get("observed_at", 0),
                )
                if rank > best_rank:
                    best = (candidate_bench, candidate)
            if best is None:
                return None
            chosen_bench, entry = best
        if not isinstance(entry, dict):
            return None
        return self._from_entry(key, chosen_bench, entry)

    @staticmethod
    def _from_entry(silo: str, bench_id: str, entry: dict[str, Any]) -> Observation:
        return Observation(
            silo=silo,
            bench_id=bench_id,
            insider_total=int(entry.get("insider_total", 0)),
            insider_unblurred=int(entry.get("insider_unblurred", 0)),
            public_total=int(entry.get("public_total", 0)),
            public_unblurred=int(entry.get("public_unblurred", 0)),
            usage_total=int(entry.get("usage_total", 0)),
            usage_unblurred=int(entry.get("usage_unblurred", 0)),
            observed_at=float(entry.get("observed_at", 0.0)),
            provenance=_entry_provenance(entry),
        )
