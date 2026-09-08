"""Live, anonymous, network-touching tests. Opt in with ``-m live``.

These are the only tests that hit RTINGS. They are anonymous by construction — no cookie is
ever configured here — and they double as the **release-time enforcement re-scan**: the
28-silo test recomputes the per-silo paywall map from what actually comes back and diffs it
against ``docs/enforcement-snapshot.json``.

**A diff there is a SPEC CHANGE, not a test failure.** Enforcement is a snapshot of RTINGS'
business decisions; it moves silently, and a stale map makes the server misdescribe what
anonymous gets — which is the product's central claim. So the 28-silo test reports the diff
and does not fail on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtings_mcp import models, services
from rtings_mcp.config import load_config
from rtings_mcp.context import Context

pytestmark = pytest.mark.live

SNAPSHOT = Path(__file__).resolve().parents[1] / "docs" / "enforcement-snapshot.json"


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    root = tmp_path_factory.mktemp("live")
    return Context.build(
        load_config(
            {
                "RTINGS_CACHE_DIR": str(root / "cache"),
                # Deliberately no RTINGS_SESSION_COOKIE: fixtures and live checks are
                # anonymous only, and must never contain unblurred member values.
                "RTINGS_CONFIG_DIR": str(root / "config"),
                "RTINGS_RATE_BURST": "5",
                "RTINGS_RATE_INTERVAL_S": "2.0",
            }
        )
    )


async def test_silos_lists_every_category(ctx):
    out = await services.rt_silos(ctx)
    models.SilosEnvelope.model_validate(out)
    assert len(out["data"]["silos"]) >= 28
    assert all(s["has_paywall"] for s in out["data"]["silos"]), (
        "has_paywall is expected to be true on every silo; if that changed, the routing "
        "story in rt_silos needs revisiting"
    )


async def test_anonymous_is_never_an_error(ctx):
    out = await services.rt_schema(ctx, "tv")
    models.SchemaEnvelope.model_validate(out)
    assert out["error"] is None
    assert out["session"] == "anonymous"


async def test_a_gated_silo_reports_gated_never_not_tested(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["11", "208"], limit=3)
    models.RatingsEnvelope.model_validate(out)
    values = [
        value
        for group in out["data"]["groups"]
        for row in group["products"]
        for value in row["tests"]
    ]
    gated = [v for v in values if v["original_id"] == "11"]
    public = [v for v in values if v["original_id"] == "208"]
    assert gated and all(v["status"] != "not_tested" for v in gated)
    assert any(v["status"] == "tested_gated" for v in gated)
    assert any(v["status"] == "tested_visible" and v["value"] for v in public)
    assert out["scores_available"]["public_tests"] == "available"


async def test_an_open_silo_serves_real_measurements_anonymously(ctx):
    schema = await ctx.repo.schema("mattress")
    bench = (await ctx.repo.bench_info("mattress")).current_id(schema)
    numeric = [
        t.original_id
        for t in schema.leaf_tests_for_bench(bench)
        if t.kind == "number" and t.insider_only
    ][:2]
    out = await services.rt_ratings(ctx, "mattress", tests=numeric, limit=3)
    models.RatingsEnvelope.model_validate(out)
    values = [
        value
        for group in out["data"]["groups"]
        for row in group["products"]
        for value in row["tests"]
    ]
    visible = [v for v in values if v["status"] == "tested_visible" and v["value"] is not None]
    assert visible, "mattress is expected to serve insider values anonymously"
    assert out["data_tier"] == "unblurred"
    # Unblurred data must never be read as an entitlement.
    assert out["auth_state"] == "anonymous"


async def test_graph_returns_selected_points_and_no_headline_scalar(ctx):
    out = await services.rt_graph(ctx, "/tv/reviews/sony/x90l-x90cl", "13907")
    models.GraphEnvelope.model_validate(out)
    assert out["data"]["points"]
    bounds = out["data"]["axis_bounds_of_served_points"] or {}
    assert "y_max" not in bounds


async def test_search_is_live_and_labels_itself(ctx):
    out = await services.rt_search(ctx, "LG C4", count=3)
    models.SearchEnvelope.model_validate(out)
    assert out["data"]["total_count"] > 0
    assert out["data"]["searched"] == "rtings live index"


async def test_recommendations_discovers_multi_segment_lists(ctx):
    out = await services.rt_recommendations(ctx, "tv")
    models.RecommendationsEnvelope.model_validate(out)
    slugs = [entry["list"] for entry in out["data"]["lists"]]
    assert slugs
    assert any("/" in slug for slug in slugs), (
        "most best-of paths are two segments; a single-segment slug rule would miss them"
    )
    picked = await services.rt_recommendations(ctx, "tv", list=slugs[0])
    models.RecommendationsEnvelope.model_validate(picked)
    assert picked["data"]["picks"]


async def test_product_review_normalizes_against_its_own_bench(ctx):
    out = await services.rt_product(ctx, "/tv/reviews/sony/x90l-x90cl")
    models.ProductEnvelope.model_validate(out)
    groups = out["data"]["results"]
    assert groups
    # Results are nested under their section, each carrying its breadcrumb once.
    assert all(g["group_id"] or g["group"] is None for g in groups)
    results = [row for g in groups for row in g["tests"]]
    assert results
    assert out["data"]["result_count"] == len(results) or out["data"].get("groups_omitted"), (
        "result_count counts the whole review; a trimmed response says so in groups_omitted"
    )
    # A legacy-bench product has 54 tests, a current-bench one 402. Joining against the
    # full silo schema would invent hundreds of false not_tested rows.
    assert all(r["status"] != "coverage_unknown" for r in results)
    assert not any(r["kind"] in {"group", "category"} for r in results)
    # The breadcrumb moved to the group; repeating it per row was ~27% of the response.
    assert not any("hierarchy" in r for r in results)


@pytest.mark.slow
async def test_enforcement_map_matches_the_committed_snapshot(ctx):
    """The release gate. A diff is a spec change: update the snapshot, RECON §11.1 and the
    README framing, and say so in the release notes."""
    # The snapshot nests the per-silo map under "silos" alongside its provenance metadata.
    # Reading the top level instead makes every lookup miss and the gate silently pass — a
    # release check that can only ever report "no drift" is worse than no check at all.
    raw = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    baseline = raw.get("silos") or {}
    assert baseline, "the enforcement snapshot has no `silos` map; the gate would be a no-op"
    observed: dict[str, bool | None] = {}
    silos = await ctx.repo.silo_index()

    for silo in sorted(silos):
        schema = await ctx.repo.schema(silo)
        bench = (await ctx.repo.bench_info(silo)).current_id(schema)
        if bench is None:
            observed[silo] = None
            continue
        sample = [
            t.original_id for t in schema.leaf_tests_for_bench(bench) if t.insider_only
        ][:12]
        if not sample:
            observed[silo] = None
            continue
        await ctx.repo.ensure_test_slices(silo, [bench], sample)
        generations = await ctx.repo.catalog(silo, [bench])
        unpublished = set(generations[bench].unpublished_ids) if bench in generations else set()
        total = unblurred = 0
        for test_id in sample:
            meta = ctx.repo.slice_meta("tests", bench, test_id, demand="anonymous")
            if meta is None:
                continue
            for row in (meta.payload or {}).get("rows", []):
                if row.get("status") != "tested":
                    continue
                if str(row.get("product_id")) in unpublished:
                    continue
                total += 1
                unblurred += bool(row.get("unblurred"))
        observed[silo] = (unblurred == 0) if total else None

    drift = []
    unscanned = []
    for silo, enforces in observed.items():
        expected = (baseline.get(silo) or {}).get("enforces_paywall")
        if enforces is None:
            unscanned.append(silo)
            continue
        if expected is None:
            drift.append(f"{silo}: NEW silo, not in the snapshot (observed={enforces})")
        elif enforces != expected:
            drift.append(f"{silo}: snapshot={expected} observed={enforces}")
    for silo in baseline:
        if silo not in observed:
            drift.append(f"{silo}: in the snapshot but no longer in RTINGS' silo list")

    enforcing = sum(1 for v in observed.values() if v is True)
    open_silos = sum(1 for v in observed.values() if v is False)
    print(f"\nenforcement re-scan: {enforcing} enforcing / {open_silos} open")
    if drift:
        print("SPEC CHANGE — the paywall map moved:\n  " + "\n  ".join(drift))
        print(
            "Update docs/enforcement-snapshot.json, RECON.md §11.1 and the README framing, "
            "then say so in the release notes. This is not a test failure."
        )
    if unscanned:
        print(f"not scanned (no insider leaf tests on the current bench): {unscanned}")
    assert enforcing + open_silos >= 28, (
        f"the re-scan only reached {enforcing + open_silos} silos; unscanned={unscanned}"
    )
