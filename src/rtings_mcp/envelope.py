"""The response envelope and ``scores_available`` (SPEC §7).

Every tool returns the same envelope, so an agent that reads only the envelope still cannot
misread the payload. Three structural fields carry the honesty:

* ``scores_available`` — an **object** of ``available | gated | partial | absent``, never a
  boolean, and **derived from observed ``unblurred`` per (silo, bench)**, never from the
  ``insider_only`` schema flag alone. That flag marks a test gate-*able*; only an observed
  ``unblurred`` says whether it is gated here — and it is wrong for 16 of 28 silos.
* ``sorted_by`` — what the ordering actually used, and whether that field was gated.
* ``rank_scope`` — always ``within_bench``. There is no cross-bench "comparable" mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .auth import DATA_TIER_UNPROVEN, derive_auth_state
from .errors import RtingsError
from .htmlprobe import SessionProbe

AVAILABLE = "available"
GATED = "gated"
PARTIAL = "partial"
ABSENT = "absent"
#: "This response did not look." Distinct from ``gated`` ("we looked and it was
#: withheld") for the same reason ``coverage_unknown`` is distinct from ``not_tested``:
#: reporting ``gated`` for a surface nobody queried tells an agent it cannot see values
#: that are, on 16 of 28 silos, served outright — and that misroutes it away from a
#: category that would have answered the question.
UNKNOWN = "unknown"

RANK_SCOPE = "within_bench"


@dataclass(slots=True)
class SurfaceObservation:
    """The unblurred ratio for one surface of one ``(silo, bench)``."""

    total: int = 0
    unblurred: int = 0

    def add(self, *, unblurred: bool) -> None:
        self.total += 1
        if unblurred:
            self.unblurred += 1

    def verdict(self, *, population_exists: bool) -> tuple[str, float | None]:
        """``absent`` when the silo carries no such surface at all; ``unknown`` when it does
        but this response contains no rows of it.

        The ratio is computed over **this response's own rows** — never over the merged
        read, which retains rows from before a silo was gated and would keep reporting
        ``available`` forever after an open->gated flip.
        """
        if not population_exists:
            return ABSENT, None
        if self.total == 0:
            return UNKNOWN, None
        ratio = self.unblurred / self.total
        if ratio >= 1.0:
            return AVAILABLE, 1.0
        if ratio <= 0.0:
            return GATED, 0.0
        return PARTIAL, round(ratio, 4)


@dataclass(slots=True)
class ScoresAvailable:
    """The three keys are defined against the schema, not invented categories."""

    public_tests: SurfaceObservation = field(default_factory=SurfaceObservation)
    insider_tests: SurfaceObservation = field(default_factory=SurfaceObservation)
    usage_ratings: SurfaceObservation = field(default_factory=SurfaceObservation)
    has_public: bool = False
    has_insider: bool = False
    has_usages: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, observation, exists in (
            ("public_tests", self.public_tests, self.has_public),
            ("insider_tests", self.insider_tests, self.has_insider),
            ("usage_ratings", self.usage_ratings, self.has_usages),
        ):
            verdict, ratio = observation.verdict(population_exists=exists)
            out[key] = verdict
            if verdict == PARTIAL:
                out[f"{key}_unblurred_ratio"] = ratio
        return out


def observe_test_rows(
    scores: ScoresAvailable,
    rows: list[dict[str, Any]],
    *,
    insider_ids: set[str],
    unpublished_product_ids: set[str],
) -> None:
    """Fold one response's rows into the observation.

    Rows whose product is ``published:false`` are **excluded**: they are blurred for a
    different reason (an Early Access review, withheld from this session) and would
    otherwise make an open silo look gated.
    """
    for row in rows:
        if row.get("status") != "tested":
            continue
        product_id = row.get("product_id")
        if product_id is not None and str(product_id) in unpublished_product_ids:
            continue
        original_id = row.get("original_id")
        if original_id is None:
            continue
        target = (
            scores.insider_tests if str(original_id) in insider_ids else scores.public_tests
        )
        target.add(unblurred=bool(row.get("unblurred")))


def observe_rating_rows(
    scores: ScoresAvailable,
    rows: list[dict[str, Any]],
    *,
    unpublished_product_ids: set[str],
) -> None:
    """Usage rows carry no ``status`` and no ``insider_only`` (both verified), so only
    presence and ``unblurred`` are available here."""
    for row in rows:
        product_id = row.get("product_id")
        if product_id is not None and str(product_id) in unpublished_product_ids:
            continue
        scores.usage_ratings.add(unblurred=bool(row.get("unblurred")))


@dataclass(slots=True)
class Envelope:
    """The wire envelope. Built by every tool, declared in every ``outputSchema``."""

    data: Any = None
    session: str = "unknown"
    data_tier: str = DATA_TIER_UNPROVEN
    scores_available: dict[str, Any] | None = None
    rank_scope: str = RANK_SCOPE
    test_benches: list[dict[str, Any]] = field(default_factory=list)
    sorted_by: dict[str, Any] | None = None
    fetched_at: str | None = None
    from_cache: bool = False
    stale: bool = False
    previews_remaining: int | None = None
    source_url: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "auth_state": derive_auth_state(self.session, self.data_tier),
            "data_tier": self.data_tier,
            "session": self.session,
            "scores_available": self.scores_available,
            "rank_scope": self.rank_scope,
            "test_benches": self.test_benches,
            "sorted_by": self.sorted_by,
            "fetched_at": self.fetched_at,
            "from_cache": self.from_cache,
            "stale": self.stale,
            "previews_remaining": self.previews_remaining,
            "source_url": self.source_url,
            "warnings": self.warnings,
            "error": self.error,
            "data": self.data,
        }


def iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def error_envelope(
    exc: RtingsError, *, probe: SessionProbe | None = None, warnings: list[str] | None = None
) -> dict[str, Any]:
    """Errors are structured **values** in the envelope, never MCP protocol errors — an
    agent must be able to tell "RTINGS has no data" from "the fetch failed"."""
    return Envelope(
        data=None,
        session=probe.session if probe else "unknown",
        warnings=list(warnings or []),
        error=exc.to_envelope(),
        previews_remaining=probe.previews_remaining if probe else None,
    ).to_json()


def gated_notice(rows_status: list[str]) -> str | None:
    """A ``data.notice`` emitted only when the gated fields are entirely null, so an agent
    that ignores the envelope still cannot misread the payload."""
    if not rows_status:
        return None
    if all(
        status in {"tested_gated", "review_unpublished"} for status in rows_status
    ) and any(status == "tested_gated" for status in rows_status):
        return (
            "Every value here was measured by RTINGS and withheld server-side. They are "
            "null because this category enforces the paywall for your session — not "
            "because the products were not tested."
        )
    return None
