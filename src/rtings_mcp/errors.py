"""Error taxonomy (SPEC §7).

Errors are **structured values in the response envelope**, never MCP protocol errors: an
agent must be able to tell "RTINGS has no data" from "the fetch failed". Every code below
appears in the envelope's ``error`` field, which is ``null`` on success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- codes -------------------------------------------------------------------------------
# Lookup / input
UNKNOWN_SILO = "unknown_silo"
UNKNOWN_PRODUCT = "unknown_product"
UNKNOWN_LIST = "unknown_list"
UNKNOWN_TEST = "unknown_test"
INVALID_BENCH = "invalid_bench"

# Schema drift — loud, never a degraded empty result
PAYLOAD_MISSING = "payload_missing"
API_ERROR = "api_error"
RECOMMENDATIONS_MISSING = "recommendations_missing"

# Structural (not failures)
NO_GRAPH = "no_graph"
GRAPH_NOT_AVAILABLE = "graph_not_available"
UNKNOWN_ROW_STATUS = "unknown_row_status"
PREVIEW_EXHAUSTED = "preview_exhausted"
COVERAGE_STALE = "coverage_stale"

# Auth / credential
SESSION_EXPIRED = "session_expired"
IDENTITY_ROTATED = "identity_rotated"

# Transport
FETCH_FAILED = "fetch_failed"
RATE_LIMITED = "rate_limited"
CHALLENGED = "challenged"
COOLDOWN_ACTIVE = "cooldown_active"
CACHE_MISS_OFFLINE = "cache_miss_offline"

#: Codes that must never be written to the cache — transport and lookup failures (SPEC §8).
NEVER_CACHED = frozenset(
    {
        FETCH_FAILED,
        CHALLENGED,
        RATE_LIMITED,
        IDENTITY_ROTATED,
        UNKNOWN_PRODUCT,
        COOLDOWN_ACTIVE,
        CACHE_MISS_OFFLINE,
    }
)

#: Codes an agent may usefully retry, and roughly when.
RETRYABLE = frozenset({RATE_LIMITED, COOLDOWN_ACTIVE, COVERAGE_STALE, CACHE_MISS_OFFLINE})


@dataclass(slots=True)
class RtingsError(Exception):
    """A structured error destined for the envelope's ``error`` field.

    Raised internally and caught at the tool boundary, where it is serialized. It never
    carries a response body: a challenge page holds tokens and a member page holds profile
    data (SPEC §9). Status, reason and ``<title>`` only.
    """

    code: str
    message: str
    retry_after: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"

    def to_envelope(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.code in RETRYABLE,
        }
        if self.retry_after is not None:
            out["retry_after"] = self.retry_after
        if self.details:
            out["details"] = self.details
        return out
