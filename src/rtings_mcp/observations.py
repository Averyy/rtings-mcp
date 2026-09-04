"""Per-(silo, bench) enforcement observations — what ``rt_silos()`` routes on.

``has_paywall`` is ``true`` on all 28 silos and therefore carries no information. What an
agent needs is whether *this* silo's numbers are answerable right now, and the only signal
for that is what came back ``unblurred`` on a real fetch. So every fetch folds its rows into
an observation, and ``rt_silos()`` reports it as ``data_completeness``.

**Never a hardcoded map.** The 12/16 split tracks RTINGS' business decisions, moves silently,
and is a dated snapshot even in ``docs/enforcement-snapshot.json`` — which is a release-time
diff baseline and documentation only, never read at runtime.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .cache import ANONYMOUS, Cache, Envelope, validate_id, validate_silo
from .envelope import ScoresAvailable

__all__ = ["Observation", "ObservationStore", "ScoresAvailable"]

COMPLETENESS_FULL = "full"
COMPLETENESS_GATED = "gated"
COMPLETENESS_PARTIAL = "partial"
COMPLETENESS_UNKNOWN = "unknown"

#: An observation older than this is reported but flagged; enforcement changes silently, so
#: a months-old reading should not be presented as current.
OBSERVATION_FRESH_S = 30 * 86_400


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

    @property
    def completeness(self) -> str:
        if self.insider_total == 0:
            # Nothing gate-able was observed, so nothing can be said about enforcement.
            return COMPLETENESS_UNKNOWN
        ratio = self.insider_unblurred / self.insider_total
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
        }


class ObservationStore:
    def __init__(self, cache: Cache) -> None:
        self.cache = cache

    def record(self, silo: str, bench_id: str, scores: ScoresAvailable) -> None:
        """Replace the stored counts when this observation is at least as informative.

        **Not cumulative.** Adding forever means an open->gated flip reads ``partial``
        indefinitely (the old unblurred rows never age out), and after a member's fetch a
        gated silo keeps reporting ``full`` on that machine long after the cookie lapsed —
        the stale-map lie the release re-scan exists to catch, reached locally.

        So a new observation replaces the old one whenever it saw at least as many gate-able
        rows. A narrow projection (one test) does not erase a wide one; a wide one always
        wins, and a re-run of the same width always wins because it is newer.
        """
        key = validate_silo(silo)
        bench = validate_id(bench_id, what="bench_id")
        existing = self.cache.get("observed", f"{key}.json")
        payload: dict[str, Any] = dict(existing.payload or {}) if existing else {}
        current = dict(payload.get(bench) or {})
        if scores.insider_tests.total < int(current.get("insider_total", 0)):
            return
        merged = {
            "insider_total": scores.insider_tests.total,
            "insider_unblurred": scores.insider_tests.unblurred,
            "public_total": scores.public_tests.total,
            "public_unblurred": scores.public_tests.unblurred,
            "usage_total": scores.usage_ratings.total,
            "usage_unblurred": scores.usage_ratings.unblurred,
            "observed_at": time.time(),
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
            # Prefer the bench that actually SAW gate-able rows, then the freshest. A bench
            # with zero insider rows says nothing about enforcement, and picking it by
            # timestamp alone reports `unknown` for a silo already measured as open — the
            # recent-bench set routinely includes benches with no published schema (mattress:
            # 4 recent benches, 1 with definitions), so those empty observations are normal.
            best: tuple[str, dict[str, Any]] | None = None
            for candidate_bench, candidate in payload.items():
                if not isinstance(candidate, dict):
                    continue
                if best is None:
                    best = (candidate_bench, candidate)
                    continue
                rank = (
                    int(candidate.get("insider_total", 0)) > 0,
                    candidate.get("observed_at", 0),
                )
                best_rank = (
                    int(best[1].get("insider_total", 0)) > 0,
                    best[1].get("observed_at", 0),
                )
                if rank > best_rank:
                    best = (candidate_bench, candidate)
            if best is None:
                return None
            chosen_bench, entry = best
        if not isinstance(entry, dict):
            return None
        return Observation(
            silo=key,
            bench_id=chosen_bench,
            insider_total=int(entry.get("insider_total", 0)),
            insider_unblurred=int(entry.get("insider_unblurred", 0)),
            public_total=int(entry.get("public_total", 0)),
            public_unblurred=int(entry.get("public_unblurred", 0)),
            usage_total=int(entry.get("usage_total", 0)),
            usage_unblurred=int(entry.get("usage_unblurred", 0)),
            observed_at=float(entry.get("observed_at", 0.0)),
        )
