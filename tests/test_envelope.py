"""scores_available and the response envelope."""

from __future__ import annotations

from rtings_mcp.envelope import (
    ABSENT,
    AVAILABLE,
    GATED,
    PARTIAL,
    UNKNOWN,
    Envelope,
    ScoresAvailable,
    error_envelope,
    gated_notice,
    iso,
    observe_rating_rows,
    observe_test_rows,
)
from rtings_mcp.errors import PAYLOAD_MISSING, RATE_LIMITED, RtingsError

INSIDER = {"11", "12000"}


def scores():
    return ScoresAvailable(has_public=True, has_insider=True, has_usages=True)


def row(original_id, unblurred, *, status="tested", product_id="1"):
    return {
        "original_id": original_id,
        "unblurred": unblurred,
        "status": status,
        "product_id": product_id,
    }


def test_gated_when_no_insider_row_came_back_unblurred():
    s = scores()
    observe_test_rows(
        s,
        [row("11", False), row("208", True)],
        insider_ids=INSIDER,
        unpublished_product_ids=set(),
    )
    out = s.to_json()
    assert out["insider_tests"] == GATED
    assert out["public_tests"] == AVAILABLE


def test_available_when_every_insider_row_is_unblurred():
    """16 of 28 silos: the flag marks a test gate-able, only an observation says gated."""
    s = scores()
    observe_test_rows(
        s,
        [row("11", True), row("12000", True)],
        insider_ids=INSIDER,
        unpublished_product_ids=set(),
    )
    assert s.to_json()["insider_tests"] == AVAILABLE


def test_partial_carries_the_ratio_and_is_not_reported_as_available():
    """A gift or preview unblurs 1 product of 97; 'any unblurred' would call that
    available while 96 rows stay gated."""
    s = scores()
    rows = [row("11", False, product_id=str(i)) for i in range(9)] + [
        row("11", True, product_id="9")
    ]
    observe_test_rows(s, rows, insider_ids=INSIDER, unpublished_product_ids=set())
    out = s.to_json()
    assert out["insider_tests"] == PARTIAL
    assert out["insider_tests_unblurred_ratio"] == 0.1


def test_unpublished_rows_are_excluded_from_the_boundary():
    """One unfinished review would otherwise make an open silo look gated."""
    s = scores()
    rows = [row("11", False, product_id="99"), row("11", True, product_id="1")]
    observe_test_rows(s, rows, insider_ids=INSIDER, unpublished_product_ids={"99"})
    assert s.to_json()["insider_tests"] == AVAILABLE


def test_non_tested_rows_are_excluded():
    s = scores()
    observe_test_rows(
        s,
        [row("11", False, status="na"), row("11", True)],
        insider_ids=INSIDER,
        unpublished_product_ids=set(),
    )
    assert s.to_json()["insider_tests"] == AVAILABLE


def test_unknown_when_the_surface_was_not_queried():
    """Reporting `gated` for a surface nobody asked about would tell an agent it cannot see
    values that are, on most silos, served outright."""
    assert scores().to_json()["insider_tests"] == UNKNOWN


def test_absent_when_the_silo_carries_no_such_surface():
    s = ScoresAvailable(has_public=True, has_insider=False, has_usages=False)
    out = s.to_json()
    assert out["insider_tests"] == ABSENT
    assert out["usage_ratings"] == ABSENT


def test_usage_rows_have_no_status_to_branch_on():
    s = scores()
    observe_rating_rows(
        s,
        [{"product_id": "1", "unblurred": True, "score": 7.7}],
        unpublished_product_ids=set(),
    )
    assert s.to_json()["usage_ratings"] == AVAILABLE


# -- envelope ---------------------------------------------------------------------


def test_auth_state_is_derived_not_stored():
    out = Envelope(session="member", data_tier="unproven").to_json()
    assert out["auth_state"] == "member"
    assert out["session"] == "member"
    assert out["data_tier"] == "unproven"


def test_rank_scope_is_always_within_bench():
    assert Envelope().to_json()["rank_scope"] == "within_bench"


def test_error_is_a_value_in_the_response_not_an_exception():
    out = error_envelope(RtingsError(PAYLOAD_MISSING, "shape changed"))
    assert out["error"]["code"] == "payload_missing"
    assert out["error"]["retryable"] is False
    assert out["data"] is None


def test_retryable_errors_carry_retry_after():
    out = error_envelope(RtingsError(RATE_LIMITED, "429", retry_after=30.0))
    assert out["error"]["retryable"] is True
    assert out["error"]["retry_after"] == 30.0


def test_notice_only_when_everything_is_gated():
    assert gated_notice(["tested_gated", "tested_gated"])
    assert gated_notice(["tested_gated", "tested_visible"]) is None
    assert gated_notice(["not_tested"]) is None
    assert gated_notice([]) is None


def test_iso_formats_utc():
    assert iso(0) == "1970-01-01T00:00:00Z"
    assert iso(None) is None
