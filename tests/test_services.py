"""The filter/sort guard and the graph resampler — pure functions, no network."""

from __future__ import annotations

from rtings_mcp.services import (
    _apply_filters,
    _apply_sort,
    _axis_bounds,
    _build_groups,
    _decimate,
    _featured_results,
)


class FakeInfo:
    def __init__(self, recent):
        self.recent_ids = recent


def product(pid, name, brand, released, *, contrast=None, status="tested_gated", score=None):
    return {
        "product_id": pid,
        "name": name,
        "brand": brand,
        "released_at": released,
        "published": True,
        "tests": [
            {
                "original_id": "11",
                "name": "Native Contrast",
                "status": status,
                "value": contrast,
                "gated": True if status == "tested_gated" else None,
                "score": score,
            }
        ],
        "usage_scores": [],
    }


GATED = [
    product("1", "A", "Sony", "2024-01-01"),
    product("2", "B", "LG", "2025-01-01"),
]
VISIBLE = [
    product("1", "A", "Sony", "2024-01-01", contrast=1200.0, status="tested_visible"),
    product("2", "B", "LG", "2025-01-01", contrast=900.0, status="tested_visible"),
]


# -- the guard --------------------------------------------------------------------


def test_a_filter_on_a_gated_field_is_not_applied_and_says_why(tv_schema):
    """Anonymously every gated value is null, so the filter would match zero products —
    and '0 results' reads as 'no TV is that bright', not 'you cannot see brightness'."""
    rows, warnings = _apply_filters(GATED, {"11": ">1000"}, tv_schema)
    assert len(rows) == 2, "the filter must not silently drop everything"
    assert warnings and "filter_unavailable" in warnings[0]
    assert "cannot see" in warnings[0]


def test_a_filter_on_a_populated_field_is_applied(tv_schema):
    """The guard exists for the gated categories; on the open ones filtering is a
    first-class capability, not a degraded one."""
    rows, warnings = _apply_filters(VISIBLE, {"11": ">1000"}, tv_schema)
    assert [r["product_id"] for r in rows] == ["1"]
    assert warnings == []


def test_filter_by_test_name_as_well_as_id(tv_schema):
    rows, _ = _apply_filters(VISIBLE, {"Native Contrast": "<1000"}, tv_schema)
    assert [r["product_id"] for r in rows] == ["2"]


def test_catalog_field_filters(tv_schema):
    rows, _ = _apply_filters(VISIBLE, {"brand": "sony"}, tv_schema)
    assert [r["product_id"] for r in rows] == ["1"]
    rows, _ = _apply_filters(VISIBLE, {"name_contains": "b"}, tv_schema)
    assert [r["product_id"] for r in rows] == ["2"]


def test_unknown_filter_field_warns_and_is_ignored(tv_schema):
    rows, warnings = _apply_filters(VISIBLE, {"nonexistent": ">1"}, tv_schema)
    assert len(rows) == 2
    assert warnings and "no test or usage named" in warnings[0]


def test_default_sort_is_a_public_catalog_field(tv_schema):
    rows, sorted_by, warnings = _apply_sort(GATED, None, tv_schema)
    assert sorted_by == {"field": "released_at", "gated": False, "direction": "desc"}
    assert [r["product_id"] for r in rows] == ["2", "1"]
    assert warnings == []


def test_sorting_by_a_gated_field_falls_back_and_reports_it(tv_schema):
    """An ordering that is anonymous-looking but secretly arbitrary is the quiet version of
    the same failure."""
    _rows, sorted_by, warnings = _apply_sort(GATED, "11", tv_schema)
    assert sorted_by["field"] == "released_at"
    # `gated` describes the field actually USED — saying release date is gated is nonsense.
    assert sorted_by["gated"] is False
    assert sorted_by["requested"] == "11"
    assert "gated or unpopulated" in sorted_by["fallback_reason"]
    assert warnings and "arbitrary" in warnings[0]


def test_sorting_by_a_populated_field_works_both_directions(tv_schema):
    rows, sorted_by, _ = _apply_sort(VISIBLE, "11", tv_schema)
    assert [r["product_id"] for r in rows] == ["1", "2"]
    assert sorted_by["field"] == "11"
    assert sorted_by["name"] == "Native Contrast"  # the id alone is unreadable
    assert sorted_by["gated"] is False and sorted_by["direction"] == "desc"
    rows, sorted_by, _ = _apply_sort(VISIBLE, "+11", tv_schema)
    assert [r["product_id"] for r in rows] == ["2", "1"]
    assert sorted_by["direction"] == "asc"


# -- grouping ---------------------------------------------------------------------


def test_the_recent_set_is_one_group_and_widening_adds_groups(tv_schema):
    """One response shape, always: widening adds groups rather than changing the type."""
    by_bench = {"227": [VISIBLE[0]], "2": [VISIBLE[1]]}
    ordered = [VISIBLE[0], VISIBLE[1]]

    only_recent = _build_groups(["227"], FakeInfo(["227"]), by_bench, ordered, tv_schema, 25, 0)
    assert len(only_recent) == 1 and only_recent[0]["is_recent_set"] is True

    widened = _build_groups(
        ["227", "2"], FakeInfo(["227"]), by_bench, ordered, tv_schema, 25, 0
    )
    assert len(widened) == 2
    assert widened[0]["is_recent_set"] is True
    assert widened[1]["is_recent_set"] is False
    assert widened[1]["test_benches"] == [{"id": "2", "display_name": "v0.9"}]


def test_the_limit_is_applied_within_each_group_not_across_benches(tv_schema):
    """A global sort-then-slice can starve a widened bench of every row it matched — and
    it would be ranking across the boundary `rank_scope: within_bench` exists to keep."""
    recent = [product(str(i), f"R{i}", "Sony", f"202{i}-01-01") for i in range(3)]
    legacy = [product("9", "L", "LG", "2015-01-01")]
    by_bench = {"227": recent, "2": legacy}
    ordered = recent + legacy  # the legacy row sorts last overall

    groups = _build_groups(
        ["227", "2"], FakeInfo(["227"]), by_bench, ordered, tv_schema, 2, 0
    )
    assert [p["product_id"] for p in groups[0]["products"]] == ["0", "1"]
    assert groups[0]["matched"] == 3
    # Under a flat window of 2 the legacy bench would have returned nothing at all.
    assert [p["product_id"] for p in groups[1]["products"]] == ["9"]
    assert groups[1]["matched"] == 1


def test_group_ordering_follows_the_global_sort(tv_schema):
    rows = [product(str(i), f"P{i}", "Sony", f"202{i}-01-01") for i in range(3)]
    by_bench = {"227": rows}
    ordered = [rows[2], rows[0], rows[1]]
    groups = _build_groups(["227"], FakeInfo(["227"]), by_bench, ordered, tv_schema, 25, 0)
    assert [p["product_id"] for p in groups[0]["products"]] == ["2", "0", "1"]


def test_rows_filtered_out_do_not_appear_in_any_group(tv_schema):
    rows = [product(str(i), f"P{i}", "Sony", "2024-01-01") for i in range(3)]
    by_bench = {"227": rows}
    groups = _build_groups(["227"], FakeInfo(["227"]), by_bench, [rows[1]], tv_schema, 25, 0)
    assert [p["product_id"] for p in groups[0]["products"]] == ["1"]
    assert groups[0]["matched"] == 1


# -- graph resampling -------------------------------------------------------------


def test_decimation_only_selects_points_that_were_shipped():
    """An interpolated point is a number RTINGS never measured."""
    points = [[i, i * 2.5] for i in range(1000)]
    served, resampled = _decimate(points, 100)
    assert resampled is True
    assert len(served) <= 100
    shipped = {tuple(p) for p in points}
    assert all(tuple(p) in shipped for p in served)


def test_decimation_keeps_the_first_and_last_point():
    points = [[i, i] for i in range(1000)]
    served, _ = _decimate(points, 50)
    assert served[0] == points[0]
    assert served[-1] == points[-1]


def test_a_short_series_is_returned_untouched():
    points = [[0, 1], [1, 2]]
    served, resampled = _decimate(points, 200)
    assert served == points and resampled is False


def test_axis_bounds_describe_the_served_points_only():
    """A curve maximum would be the gated scalar in all but name."""
    bounds = _axis_bounds([[0, 5], [10, 900]])
    assert bounds == {"x_min": 0, "x_max": 10}
    assert "y_max" not in bounds
    assert _axis_bounds([]) is None


# -- recommendation rows ----------------------------------------------------------


def test_recommendation_rows_keep_the_seven_state_honesty_without_a_schema_join():
    """Those rows carry an inline `test` stub with no `original_id`, so they cannot be
    joined to the schema — but they still must not report a gated value as untested."""
    rows = [
        {"status": "tested", "unblurred": False, "rendered_value": None,
         "test": {"name": "Peak Brightness", "kind": "number", "insider_only": True}},
        {"status": "tested", "unblurred": True, "rendered_value": "<b>4k</b>", "score": 10.0,
         "test": {"name": "Resolution", "kind": "word", "insider_only": False}},
        {"status": "na", "unblurred": True, "rendered_value": None,
         "test": {"name": "3D", "kind": "word", "insider_only": True}},
    ]
    out = _featured_results(rows)
    assert out[0]["status"] == "tested_gated" and out[0]["gated"] is True
    assert out[1]["status"] == "tested_visible" and out[1]["display"] == "4k"
    assert out[2]["status"] == "not_applicable"
    for entry in out:
        assert not (entry.get("display") is None and entry.get("gated") is False)
