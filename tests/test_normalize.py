"""The safety property, tested from every direction it can fail.

An agent must never read "no member session" as "RTINGS did not test this". These tests are
the executable form of that rule: each of the seven states must be reachable, and no two of
them may collapse into the same output.
"""

from __future__ import annotations

import pytest

from rtings_mcp.normalize import (
    COVERAGE_UNKNOWN,
    NOT_APPLICABLE,
    NOT_TESTED,
    REVIEW_UNPUBLISHED,
    STATUS_VALUES,
    TESTED_GATED,
    TESTED_VISIBLE,
    UNKNOWN_ROW_STATUS,
    coerce_value,
    normalize_absent,
    normalize_rating_row,
    normalize_review_row,
    normalize_table_row,
    parse_rendered_number,
    strip_html,
)


def row(**over):
    base = {
        "original_id": "11",
        "product_id": "1",
        "status": "tested",
        "unblurred": False,
        "value": None,
        "rendered_value": None,
        "score": None,
    }
    base.update(over)
    return base


# -- the seven states are all reachable and all distinct ---------------------------


def test_all_seven_states_reachable(tv_schema):
    gated_test = tv_schema.test("11")
    public_test = tv_schema.test("208")
    seen = set()

    seen.add(normalize_table_row(row(), gated_test).status)
    seen.add(
        normalize_table_row(
            row(original_id="208", unblurred=True, value="4k"), public_test
        ).status
    )
    seen.add(normalize_table_row(row(status="na"), gated_test).status)
    seen.add(normalize_table_row(row(status="untested"), gated_test).status)
    seen.add(
        normalize_table_row(
            row(), gated_test, unpublished_product_ids={"1"}
        ).status
    )
    seen.add(normalize_absent(gated_test, product_id="1", as_of=1.0).status)
    seen.add(
        normalize_absent(gated_test, product_id="9", as_of=1.0, covered=False).status
    )
    seen.add(normalize_table_row(row(status="in_progress"), gated_test).status)

    assert seen == set(STATUS_VALUES)


def test_gated_and_not_tested_are_never_the_same_output(tv_schema):
    """The single most important assertion in the project."""
    gated = normalize_table_row(row(), tv_schema.test("11"))
    not_tested = normalize_absent(tv_schema.test("11"), product_id="1", as_of=1.0)

    assert gated.value is None and not_tested.value is None
    # ...but they are never confusable:
    assert gated.status == TESTED_GATED
    assert gated.gated is True
    assert not_tested.status == NOT_TESTED
    assert not_tested.gated is None
    assert gated.to_json() != not_tested.to_json()


# -- status is branched BEFORE unblurred, in both directions -----------------------


def test_na_with_unblurred_false_is_not_applicable_not_gated(tv_schema):
    """An `na` row is byte-identical to a gated row on `unblurred` alone."""
    out = normalize_table_row(row(status="na", unblurred=False), tv_schema.test("11"))
    assert out.status == NOT_APPLICABLE
    assert out.gated is None  # never "buy a membership" for a test that never applied


def test_na_with_unblurred_true_is_not_a_visible_null(tv_schema):
    """47% of measured `na` rows are unblurred:true — reading `unblurred` first would
    report them as 'RTINGS measured this and the answer is nothing'."""
    out = normalize_table_row(row(status="na", unblurred=True), tv_schema.test("11"))
    assert out.status == NOT_APPLICABLE
    assert out.value is None
    assert out.gated is None


@pytest.mark.parametrize("unblurred", [True, False])
def test_untested_is_not_tested_not_a_drift_alarm(tv_schema, unblurred):
    """The table path emits BOTH an absent row and an explicit `untested`."""
    out = normalize_table_row(row(status="untested", unblurred=unblurred), tv_schema.test("11"))
    assert out.status == NOT_TESTED
    assert out.warning is None  # a real state, not a schema-drift warning


def test_unrecognised_status_warns_and_never_becomes_visible(tv_schema):
    out = normalize_table_row(row(status="something_new", unblurred=True), tv_schema.test("11"))
    assert out.status == UNKNOWN_ROW_STATUS
    assert out.warning
    assert out.value is None


# -- published:false is checked before the paywall ---------------------------------


def test_unpublished_beats_gated(tv_schema):
    """An Early Access review is withheld for a different reason than the paywall, so it
    must not be reported as 'buy a membership for this category'."""
    out = normalize_table_row(row(), tv_schema.test("11"), unpublished_product_ids={"1"})
    assert out.status == REVIEW_UNPUBLISHED
    assert out.gated is None


def test_unpublished_does_not_leak_to_other_products(tv_schema):
    out = normalize_table_row(
        row(product_id="2"), tv_schema.test("11"), unpublished_product_ids={"1"}
    )
    assert out.status == TESTED_GATED


# -- gated is null, never false, when there is no value ----------------------------


def test_visible_row_with_null_value_is_gated_null(tv_schema):
    """Measured: 2 rows came back tested + unblurred:true + value:null. `gated:false`
    beside a null value is indistinguishable from a visible value that happens to be null."""
    out = normalize_table_row(
        row(unblurred=True, value=None, rendered_value=None), tv_schema.test("11")
    )
    assert out.status == TESTED_VISIBLE
    assert out.value is None
    assert out.gated is None


def test_no_state_ever_pairs_null_value_with_gated_false(tv_schema):
    cases = [
        normalize_table_row(row(), tv_schema.test("11")),
        normalize_table_row(row(status="na"), tv_schema.test("11")),
        normalize_table_row(row(status="untested"), tv_schema.test("11")),
        normalize_table_row(row(unblurred=True), tv_schema.test("11")),
        normalize_table_row(row(), tv_schema.test("11"), unpublished_product_ids={"1"}),
        normalize_absent(tv_schema.test("11"), product_id="1", as_of=1.0),
        normalize_absent(tv_schema.test("11"), product_id="1", as_of=1.0, covered=False),
    ]
    for out in cases:
        assert not (out.value is None and out.gated is False), out


# -- coverage --------------------------------------------------------------------


def test_uncovered_product_is_coverage_unknown_not_not_tested(tv_schema):
    """Skipping the coverage check turns every product newer than the cached slice into a
    false not_tested."""
    out = normalize_absent(tv_schema.test("11"), product_id="99", as_of=1.0, covered=False)
    assert out.status == COVERAGE_UNKNOWN
    assert out.warning


def test_not_tested_carries_as_of(tv_schema):
    out = normalize_absent(tv_schema.test("11"), product_id="1", as_of=1234.5)
    assert out.as_of == 1234.5


# -- coercion by declared kind ----------------------------------------------------


def test_word_values_are_never_coerced(tv_schema):
    """`word` tests hold "Yes"/"No" and numeric-looking strings alike."""
    value, warning = coerce_value(tv_schema.test("208"), "1080")
    assert value == "1080"
    assert isinstance(value, str)
    assert warning is None


def test_number_coerced_from_string_with_separators(tv_schema):
    value, warning = coerce_value(tv_schema.test("11"), "1,234.5")
    assert value == 1234.5
    assert warning is None


def test_uncoercible_number_keeps_raw_and_warns(tv_schema):
    out = normalize_table_row(
        row(unblurred=True, value="not a number"), tv_schema.test("11")
    )
    assert out.value is None
    assert out.raw_value == "not a number"
    assert out.warning
    assert out.status == TESTED_VISIBLE


def test_unit_and_precision_only_on_number_kind(tv_schema):
    number = normalize_table_row(row(unblurred=True, value="5"), tv_schema.test("11"))
    word = normalize_table_row(
        row(original_id="208", unblurred=True, value="4k"), tv_schema.test("208")
    )
    assert number.unit == ": 1"
    assert word.unit is None
    assert word.precision is None


# -- the review path is a DIFFERENT row shape -------------------------------------


def test_review_row_has_no_value_key_and_is_display_rounded(tv_schema):
    review_row = {
        "status": "tested",
        "unblurred": True,
        "rendered_value": "1,950 cd/m²",
        "score": 8.4,
        "test": {"original_id": "12000"},
    }
    assert "value" not in review_row
    out = normalize_review_row(
        review_row, tv_schema.test("12000"), product_id="1", schema=tv_schema
    )
    assert out.status == TESTED_VISIBLE
    assert out.value == 1950.0
    assert out.value_source == "rendered"  # precision loss is stated, not implied
    assert out.score == 8.4


def test_review_row_gated_never_parses_the_blur_marker(tv_schema):
    review_row = {
        "status": "tested",
        "unblurred": False,
        "rendered_value": '<span class="e-blurred">Lock</span> : 1',
        "score": None,
        "test": {"original_id": "11"},
    }
    out = normalize_review_row(review_row, tv_schema.test("11"), product_id="1")
    assert out.status == TESTED_GATED
    assert out.value is None
    assert out.display is None


def test_review_row_branches_status_first(tv_schema):
    out = normalize_review_row(
        {"status": "na", "unblurred": True, "rendered_value": None, "test": {}},
        tv_schema.test("11"),
        product_id="1",
    )
    assert out.status == NOT_APPLICABLE


def test_parse_rendered_number_never_invents_a_number(tv_schema):
    value, warning = parse_rendered_number(tv_schema.test("12000"), "<span>N/A</span>")
    assert value is None
    assert warning


def test_strip_html_flattens_and_unescapes():
    assert strip_html("<p>1,950&nbsp;cd/m&sup2;</p>") == "1,950 cd/m²"
    assert strip_html(None) is None
    assert strip_html("<span></span>") is None


# -- ratings rows carry no status -------------------------------------------------


def test_rating_row_gated_and_visible():
    gated = normalize_rating_row(
        {"original_id": "1", "product_id": "1", "score": None, "unblurred": False},
        name="Mixed Usage",
    )
    assert gated.status == TESTED_GATED and gated.gated is True

    visible = normalize_rating_row(
        {"original_id": "1", "product_id": "1", "score": 7.7, "unblurred": True},
        name="Mixed Usage",
    )
    assert visible.status == TESTED_VISIBLE and visible.score == 7.7 and visible.gated is False


def test_rating_row_unpublished_beats_gated():
    out = normalize_rating_row(
        {"original_id": "1", "product_id": "1", "score": None, "unblurred": False},
        name="Mixed Usage",
        unpublished_product_ids={"1"},
    )
    assert out.status == REVIEW_UNPUBLISHED
    assert out.gated is None


# -- Early Access is a membership perk, not a permanent blur -----------------------


def test_early_access_data_that_came_through_is_kept(tv_schema):
    """RTINGS: "the writer publishes it for Early Access so that Insiders who support us can
    see the data without any text." So a membership DOES lift this blur — and a member's real
    early-access values must never be discarded because the catalog says `published:false`.
    `unblurred` is therefore checked before the flag."""
    out = normalize_table_row(
        row(unblurred=True, value="1200"),
        tv_schema.test("11"),
        unpublished_product_ids={"1"},
    )
    assert out.status == TESTED_VISIBLE
    assert out.value == 1200.0
    assert out.gated is False


def test_early_access_withheld_from_this_session_is_its_own_state(tv_schema):
    """Still distinct from the paywall: the reason differs, and so does the remedy."""
    out = normalize_table_row(row(unblurred=False), tv_schema.test("11"),
                              unpublished_product_ids={"1"})
    assert out.status == REVIEW_UNPUBLISHED
    assert out.gated is None


def test_early_access_on_the_review_path_behaves_the_same(tv_schema):
    visible = normalize_review_row(
        {"status": "tested", "unblurred": True, "rendered_value": "4k", "test": {}},
        tv_schema.test("208"),
        product_id="1",
        unpublished=True,
    )
    assert visible.status == TESTED_VISIBLE and visible.value == "4k"

    withheld = normalize_review_row(
        {"status": "tested", "unblurred": False, "rendered_value": None, "test": {}},
        tv_schema.test("208"),
        product_id="1",
        unpublished=True,
    )
    assert withheld.status == REVIEW_UNPUBLISHED


def test_early_access_usage_rating_behaves_the_same():
    visible = normalize_rating_row(
        {"original_id": "1", "product_id": "1", "score": 8.0, "unblurred": True},
        name="Mixed Usage",
        unpublished_product_ids={"1"},
    )
    assert visible.status == TESTED_VISIBLE and visible.score == 8.0

    withheld = normalize_rating_row(
        {"original_id": "1", "product_id": "1", "score": None, "unblurred": False},
        name="Mixed Usage",
        unpublished_product_ids={"1"},
    )
    assert withheld.status == REVIEW_UNPUBLISHED and withheld.gated is None


# -- an unscored test has no score --------------------------------------------------


def test_an_unscored_test_reports_no_score(tv_schema):
    """RTINGS ships `score: 0.0` on tests it does not score; passing it through reads as
    "0 out of 10" (mattress "Mattress Type" = Foam, score 0.0)."""
    unscored = tv_schema.test("555")  # has_score: false
    assert unscored.has_score is False
    out = normalize_table_row(
        row(original_id="555", unblurred=True, value="v1.2", score=0.0), unscored
    )
    assert out.value == "v1.2"
    assert out.score is None

    scored = normalize_table_row(
        row(original_id="208", unblurred=True, value="4k", score=10.0), tv_schema.test("208")
    )
    assert scored.score == 10.0
