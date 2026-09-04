"""Schema parsing, including the trap that fails silently."""

from __future__ import annotations

import pytest

from rtings_mcp.errors import RtingsError
from rtings_mcp.schema import (
    parse_column_options,
    schema_from_cacheable,
    schema_to_cacheable,
)


def test_definitions_come_from_test_bench_plus_legacy(tv_schema):
    """`test_benches[].tests[]` is a REFERENCE list — `{original_id}` only. Joining the
    wrong one yields an empty schema silently."""
    assert tv_schema.test("11").name == "Native Contrast"  # current bench
    assert tv_schema.test("5").name == "Bright Room"  # legacy only
    assert len(tv_schema.tests) == 8


def test_bench_membership_comes_from_test_benches(tv_schema):
    current = {t.original_id for t in tv_schema.tests_for_bench("227")}
    legacy = {t.original_id for t in tv_schema.tests_for_bench("2")}
    assert "11" in current and "11" not in legacy
    assert "5" in legacy and "5" not in current
    assert "208" in current and "208" in legacy  # a test can live on both


def test_leaf_tests_exclude_structure_and_graph(tv_schema):
    leaves = {t.original_id for t in tv_schema.leaf_tests_for_bench("227")}
    assert "900" not in leaves  # group
    assert "13907" not in leaves  # graph is not a leaf VALUE kind
    assert {"208", "11", "12000", "555"} <= leaves


def test_hierarchy_recovers_the_positional_category(tv_schema):
    """Categories and groups both state `parent: null`; only list order links them."""
    assert tv_schema.ancestry("11") == ["Picture", "Picture Quality"]
    assert tv_schema.ancestry("900") == ["Picture"]
    assert tv_schema.ancestry("31615") == []
    assert tv_schema.test("900").derived_category_id == "31615"
    # The API's own field is untouched — the link is derived, and labelled as derived.
    assert tv_schema.test("900").parent_original_id is None


def test_insider_only_is_read_not_inferred(tv_schema):
    assert tv_schema.test("208").insider_only is False
    assert tv_schema.test("11").insider_only is True


def test_units_are_read_never_inferred(tv_schema):
    assert tv_schema.test("12000").number_display_unit == "cd/m²"
    assert tv_schema.test("208").number_display_unit is None


def test_usages_carry_no_insider_only(tv_schema):
    """Verified across 10 silos: only *test* definitions carry the flag."""
    for usage in tv_schema.usages.values():
        assert not hasattr(usage, "insider_only")


def test_graph_kind_detection(tv_schema):
    assert tv_schema.test("13907").has_graph
    assert not tv_schema.test("11").has_graph


def test_empty_schema_raises_rather_than_returning_empty():
    with pytest.raises(RtingsError) as excinfo:
        parse_column_options("tv", {"test_bench": {"tests": []}, "test_benches": []})
    assert excinfo.value.code == "payload_missing"


def test_round_trip_through_the_cacheable_form(tv_schema):
    restored = schema_from_cacheable(schema_to_cacheable(tv_schema))
    assert set(restored.tests) == set(tv_schema.tests)
    assert restored.test("12000").number_display_unit == "cd/m²"
    assert restored.tests_for_bench("227") == tv_schema.tests_for_bench("227")
    assert restored.bench("227").display_name == "v2.2"
