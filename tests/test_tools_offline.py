"""The seven tool bodies, offline, against a stub transport.

The live tests prove the tools work against RTINGS; these prove they behave correctly on
inputs RTINGS rarely produces on demand — a product that postdates a cached slice, a
response with nothing to say about a surface, a filter on a field whose value is null.
"""

from __future__ import annotations

import html as html_module
import json
from pathlib import Path

import pytest

from rtings_mcp import services
from rtings_mcp.auth import AuthManager
from rtings_mcp.cache import Cache
from rtings_mcp.config import load_config
from rtings_mcp.context import Context
from rtings_mcp.errors import RtingsError
from rtings_mcp.http import FetchResult, Transport, _count_request
from rtings_mcp.repository import Repository

FIXTURES = Path(__file__).parent / "fixtures"

def _props(payload: dict) -> str:
    """A `data-props` attribute exactly as RTINGS emits it — HTML-escaped JSON.

    Built rather than hand-written: the escaping is the thing under test (a raw-text regex
    silently never matches these), so it must be real, and a 1,000-character escaped literal
    is unreadable.
    """
    return f'<div data-props="{html_module.escape(json.dumps(payload), quote=True)}"></div>'


SILO_LAYOUT_PROPS = _props(
    {
        "has_insider_access": False,
        "silo_layout": {
            "best": [
                {
                    "title": "Best TVs",
                    "url": "/tv/reviews/best/tvs-on-the-market",
                    "short": "Best TVs",
                },
                {
                    "title": "Best 65-Inch",
                    "url": "/tv/reviews/best/by-size/65-inch",
                    "short": "65-Inch",
                },
            ]
        },
    }
)

REC_PROPS = _props(
    {
        "title": "The 7 Best TVs",
        "page_data": {
            "page": {
                "url": "/tv/reviews/best/tvs-on-the-market",
                "recommendation": {
                    "seasonal_title": "The 7 Best TVs",
                    "introduction": "intro",
                    "product_recommendations": [
                        {
                            "title": "Best TV",
                            "subtitle": "sub",
                            "description": "why",
                            "product_id": "1",
                            "product": {
                                "id": "1",
                                "fullname": "Alpha One",
                                "preferred_scoreset_score": 9,
                                "page": {"url": "/tv/reviews/alpha/one"},
                            },
                            "featured_test_results": [
                                {
                                    "status": "tested",
                                    "unblurred": False,
                                    "rendered_value": None,
                                    "test": {
                                        "name": "Native Contrast",
                                        "kind": "number",
                                        "insider_only": True,
                                    },
                                }
                            ],
                            "ratings": [
                                {
                                    "score": None,
                                    "unblurred": False,
                                    "usage": {"original_id": "1", "name": "Mixed Usage"},
                                }
                            ],
                        }
                    ],
                },
            }
        },
    }
)

PAGE_HTML = f"""
<html><head><title>TV Table Tool - RTINGS.com</title></head><body>
<script>var GLOBALS = {{"session": {{"current_user": null, "access_state":
 {{"access_level": 1, "preview_level": 2, "access_limit": null, "previewed_products": []}}}},
 "static": {{"silos": [{{"url_part": "tv", "name": "TV", "has_paywall": true,
   "silo_group": "home-entertainment", "review_count": 3,
   "first_published_at": "2011-11-22", "tool_pages": [{{"url": "/tv/tools/table"}}],
   "test_bench": {{"id": "227", "name": "v2.2"}}}}],
  "silo": {{"latest_test_bench_id": "227", "test_benches":
   [{{"id": "227", "is_recent": true, "major": false}},
    {{"id": "2", "is_recent": false, "major": false}}]}}}}}};</script>
<a href="/tv/reviews/best/tvs-on-the-market">Best TVs</a>
{SILO_LAYOUT_PROPS}
</body></html>
"""

#: The member shape is a GUESS — no logged-in `GLOBALS.session` has ever been measured
#: (Phase-0 capture a). It encodes the two signals `classify_session` actually keys on:
#: `has_insider_access: true` and `access_level > preview_level`. If the real shape differs,
#: `RTINGS_SESSION_OVERRIDE` is the escape hatch, which is why that has its own test.
MEMBER_HTML_MARKER = "MEMBER"

#: The same page as a logged-in free account would see it. Values are placeholders — a real
#: `current_user` carries the user's name and email, and no fixture in this repo may.
FREE_HTML = PAGE_HTML.replace(
    '"current_user": null, "access_state":\n {"access_level": 1, "preview_level": 2, '
    '"access_limit": null, "previewed_products": []}',
    '"current_user": {"id": "REDACTED"}, "access_state":\n {"access_level": 2, '
    '"preview_level": 2, "access_limit": 1, "previewed_products": []}',
)

MEMBER_HTML = (
    PAGE_HTML.replace(
        '"current_user": null, "access_state":\n {"access_level": 1, "preview_level": 2, '
        '"access_limit": null, "previewed_products": []}',
        '"current_user": {"id": "REDACTED"}, "access_state":\n {"access_level": 3, '
        '"preview_level": 2, "access_limit": null, "previewed_products": []}',
    ).replace(
        html_module.escape('"has_insider_access": false', quote=True),
        html_module.escape('"has_insider_access": true', quote=True),
    )
)

REC_HTML = f"""
<html><head><title>The 7 Best TVs - RTINGS.com</title></head><body>
{REC_PROPS}
</body></html>
"""


def product(
    pid, name, bench="227", published=True, released="2024-01-01", url=None,
    tested='65"',
):
    return {
        "id": pid,
        "fullname": name,
        "brand_name": "Alpha",
        "published": published,
        "approximate_released_at": released,
        "review": {"test_bench": {"id": bench, "display_name": "v2.2"}},
        "page": {"url": url or f"/tv/reviews/alpha/{name.lower().replace(' ', '-')}"},
        "image": None,
        "reviewed_sku_id": "s-" + pid,
        "variant_skus": [
            {"id": "s-" + pid, "name": name, "variation": tested},
            {"id": "other-" + pid, "name": name, "variation": '75"'},
        ],
    }


def make_test_row(pid, original_id, *, unblurred=False, value=None, status="tested", score=None):
    return {
        "product_id": pid,
        "original_id": original_id,
        "status": status,
        "unblurred": unblurred,
        "value": value,
        "rendered_value": value,
        "score": score,
    }


def rating_row(pid, usage_id, *, unblurred=False, score=None):
    return {
        "product_id": pid,
        "original_id": usage_id,
        "score": score,
        "suitable": True,
        "unblurred": unblurred,
        "usage": {"kind": "usage", "is_unscored": False},
    }


class StubTransport(Transport):
    """Serves canned API payloads. Records every call so a test can assert cache-first."""

    def __init__(self, config, payloads):
        super().__init__(config)
        self.payloads = payloads
        self.calls: list[str] = []
        #: What the probe page should say. `_fetch_slice_chunk` re-probes (unthrottled)
        #: before any tier-keyed write, so an injected probe never survives — the page has
        #: to say it, exactly as it would in production. `None` means "auto": logged-out,
        #: or free once a credential is configured.
        self.session_page: str | None = None
        #: What `_rtings_session` the jar holds. RTINGS re-issues it on every response, so
        #: the real one is almost never the pasted one.
        self.jar_cookie: str | None = None

    async def api_post(self, query, body, *, referer=None, browser_headers=True):
        self.calls.append(query)
        # The real transport counts every request it sends; `from_cache` reads that counter.
        # A stub that skips it makes a test of that field pass against the double.
        _count_request()
        value = self.payloads.get(query)
        if value is None:
            raise RtingsError("fetch_failed", f"no stub for {query}")
        if isinstance(value, Exception):
            raise value
        payload = value(body) if callable(value) else value
        # Mirror the real transport's contract exactly, or a test of that contract passes
        # against the stub rather than against the code.
        if isinstance(payload, dict) and payload.get("errors") and payload.get("data") is None:
            raise RtingsError("api_error", f"{query} returned errors[] and no data")
        return payload

    async def api_get_html(self, path):
        self.calls.append(f"GET {path}")
        _count_request()
        if "/reviews/best/" in path:
            html = REC_HTML
        elif self.session_page == "member":
            html = MEMBER_HTML
        elif self.session_page == "anonymous":
            html = PAGE_HTML  # a configured cookie that came back logged out
        elif self.session_page == "free" or self.credential.present:
            html = FREE_HTML
        else:
            html = PAGE_HTML
        return FetchResult(
            status=200,
            text=html,
            headers={},
            url=f"https://www.rtings.com{path}",
            elapsed=0.0,
            from_host="www.rtings.com",
        )

    async def current_jar_cookie(self):
        return self.jar_cookie or self.credential.configured

    async def cdn_get_json(self, path):
        self.calls.append(f"CDN {path}")
        _count_request()
        return {"header": ["x", "y"], "data": [[i, i * 2] for i in range(500)]}


@pytest.fixture
def payloads():
    column_options = json.loads((FIXTURES / "column_options_min.json").read_text())
    products = [
        product("1", "Alpha One"),
        product("2", "Alpha Two", tested='55"'),
        product(
            "3",
            "Alpha Draft",
            published=False,
            released="2026-01-01",
            url="/early-access/tv/reviews/alpha/alpha-draft",
        ),
    ]
    return {
        "table_tool__column_options": {"data": {"silo": column_options}},
        "table_tool__products_list": {"data": {"products": products}},
        "table_tool__test_results": {
            "data": {
                "test_results": [
                    make_test_row("1", "208", unblurred=True, value="4k", score=10.0),
                    make_test_row("2", "208", unblurred=True, value="1080p", score=6.0),
                    make_test_row("3", "208"),  # published:false -> blurred for everyone
                    make_test_row("99", "208", unblurred=True, value="8k", score=9.0),
                    make_test_row("1", "11"),
                    make_test_row("2", "11"),
                    make_test_row("3", "11"),
                ]
            }
        },
        "table_tool__ratings": {
            "data": {
                "ratings": [
                    rating_row("1", "1", unblurred=True, score=8.1),
                    rating_row("2", "1"),
                ]
            }
        },
        "app/search__search_results": {
            "data": {
                "search_results": {
                    "query": "alpha",
                    "total_count": 1,
                    "results": [
                        {
                            "kind": "page",
                            "title": "Alpha One TV Review",
                            "url": "/tv/reviews/alpha/alpha-one",
                            "product_id": "1",
                        }
                    ],
                }
            }
        },
        "graph_tool__product_graph_data_url": {
            "data": {
                "product": {
                    "review": {"test_results": [{"graph_data_url": "/assets/x/graph.json"}]}
                }
            }
        },
        "app/product_vue_page__page_body": {
            "data": {
                "page": {
                    "product": {
                        "id": "1",
                        "fullname": "Alpha One",
                        "review": {
                            "test_bench": {"id": "227", "display_name": "v2.2"},
                            "compared_summary_linked": "<p>summary</p>",
                            "test_results": [
                                {
                                    "status": "tested",
                                    "unblurred": True,
                                    "rendered_value": "4k",
                                    "score": 10.0,
                                    "test": {"original_id": "208"},
                                },
                                {
                                    "status": "tested",
                                    "unblurred": False,
                                    "rendered_value": '<span class="e-blurred">Lock</span>',
                                    "test": {"original_id": "11"},
                                },
                                {
                                    "status": "tested",
                                    "unblurred": False,
                                    "rendered_value": None,
                                    "linked_description": "<p>prose about design</p>",
                                    "test": {"original_id": "900"},
                                },
                                {
                                    "status": "na",
                                    "unblurred": True,
                                    "rendered_value": None,
                                    "test": {"original_id": "555"},
                                },
                            ],
                        },
                    }
                }
            }
        },
    }


@pytest.fixture
def member_ctx(tmp_path, payloads):
    """The same server with RTINGS_MEMBER_MODE on — the Phase-0 switch."""
    config = load_config(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "m"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "mcfg"),
            "RTINGS_MEMBER_MODE": "1",
        }
    )
    cache = Cache(config)
    transport = StubTransport(config, payloads)
    auth = AuthManager(config=config, cache=cache, transport=transport)
    return Context(
        config=config,
        cache=cache,
        transport=transport,
        auth=auth,
        repo=Repository(config, cache, transport, auth),
    )


@pytest.fixture
def ctx(tmp_path, payloads):
    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CONFIG_DIR": str(tmp_path / "cfg")}
    )
    cache = Cache(config)
    transport = StubTransport(config, payloads)
    auth = AuthManager(config=config, cache=cache, transport=transport)
    repo = Repository(config, cache, transport, auth)
    return Context(config=config, cache=cache, transport=transport, auth=auth, repo=repo)


def values(out, product_id):
    for group in out["data"]["groups"]:
        for row in group["products"]:
            if row["product_id"] == product_id:
                return row
    raise AssertionError(f"product {product_id} not in the response")


# -- rt_silos ----------------------------------------------------------------------


async def test_rt_silos_reports_unknown_before_anything_is_fetched(ctx):
    out = await services.rt_silos(ctx)
    assert out["error"] is None
    assert out["session"] == "anonymous"
    assert out["data"]["silos"][0]["data_completeness"] == "unknown"


async def test_rt_silos_reports_gated_after_a_gated_fetch(ctx):
    await services.rt_ratings(ctx, "tv", tests=["11"])
    out = await services.rt_silos(ctx)
    assert out["data"]["silos"][0]["data_completeness"] == "gated"


# -- rt_ratings ---------------------------------------------------------------------


async def test_public_and_gated_values_are_never_confusable(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["208", "11"])
    row = values(out, "1")
    by_id = {t["original_id"]: t for t in row["tests"]}
    assert by_id["208"]["status"] == "tested_visible" and by_id["208"]["value"] == "4k"
    assert by_id["11"]["status"] == "tested_gated" and by_id["11"]["gated"] is True
    assert by_id["11"]["value"] is None
    # `usages` defaults to the bench's usages, so that surface is observed too.
    assert out["scores_available"]["public_tests"] == "available"
    assert out["scores_available"]["insider_tests"] == "gated"


async def test_a_surface_this_call_did_not_query_is_unknown_not_gated(ctx):
    """Reporting `gated` for a surface nobody asked about routes an agent away from a
    category that would have answered the question."""
    out = await services.rt_ratings(ctx, "tv", usages=[])
    assert out["scores_available"]["insider_tests"] == "unknown"
    assert out["scores_available"]["public_tests"] == "unknown"
    assert out["scores_available"]["usage_ratings"] == "unknown"


async def test_an_unpublished_product_is_never_reported_as_paywalled(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["208", "11"])
    row = values(out, "3")
    assert row["published"] is False
    for value in row["tests"]:
        assert value["status"] == "review_unpublished"
        assert value["gated"] is None


async def test_usage_scores_outside_the_cached_coverage_are_not_reported_as_untested(ctx):
    """The bug this file exists for: a product that postdates the cached ratings slice must
    be `coverage_unknown`, never `not_tested`."""
    out = await services.rt_ratings(ctx, "tv", usages=["1"])
    # Products 1 and 2 have rating rows; product 3 is in the catalog but has no rating row.
    covered = {u["status"] for u in values(out, "3")["usage_scores"]}
    assert covered == {"not_tested"}, "in-coverage absence is a real not_tested"

    # Now simulate a product the ratings slice never covered.
    from rtings_mcp.services import _usage_json

    schema = await ctx.repo.schema("tv")
    meta = ctx.repo.slice_meta("ratings", "227", "1", demand="anonymous")
    entry = _usage_json(schema, "1", {}, meta, set(), "999")
    assert entry["status"] == "coverage_unknown"
    assert entry["warning"]


async def test_usage_scores_normalize_gated_and_visible(ctx):
    out = await services.rt_ratings(ctx, "tv", usages=["1"])
    assert values(out, "1")["usage_scores"][0]["score"] == 8.1
    assert values(out, "2")["usage_scores"][0]["status"] == "tested_gated"
    assert out["scores_available"]["usage_ratings"] == "partial"


async def test_the_default_sort_is_public_and_pagination_is_reported(ctx):
    out = await services.rt_ratings(ctx, "tv", limit=1)
    assert out["sorted_by"] == {"field": "released_at", "gated": False, "direction": "desc"}
    assert out["data"]["returned"] == 1
    assert out["data"]["total_matched"] == 3


async def test_a_second_call_is_served_from_cache(ctx):
    await services.rt_ratings(ctx, "tv", tests=["208"])
    before = len(ctx.transport.calls)
    await services.rt_ratings(ctx, "tv", tests=["208"])
    assert len(ctx.transport.calls) == before, "a cache hit must not touch the network"


async def test_unknown_silo_is_a_structured_error(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_ratings(ctx, "not-a-silo")
    assert excinfo.value.code == "unknown_silo"


async def test_a_score_is_never_substituted_for_a_missing_value(ctx):
    """A test's 0-10 score must not stand in for its measurement: `contrast < 10` matching
    a TV whose *score* is 8.5 is confidently wrong, not merely empty."""
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                # visible, but genuinely empty — with a score attached
                make_test_row("1", "11", unblurred=True, value=None, score=8.5),
                make_test_row("2", "11", unblurred=True, value="5000", score=9.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], filters={"11": "<10"})
    matched = [
        row["product_id"] for group in out["data"]["groups"] for row in group["products"]
    ]
    assert matched == [], "the null-valued row must not match on its score"

    out = await services.rt_ratings(ctx, "tv", tests=["11"], filters={"11": ">1000"})
    matched = [
        row["product_id"] for group in out["data"]["groups"] for row in group["products"]
    ]
    assert matched == ["2"]


# -- rt_schema ----------------------------------------------------------------------


async def test_rt_schema_returns_a_bounded_tree_then_a_group(ctx):
    out = await services.rt_schema(ctx, "tv")
    assert out["data"]["test_count"] == 7
    # The category is the top level; the group hangs beneath it.
    assert out["data"]["groups"][0]["original_id"] == "31615"
    assert out["data"]["groups"][0]["children"][0]["original_id"] == "900"
    assert out["data"]["groups"][0]["children"][0]["leaf_test_count"] == 4

    detail = await services.rt_schema(ctx, "tv", group="900")
    ids = {t["original_id"] for t in detail["data"]["tests"]}
    assert ids == {"208", "11", "12000", "555", "13907"}
    assert all("status" not in t for t in detail["data"]["tests"])


async def test_rt_schema_reports_the_schemas_own_age(ctx):
    """A 29-day-old cached schema reported as freshly fetched is the quiet dishonesty the
    envelope exists to prevent."""
    first = await services.rt_schema(ctx, "tv")
    assert first["from_cache"] is False
    assert first["stale"] is False
    assert first["fetched_at"]


async def test_a_bench_with_no_published_schema_is_refused_honestly(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_schema(ctx, "tv", bench="2222222")
    assert excinfo.value.code == "invalid_bench"


# -- rt_product ---------------------------------------------------------------------


async def test_rt_product_scopes_to_the_products_own_bench(ctx):
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_prose=True)
    data = out["data"]
    assert data["product"]["test_bench"]["id"] == "227"
    by_name = {v["name"]: v for v in data["results"]}
    assert by_name["Resolution"]["status"] == "tested_visible"
    assert by_name["Resolution"]["value_source"] == "rendered"
    assert by_name["Native Contrast"]["status"] == "tested_gated"
    assert by_name["Firmware"]["status"] == "not_applicable"
    # Bench 227 carries 12000 too; the review body omitted it, so it is a real not_tested.
    assert by_name["Peak Brightness"]["status"] == "not_tested"
    # A structure row must never be a result...
    assert "Picture Quality" not in by_name
    # ...but its prose is not discarded.
    assert data["commentary"][0]["name"] == "Picture Quality"
    assert "prose about design" in data["commentary"][0]["text"]


async def test_rt_product_resolves_a_bare_search_term(ctx):
    out = await services.rt_product(ctx, "Alpha One")
    assert out["data"]["product"]["product_id"] == "1"


async def test_rt_product_refuses_an_unknown_product(ctx):
    ctx.transport.payloads["app/search__search_results"] = {
        "data": {"search_results": {"total_count": 0, "results": []}}
    }
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_product(ctx, "nothing like this exists")
    assert excinfo.value.code == "unknown_product"


# -- rt_graph -----------------------------------------------------------------------


async def test_rt_graph_selects_shipped_points_and_reports_no_headline_scalar(ctx):
    out = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    data = out["data"]
    assert data["n_points_shipped"] == 500
    assert data["n_points"] <= 200 and data["resampled"] is True
    shipped = {(i, i * 2) for i in range(500)}
    assert all(tuple(p) in shipped for p in data["points"])
    assert set(data["axis_bounds_of_served_points"]) == {"x_min", "x_max"}


async def test_rt_graph_refuses_a_non_graph_test_structurally(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "11")
    assert excinfo.value.code == "no_graph"


async def test_rt_graph_reports_a_product_with_no_curve_separately(ctx):
    ctx.transport.payloads["graph_tool__product_graph_data_url"] = {
        "data": {"product": {"review": {"test_results": []}}}
    }
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    assert excinfo.value.code == "graph_not_available"


async def test_rt_graph_can_be_disabled_by_config(tmp_path, payloads):
    config = load_config(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "c2"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "cfg2"),
            "RTINGS_ENABLE_GRAPH": "false",
        }
    )
    cache = Cache(config)
    transport = StubTransport(config, payloads)
    auth = AuthManager(config=config, cache=cache, transport=transport)
    ctx = Context(
        config=config,
        cache=cache,
        transport=transport,
        auth=auth,
        repo=Repository(config, cache, transport, auth),
    )
    with pytest.raises(RtingsError):
        await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")


# -- rt_search / rt_recommendations --------------------------------------------------


async def test_rt_search_labels_its_index(ctx):
    out = await services.rt_search(ctx, "alpha")
    assert out["data"]["searched"] == "rtings live index"
    assert out["data"]["results"][0]["silo"] == "tv"


async def test_rt_recommendations_lists_then_ranks(ctx):
    index = await services.rt_recommendations(ctx, "tv")
    slugs = [entry["list"] for entry in index["data"]["lists"]]
    assert "tvs-on-the-market" in slugs
    assert "by-size/65-inch" in slugs  # multi-segment paths must survive validation

    ranked = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    pick = ranked["data"]["picks"][0]
    assert pick["rank"] == 1 and pick["name"] == "Alpha One"
    assert pick["overall_score"] == 9
    assert pick["featured_results"][0]["status"] == "tested_gated"
    assert pick["usage_scores"][0]["status"] == "tested_gated"


async def test_an_undiscovered_list_is_refused(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_recommendations(ctx, "tv", list="made-up")
    assert excinfo.value.code == "unknown_product"


# -- the preview budget --------------------------------------------------------------


def member_probe(ctx):
    from rtings_mcp.htmlprobe import SessionProbe

    ctx.transport.credential.configured = "PLACEHOLDER"
    ctx.transport.session_page = "member"
    ctx.auth._probe = SessionProbe(
        session="member",
        logged_in=True,
        access_level=3,
        preview_level=2,
        access_limit=None,
        previewed_products=[],
        has_insider_access=True,
        probed_at=9e12,
        source_url="/tv/tools/table",
    )


def free_probe(ctx, *, previewed=(), limit=1):
    """A free session needs a configured credential: without one the server correctly
    resolves to `anonymous` without a probe, and anonymous has no previews to spend."""
    from rtings_mcp.htmlprobe import SessionProbe

    ctx.transport.credential.configured = "PLACEHOLDER"
    ctx.auth._probe = SessionProbe(
        session="free",
        logged_in=True,
        access_level=2,
        preview_level=2,
        access_limit=limit,
        previewed_products=list(previewed),
        has_insider_access=False,
        probed_at=9e12,  # far future so the TTL never triggers a real probe
        source_url="/tv/tools/table",
    )


async def test_a_free_session_refuses_to_spend_a_preview_by_default(ctx):
    free_probe(ctx)
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert excinfo.value.code == "preview_exhausted"
    assert "consume_preview" in excinfo.value.message
    assert "app/product_vue_page__page_body" not in ctx.transport.calls


async def test_an_explicit_opt_in_spends_exactly_one(ctx):
    free_probe(ctx)
    await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", consume_preview=True)
    assert ctx.transport.calls.count("app/product_vue_page__page_body") == 1
    assert ctx.repo._previews_spent == 1


async def test_the_process_budget_is_not_beaten_by_two_products(ctx):
    """The per-product lock cannot bound a per-process budget: two calls for two different
    products take different locks and would both pass a budget of 1."""
    import asyncio

    free_probe(ctx)
    results = await asyncio.gather(
        services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", consume_preview=True),
        services.rt_product(ctx, "/tv/reviews/alpha/alpha-two", consume_preview=True),
        return_exceptions=True,
    )
    spent = ctx.transport.calls.count("app/product_vue_page__page_body")
    assert spent == 1, f"spent {spent} previews against a budget of 1"
    refused = [r for r in results if isinstance(r, RtingsError)]
    assert len(refused) == 1 and refused[0].code == "preview_exhausted"


async def test_an_already_previewed_product_costs_nothing(ctx):
    free_probe(ctx, previewed=["1"])
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert out["error"] is None
    assert ctx.transport.calls.count("app/product_vue_page__page_body") == 1


async def test_an_anonymous_session_has_nothing_to_spend(ctx):
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert out["error"] is None
    assert out["previews_remaining"] is None


async def test_a_failed_fetch_releases_the_reservation(ctx):
    free_probe(ctx, limit=5)
    ctx.transport.payloads["app/product_vue_page__page_body"] = RtingsError(
        "fetch_failed", "boom"
    )
    with pytest.raises(RtingsError):
        await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", consume_preview=True)
    assert ctx.repo._previews_spent == 0, "a request that never landed must not be charged"


# -- serving stale rather than failing ------------------------------------------------


async def test_a_failed_refresh_serves_the_cached_slice_with_stale_true(ctx, monkeypatch):
    """A cached row is served with error:null whenever one exists, even past TTL and even
    when the refetch failed. `error` is non-null only when there is nothing to return."""
    import time as time_module

    first = await services.rt_ratings(ctx, "tv", tests=["208"])
    assert first["stale"] is False
    assert values(first, "1")["tests"][0]["value"] == "4k"

    # Push every cached slice past its TTL, then break the network.
    real_time = time_module.time
    monkeypatch.setattr(
        "rtings_mcp.cache.time.time", lambda: real_time() + 40 * 86_400
    )
    ctx.transport.payloads["table_tool__test_results"] = RtingsError("fetch_failed", "boom")
    ctx.transport.payloads["table_tool__products_list"] = RtingsError("fetch_failed", "boom")

    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    assert out["error"] is None, "a good cached slice must not be thrown away"
    assert out["stale"] is True
    assert any("refresh_failed" in w for w in out["warnings"])
    assert values(out, "1")["tests"][0]["value"] == "4k"


async def test_a_failed_fetch_with_nothing_cached_is_a_real_error(ctx):
    ctx.transport.payloads["table_tool__test_results"] = RtingsError("fetch_failed", "boom")
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_ratings(ctx, "tv", tests=["208"])
    assert excinfo.value.code == "fetch_failed"


async def test_a_partly_cached_batch_degrades_per_pair_not_all_or_nothing(ctx, monkeypatch):
    """One chunk mixes up to 60 ids. A single never-fetched test alongside cached-but-stale
    ones must not fail the whole chunk — the cached answers are sitting right there."""
    import time as time_module

    await services.rt_ratings(ctx, "tv", tests=["208"])  # 208 is now cached

    real_time = time_module.time
    monkeypatch.setattr("rtings_mcp.cache.time.time", lambda: real_time() + 40 * 86_400)
    ctx.transport.payloads["table_tool__test_results"] = RtingsError("fetch_failed", "boom")
    ctx.transport.payloads["table_tool__products_list"] = RtingsError("fetch_failed", "boom")

    out = await services.rt_ratings(ctx, "tv", tests=["208", "11"])
    assert out["error"] is None
    by_id = {t["original_id"]: t for t in values(out, "1")["tests"]}
    assert by_id["208"]["value"] == "4k", "the cached answer must still be served"
    # ...and the one that was never fetched is an honest "I do not know", never not_tested.
    assert by_id["11"]["status"] == "coverage_unknown"


async def test_a_bench_whose_catalog_is_unavailable_is_not_reported_as_empty(ctx, monkeypatch):
    """An ordinary empty group is indistinguishable from 'nothing matched your filter',
    which hides missing data behind a plausible answer."""
    import time as time_module

    await services.rt_ratings(ctx, "tv", tests=["208"])

    real_time = time_module.time
    monkeypatch.setattr("rtings_mcp.cache.time.time", lambda: real_time() + 40 * 86_400)
    ctx.transport.payloads["table_tool__products_list"] = RtingsError("fetch_failed", "boom")

    # Bench 2 has never been fetched, so it has no generation to fall back to.
    out = await services.rt_ratings(ctx, "tv", bench=["227", "2"], tests=["208"])
    assert out["error"] is None
    unavailable = [g for g in out["data"]["groups"] if g.get("coverage") == "unavailable"]
    assert unavailable, "the bench with no catalog must be marked, not silently empty"
    assert unavailable[0]["unavailable_benches"] == ["2"]
    assert any("2" in w and "NOT in this response" in w for w in out["warnings"])


# -- Early Access ------------------------------------------------------------------


async def test_an_early_access_url_resolves_to_that_product(ctx):
    """`/early-access/{silo}/reviews/...` puts the silo in the SECOND segment. Deriving it
    from the first and falling back to search returned a different product entirely."""
    ref = await ctx.repo.resolve_product("/early-access/tv/reviews/alpha/alpha-draft")
    assert ref.product_id == "3"
    assert ref.silo == "tv"


async def test_a_url_never_falls_back_to_a_search_hit(ctx):
    """A search hit is ranked by relevance, not identity — live, an early-access URL matched
    a nine-year-old TV and returned its rows as the answer."""
    with pytest.raises(RtingsError) as excinfo:
        await ctx.repo.resolve_product("/tv/reviews/alpha/does-not-exist")
    assert excinfo.value.code == "unknown_product"
    assert "catalog" in excinfo.value.message


async def test_early_access_rows_that_come_through_are_kept(ctx):
    """A member sees early-access data; the catalog flag must not discard it."""
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("3", "208", unblurred=True, value="4k", score=10.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    row = values(out, "3")
    assert row["published"] is False
    assert row["tests"][0]["status"] == "tested_visible"
    assert row["tests"][0]["value"] == "4k"


# -- uncatalogued products ----------------------------------------------------------


async def test_products_with_results_but_no_catalog_row_are_surfaced(ctx):
    """Live, 40 of 109 mattresses are in this state with real values. Leaving them in the
    cache unreported means a comparison silently loses one and reads as 'not tested'."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    orphan = [g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    assert orphan, "a product with results and no catalog row must not vanish"
    assert orphan[0]["matched"] == 1
    row = orphan[0]["products"][0]
    assert row["product_id"] == "99"
    assert row["name"] is None  # the catalog is where names live
    assert row["tests"][0]["value"] == "8k"
    assert "no RTINGS catalog listing" in orphan[0]["notice"]


# -- a cached review is never thrown away for budget reasons ------------------------


async def test_a_cached_review_is_served_rather_than_refused_on_a_free_session(member_ctx):
    """Raising the demand tier to `free` must not turn a good anonymous review — prose,
    public fields, and on an open silo every value — into `preview_exhausted`."""
    await services.rt_product(member_ctx, "/tv/reviews/alpha/alpha-one")  # cached anonymously
    before = member_ctx.transport.calls.count("app/product_vue_page__page_body")

    free_probe(member_ctx)
    out = await services.rt_product(member_ctx, "/tv/reviews/alpha/alpha-one")
    assert out["error"] is None
    assert out["data"]["results"]
    assert member_ctx.transport.calls.count("app/product_vue_page__page_body") == before
    assert any("rather than spending" in w for w in out["warnings"])


# -- member mode, end to end (the flag flips once and is not re-reviewed) ------------


async def test_member_mode_writes_a_member_tier_slice_and_hits_it(member_ctx):
    """With the flag on and a member probe, the write is tier-keyed and the next call hits
    it probe-vs-probe. Nothing exercises this until the flag flips, so it is tested here."""
    member_probe(member_ctx)
    member_ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [make_test_row("1", "11", unblurred=True, value="5000", score=8.0)]
        }
    }
    out = await services.rt_ratings(member_ctx, "tv", tests=["11"])
    assert values(out, "1")["tests"][0]["value"] == 5000.0
    assert out["data_tier"] == "unblurred"
    assert out["auth_state"] == "member"

    tiers = {v.tier for v in member_ctx.cache.list_variants("tests", "227", key="11")}
    assert "member" in tiers

    before = member_ctx.transport.calls.count("table_tool__test_results")
    await services.rt_ratings(member_ctx, "tv", tests=["11"])
    assert member_ctx.transport.calls.count("table_tool__test_results") == before


async def test_member_mode_demotes_a_slice_that_contradicts_the_probe(member_ctx):
    """The probe and the fetch race. Labelling an all-blurred response `member` would serve
    those nulls to a re-authenticated member for the whole TTL — a hit never re-probes."""
    member_probe(member_ctx)
    member_ctx.transport.payloads["table_tool__test_results"] = {
        "data": {"test_results": [make_test_row("1", "11", unblurred=False)]}
    }
    await services.rt_ratings(member_ctx, "tv", tests=["11"])
    tiers = {v.tier for v in member_ctx.cache.list_variants("tests", "227", key="11")}
    assert tiers == {"anonymous"}, "an all-blurred response must not be labelled member"


async def test_a_session_override_rescues_a_misclassified_member(member_ctx):
    """If the classifier guesses `free` for a real member they are served cached nulls for a
    week. The override is a user assertion, and it must reach the demand tier."""
    from rtings_mcp.cache import MEMBER

    member_ctx.transport.credential.configured = "PLACEHOLDER"
    member_ctx.config.session_override = "member"
    probe = await member_ctx.auth.session_probe(force=True)
    assert probe.session == "member"
    assert probe.note and "OVERRIDE" in probe.note
    assert member_ctx.auth.demand_tier("tests", probe) == MEMBER


# -- anonymous-mode usability -------------------------------------------------------


async def test_the_schema_tree_recovers_the_positional_category(ctx):
    """Every category/group row states `parent: null`, so the API declares no hierarchy at
    all — only list order does. Ignoring it returns a flat scramble of groups and categories
    reporting zero leaves, which makes the discovery tool nearly useless."""
    out = await services.rt_schema(ctx, "tv")
    top = out["data"]["groups"]
    assert [g["kind"] for g in top] == ["category"], "categories are the top level"
    assert top[0]["children"], "a category must carry its groups"
    assert top[0]["children"][0]["leaf_test_count"] > 0

    detail = await services.rt_schema(ctx, "tv", group=top[0]["original_id"])
    assert detail["data"]["tests"], "asking for a category must return its tests"
    # Two levels, because the category was recovered from position.
    assert detail["data"]["tests"][0]["hierarchy"] == ["Picture", "Picture Quality"]


async def test_the_tested_variant_is_reported_and_filterable(ctx):
    """There is no 'Size' test on most categories, so without this 'which 65-inch TV is
    brightest?' cannot be asked at all — the field was in the catalog and dropped."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    row = values(out, "1")
    assert row["tested_variant"] == '65"'
    assert row["variants"] == ['65"', '75"']

    narrowed = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"variant": "65"})
    ids = {r["product_id"] for g in narrowed["data"]["groups"] for r in g["products"]}
    assert "1" in ids
    assert "2" not in ids, "product 2 was tested at 55 inches"


async def test_variant_matching_is_forgiving(ctx):
    for spelling in ("65", '65"', "65-inch", "65 inch"):
        out = await services.rt_ratings(
            ctx, "tv", tests=["208"], filters={"variant": spelling}
        )
        ids = {r["product_id"] for g in out["data"]["groups"] for r in g["products"]}
        assert "1" in ids, f"{spelling!r} should match 65 inch"


async def test_usages_can_be_skipped_and_default_to_headline_only(ctx):
    """25 products x every usage is ~15K tokens of mostly identical rows on a gated silo."""
    full = await services.rt_ratings(ctx, "tv")
    assert full["data"]["groups"][0]["products"][0]["usage_scores"]

    lean = await services.rt_ratings(ctx, "tv", usages=[])
    assert lean["data"]["groups"][0]["products"][0]["usage_scores"] == []
    assert len(json.dumps(lean)) < len(json.dumps(full))


async def test_a_graph_row_is_not_reported_as_an_empty_measurement(ctx):
    """`value: null` on a visible graph row reads as 'measured, and the answer is nothing'.
    The answer is a curve."""
    ctx.transport.payloads["app/product_vue_page__page_body"]["data"]["page"]["product"][
        "review"
    ]["test_results"].append(
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": None,
            "test": {"original_id": "13907"},
        }
    )
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    curve = next(v for v in out["data"]["results"] if v["original_id"] == "13907")
    assert curve["value_kind"] == "curve"
    assert "rt_graph" in curve["warning"]


async def test_sorted_by_never_calls_the_fallback_field_gated(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["11"], sort="11")
    assert out["sorted_by"]["field"] == "released_at"
    assert out["sorted_by"]["gated"] is False  # release date is not gated
    assert out["sorted_by"]["requested"] == "11"
    assert out["sorted_by"]["fallback_reason"]


async def test_timestamps_are_all_iso(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    for group in out["data"]["groups"]:
        for row in group["products"]:
            for value in row["tests"]:
                if value.get("as_of") is not None:
                    assert value["as_of"].endswith("Z"), value["as_of"]


async def test_from_cache_is_false_on_a_cold_fetch(ctx):
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    assert out["from_cache"] is False


async def test_a_narrow_observation_does_not_erase_a_wide_one(ctx):
    """Cumulative counts make an open->gated flip read `partial` forever."""
    from rtings_mcp.observations import ObservationStore, ScoresAvailable

    store = ObservationStore(ctx.cache)
    wide = ScoresAvailable(has_insider=True)
    for _ in range(50):
        wide.insider_tests.add(unblurred=True)
    store.record("tv", "227", wide)
    assert store.read("tv", "227").completeness == "full"

    narrow = ScoresAvailable(has_insider=True)
    narrow.insider_tests.add(unblurred=False)
    store.record("tv", "227", narrow)
    assert store.read("tv", "227").insider_total == 50, "a 1-row read must not overwrite 50"

    flip = ScoresAvailable(has_insider=True)
    for _ in range(50):
        flip.insider_tests.add(unblurred=False)
    store.record("tv", "227", flip)
    assert store.read("tv", "227").completeness == "gated", "an equally wide read must win"


async def test_a_search_hit_must_be_a_review(ctx):
    """The index also returns articles, tool pages and best-of lists."""
    ctx.transport.payloads["app/search__search_results"] = {
        "data": {
            "search_results": {
                "total_count": 1,
                "results": [
                    {
                        "kind": "page",
                        "title": "Best TVs",
                        "url": "/tv/reviews/best/tvs",
                        "product_id": "555",
                    },
                ],
            }
        }
    }
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_product(ctx, "best tvs")
    assert excinfo.value.code == "unknown_product"
    assert "rt_search" in excinfo.value.message


async def test_a_coverage_unknown_usage_row_also_uses_iso(ctx):
    """That branch builds its dict by hand, so it is the one that drifts."""
    from rtings_mcp.services import _usage_json

    schema = await ctx.repo.schema("tv")
    await services.rt_ratings(ctx, "tv", usages=["1"])
    meta = ctx.repo.slice_meta("ratings", "227", "1", demand="anonymous")
    covered = _usage_json(schema, "1", {}, meta, set(), "1")
    assert covered["status"] == "not_tested"
    assert isinstance(covered["as_of"], str) and covered["as_of"].endswith("Z")


# -- rotation write-back: the session slides, so the stored copy must keep up ---------


async def test_a_rotated_cookie_is_persisted_when_the_probe_proves_login(member_ctx):
    """RTINGS re-issues the cookie on every response with a fresh 30-day expiry, so a
    credential that is never refreshed expires 30 days after the paste however much the
    server is used. Persisting the proven-logged-in value is what makes it last."""
    from rtings_mcp.auth import load_credential, store_credential

    store_credential(member_ctx.config, "PASTED")
    member_ctx.transport.credential.configured = "PASTED"
    member_ctx.transport.credential.source = "file"
    member_ctx.transport.session_page = "member"
    member_ctx.transport.jar_cookie = "REISSUED-BY-RTINGS"

    probe = await member_ctx.auth.session_probe(force=True)
    assert probe.logged_in
    assert member_ctx.transport.credential.configured == "REISSUED-BY-RTINGS"
    assert load_credential(member_ctx.config).configured == "REISSUED-BY-RTINGS"


async def test_a_rotated_cookie_is_NOT_persisted_when_the_response_is_logged_out(ctx):
    """An anonymous GET mints a cookie too. Persisting that would replace the user's
    credential with an anonymous one and destroy it."""
    from rtings_mcp.auth import load_credential, store_credential

    store_credential(ctx.config, "REAL-CREDENTIAL")
    ctx.transport.credential.configured = "REAL-CREDENTIAL"
    ctx.transport.credential.source = "file"
    ctx.transport.session_page = "anonymous"
    ctx.transport.jar_cookie = "ANONYMOUS-REMINT"

    probe = await ctx.auth.session_probe(force=True)
    assert probe.logged_in is False
    assert probe.session == "expired"  # a configured cookie that came back logged out
    assert load_credential(ctx.config).configured == "REAL-CREDENTIAL"
    assert ctx.transport.credential.jar_mismatch is True


async def test_an_env_var_credential_is_never_written_to_disk(member_ctx):
    """It cannot be refreshed, so the user is warned rather than silently degraded."""
    from rtings_mcp.auth import load_credential

    member_ctx.transport.credential.configured = "FROM-ENV"
    member_ctx.transport.credential.source = "env"
    member_ctx.transport.session_page = "member"
    member_ctx.transport.jar_cookie = "REISSUED"

    await member_ctx.auth.session_probe(force=True)
    assert member_ctx.transport.credential.configured == "REISSUED"  # in memory only
    assert load_credential(member_ctx.config).configured is None


async def test_partial_field_errors_do_not_discard_the_data(ctx):
    """RTINGS strips admin-only fields and says so in errors[] while returning a full
    payload. Treating any errors[] as fatal throws that data away."""
    ctx.transport.payloads["app/search__search_results"] = {
        "data": {"search_results": {"total_count": 1, "results": []}},
        "errors": [
            {"message": "The field edit_url on an object of type Comparison was hidden "
                        "due to permissions"}
        ],
    }
    out = await services.rt_search(ctx, "anything")
    assert out["error"] is None
    assert out["data"]["total_count"] == 1


async def test_errors_with_no_data_is_a_real_api_error(ctx):
    ctx.transport.payloads["app/search__search_results"] = {
        "data": None,
        "errors": [{"message": "boom"}],
    }
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_search(ctx, "anything")
    assert excinfo.value.code == "api_error"


async def test_an_unknown_silo_payload_is_payload_missing_not_api_error(ctx):
    """The case `api_error` was documented for does not use errors[] at all."""
    ctx.transport.payloads["table_tool__column_options"] = {"data": {"silo": None}}
    with pytest.raises(RtingsError) as excinfo:
        await ctx.repo.schema("mattress")
    assert excinfo.value.code == "payload_missing"


# -- verdicts: RTINGS' words, which survive the paywall -------------------------------


SBS_PAYLOAD = {
    "data": {
        "review": {
            "id": "1",
            "user_has_access": False,
            "product": {"id": "1", "fullname": "Alpha One"},
            "test_bench": {"id": "227", "name": "2.2", "tests": []},
            "product_score_sets": [
                {
                    "id": "a",
                    "score": None,
                    "suitable": True,
                    "score_set_id": "ss1",
                    "score_set__original_id": "1",
                    "linked_description": "<p>The Alpha One is good for mixed usage.</p>",
                },
                {
                    "id": "b",
                    "score": None,
                    "suitable": False,
                    "score_set_id": "ss2",
                    "score_set__original_id": "12",
                    "linked_description": "<p>Not ideal for home theater.</p>",
                },
            ],
            "score_sets": [
                {
                    "id": "ss1",
                    "original_id": "1",
                    "name": "Mixed Usage",
                    "kind": "usage",
                    "items": [{"score_set_id": "ss2", "test_id": None, "weight": 40.0}],
                },
                {
                    "id": "ss2",
                    "original_id": "12",
                    "name": "Home Theater",
                    "kind": "usage",
                    "items": [],
                },
            ],
            "summaries": [
                {"id": "s1", "priority": 1, "blurb": "<p>Very bright.</p>", "title": None},
                {"id": "s2", "priority": -1, "blurb": "<p>Poor viewing angles.</p>",
                 "title": None},
            ],
            "test_results": [],
        }
    }
}


async def test_verdicts_are_served_even_when_every_number_is_withheld(ctx):
    """The whole point: on a category that withholds measurements, RTINGS' written verdict
    still comes through, so 'is this good for gaming?' has a real answer."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    data = out["data"]
    mixed = next(v for v in data["verdicts"] if v["name"] == "Mixed Usage")
    assert mixed["verdict"] == "The Alpha One is good for mixed usage."
    assert mixed["status"] == "tested_gated"
    assert mixed["gated"] is True
    assert mixed["score"] is None
    assert mixed["suitable"] is True


async def test_a_served_score_is_gated_false_not_null(ctx):
    """`gated: null` beside a real value is the pair the project forbids."""
    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["user_has_access"] = True
    payload["data"]["review"]["product_score_sets"][0]["score"] = 7.3
    ctx.transport.payloads["app/side_by_side__review"] = payload

    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    mixed = next(v for v in out["data"]["verdicts"] if v["name"] == "Mixed Usage")
    assert mixed["score"] == 7.3
    assert mixed["status"] == "tested_visible"
    assert mixed["gated"] is False


async def test_pros_and_cons_are_separated_by_priority(ctx):
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    highlights = {h["text"]: h["sentiment"] for h in out["data"]["highlights"]}
    assert highlights["Very bright."] == "pro"
    assert highlights["Poor viewing angles."] == "con"


async def test_the_scoring_recipe_is_reported(ctx):
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    mixed = next(s for s in out["data"]["scoring"] if s["name"] == "Mixed Usage")
    assert mixed["components"][0]["weight_pct"] == 40.0
    assert mixed["components"][0]["component"] == "Home Theater"


async def test_verdict_scores_inform_scores_available(ctx):
    """A call that just returned usage scores must not report `usage_ratings: unknown`."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    assert out["scores_available"]["usage_ratings"] == "gated"


async def test_verdicts_are_opt_in_and_cost_nothing_when_off(ctx):
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert out["data"].get("verdicts") is None
    assert "app/side_by_side__review" not in ctx.transport.calls


async def test_losing_the_verdicts_does_not_lose_the_measurements(ctx):
    """The commentary is an extra request; a failure there must not fail the whole call."""
    ctx.transport.payloads["app/side_by_side__review"] = RtingsError("fetch_failed", "boom")
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    assert out["error"] is None
    assert out["data"]["results"], "the measurements were already in hand"
    assert any("verdicts unavailable" in w for w in out["warnings"])


async def test_verdicts_are_cached(ctx):
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True)
    before = ctx.transport.calls.count("app/side_by_side__review")
    await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True)
    assert ctx.transport.calls.count("app/side_by_side__review") == before


async def test_rt_product_rows_do_not_repeat_response_constants(ctx):
    """`product_id` and `as_of` are identical on all 243 rows of a real response."""
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert out["data"]["product"]["product_id"] == "1"
    for row in out["data"]["results"]:
        assert "product_id" not in row
        assert "as_of" not in row


# -- the review's findings, each with a regression guard -----------------------------


async def test_the_verdicts_notice_never_overwrites_the_measurement_notice(ctx):
    """`data.notice` explains why the measurements are null — including "this is Early
    Access", which must never be replaced by paywall framing. `dict.update` did exactly
    that."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/early-access/tv/reviews/alpha/alpha-draft", include_verdicts=True
    )
    assert "still in progress" in out["data"]["notice"]
    assert "Early Access" in out["data"]["notice"] or "progress" in out["data"]["notice"]
    # The verdicts get their own key rather than stealing that one.
    assert out["data"]["verdicts_notice"]
    assert out["data"]["verdicts_notice"] != out["data"]["notice"]


async def test_an_absent_score_with_access_is_not_claimed_to_be_untested(ctx):
    """The only signal is one review-wide flag. Calling a missing score `not_tested` asserts
    "RTINGS did not measure this" — the one claim this project must never make on thin
    evidence. A visible row can be genuinely empty."""
    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["user_has_access"] = True  # access granted, score still null
    ctx.transport.payloads["app/side_by_side__review"] = payload

    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    mixed = next(v for v in out["data"]["verdicts"] if v["name"] == "Mixed Usage")
    assert mixed["score"] is None
    assert mixed["status"] != "not_tested"
    assert mixed["status"] == "tested_visible"
    assert mixed["gated"] is None  # never False beside a null score


async def test_early_access_verdicts_are_not_blamed_on_the_paywall(ctx):
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/early-access/tv/reviews/alpha/alpha-draft", include_verdicts=True
    )
    statuses = {v["status"] for v in out["data"]["verdicts"]}
    assert statuses == {"review_unpublished"}
    assert all(v["gated"] is None for v in out["data"]["verdicts"])


async def test_the_verdicts_cache_marks_files_worth_protecting(ctx):
    """`envelope_has_unblurred_insider` reads `has_unblurred_insider`. Writing only
    `user_has_access` meant never-downgrade never fired for this surface."""
    from rtings_mcp.cache import envelope_has_unblurred_insider

    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["user_has_access"] = True
    ctx.transport.payloads["app/side_by_side__review"] = payload

    await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True)
    variants = ctx.cache.read_variants("verdicts", key="1")
    assert variants
    assert envelope_has_unblurred_insider(variants[0]) is True


async def test_a_scoring_component_that_is_a_raw_test_resolves_to_a_name(ctx):
    """A component is either a sub-score or a raw test; both must resolve, and the internal
    test id is not joinable against the schema."""
    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["test_bench"]["tests"] = [
        {"id": "t99", "original_id": "463", "name": "Peak 100% Window"}
    ]
    payload["data"]["review"]["score_sets"][0]["items"] = [
        {"score_set_id": None, "test_id": "t99", "weight": 55.0}
    ]
    ctx.transport.payloads["app/side_by_side__review"] = payload

    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    mixed = next(s for s in out["data"]["scoring"] if s["name"] == "Mixed Usage")
    component = mixed["components"][0]
    assert component["component"] == "Peak 100% Window"
    assert component["original_id"] == "463"  # joinable against rt_schema
    assert component["component_kind"] == "test"


async def test_a_spent_preview_budget_does_not_also_withhold_the_verdicts(ctx):
    """The verdicts are the public compare tool, not the metered review page. Denying them
    because the review body could not be bought charges them a cost they do not incur."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    free_probe(ctx, previewed=["other"], limit=0)

    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    assert out["error"] is None, "there is real content to return"
    assert out["data"]["verdicts"], "the verdicts are unaffected by the budget"
    assert out["data"]["results"] == []
    assert "measurements were not fetched" in out["data"]["notice"]
    assert any("measurements unavailable" in w for w in out["warnings"])
    assert "app/product_vue_page__page_body" not in ctx.transport.calls


async def test_a_spent_budget_still_fails_when_verdicts_were_not_asked_for(ctx):
    free_probe(ctx, previewed=["other"], limit=0)
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert excinfo.value.code == "preview_exhausted"


async def test_from_cache_means_this_call_made_no_request(ctx):
    """It used to mean "the data is more than 2 seconds old", so two back-to-back calls both
    reported `from_cache: false` while the second made zero requests."""
    first = await services.rt_ratings(ctx, "tv", tests=["208"])
    assert first["from_cache"] is False, "a cold fetch is not from cache"
    before = len(ctx.transport.calls)

    second = await services.rt_ratings(ctx, "tv", tests=["208"])
    assert len(ctx.transport.calls) == before, "no request was made"
    assert second["from_cache"] is True, "so it must say so, immediately, not after 2s"


async def test_a_numeric_id_miss_names_the_silo_that_was_searched(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await ctx.repo.resolve_product("99999", "tv")
    assert "not in the tv catalog" in excinfo.value.message
    assert "pass silo=" not in excinfo.value.message  # a silo WAS passed
