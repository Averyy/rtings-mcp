"""The seven tool bodies, offline, against a stub transport.

The live tests prove the tools work against RTINGS; these prove they behave correctly on
inputs RTINGS rarely produces on demand — a product that postdates a cached slice, a
response with nothing to say about a surface, a filter on a field whose value is null.
"""

from __future__ import annotations

import html as html_module
import json
import time
from pathlib import Path

import pytest

from rtings_mcp import models, repository, services
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
                {
                    "title": "Best Static TVs",
                    "url": "/tv/reviews/best/static-template",
                    "short": "Static",
                },
                # RTINGS' Best nav carries the per-brand pages beside the /best/ lists,
                # one segment up: "The 4 Best Sony TVs" at /tv/reviews/sony.
                {"title": "The 4 Best Alpha TVs", "url": "/tv/reviews/alpha", "short": "Alpha"},
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

#: The server-rendered best-of template (mattress, running-shoes as of 2026-09-05).
#: Structure copied from the live page: class names, nested tooltip divs and the
#: `RecommendationPagePrices` island that carries the ranked product_id.
REC_STATIC_HTML = (FIXTURES / "recommendation_static_min.html").read_text(
    encoding="utf-8"
)


#: A `learn` article: `page.article` with the prose in `text_with_anchors`, headings and
#: all. Shape copied from /tv/learn/2026-lineup, measured 2026-09-08.
ARTICLE_PROPS = _props(
    {
        "page_data": {
            "page": {
                "url": "/tv/learn/alpha-lineup",
                "updated_at": "2026-06-18 07:24:41 -0400",
                "authors": [{"name": "A Writer", "author_url": "/authors/a-writer"}],
                "article": {
                    "title": "2026 Alpha Lineup",
                    "introduction": "<p>The intro.</p>",
                    "text": "no anchors",
                    "text_with_anchors": (
                        "<h2>Market Trends</h2><p>Panels got brighter.</p>"
                        "<h2>Brand Lineups</h2><h3>Alpha</h3><p>Alpha ships an 83 inch "
                        "OLED this year.</p><h3>Beta</h3><p>Beta ships nothing.</p>"
                    ),
                    "latest_update_date": "2026-06-18 07:24:41 -0400",
                    "created_at": "2026-01-09 00:00:00 -0400",
                    "toc_items": [{"name": "Intro", "url": "#page-top"}],
                    "meta_description": "what is new",
                },
            }
        }
    }
)

ARTICLE_HTML = f"""
<html><head><title>2026 Alpha Lineup - RTINGS.com</title></head><body>
{ARTICLE_PROPS}
</body></html>
"""


def _test_page_props(url: str, title: str, *, intro: str, text: str = "") -> str:
    """A `/{silo}/tests/{slug}` page: `page.type` is `TestPage` and the prose may live
    ENTIRELY in `introduction` with `text` empty. Shape copied from
    /tv/tests/longevity-burn-in-test-updates-and-results, measured 2026-09-09."""
    return _props(
        {
            "page_data": {
                "page": {
                    "url": url,
                    "type": "TestPage",
                    "updated_at": "2026-03-16 10:59:15 -0400",
                    "authors": [{"name": "A Tester", "author_url": "/authors/a-tester"}],
                    "article": {
                        "title": title,
                        "introduction": intro,
                        "text": text,
                        "text_with_anchors": text,
                        "latest_update_date": "2026-03-16 10:59:15 -0400",
                        "created_at": "2026-01-09 00:00:00 -0400",
                        "toc_items": [{"name": "Intro", "url": "#page-top"}],
                        "meta_description": "how long a TV lasts",
                    },
                }
            }
        }
    )


#: The longevity shape: 0 characters of `text`, the whole article in `introduction`, one
#: heading per dated update. Reading only `text` returned an empty body for this page.
LONGEVITY_PROPS = _test_page_props(
    "/tv/tests/alpha-longevity",
    "Longevity Burn-In Test: Updates And Results",
    intro=(
        "<p>The lead paragraph.</p>"
        "<h2>March 16, 2026 - Final Update</h2><p>Four panels failed outright.</p>"
        "<h2>August 28, 2025 - Alpha X90J</h2><p>Uniform dimming after 9,000 hours.</p>"
    ),
)
LONGEVITY_HTML = f"""
<html><head><title>Longevity - RTINGS.com</title></head><body>
{LONGEVITY_PROPS}
</body></html>
"""

#: A nested `/tests/` slug — the methodology pages `rt_schema` describes numerically.
CONTRAST_TEST_PROPS = _test_page_props(
    "/tv/tests/picture-quality/alpha-contrast",
    "Contrast Ratio",
    intro="<p>What contrast is.</p>",
    text="<h2>Our Test</h2><p>We measure a checkerboard.</p>",
)
CONTRAST_TEST_HTML = f"""
<html><head><title>Contrast Ratio - RTINGS.com</title></head><body>
{CONTRAST_TEST_PROPS}
</body></html>
"""

#: A real preface AND a body, with the preface long enough to crowd the body out.
LONG_INTRO_HTML = f"""
<html><head><title>Long Intro - RTINGS.com</title></head><body>
{_test_page_props(
    "/tv/tests/long-intro",
    "A Page With A Long Preface",
    intro="<p>" + ("Sentence number one. " * 500) + "</p>",
    text="<h2>Results</h2><p>" + ("A body sentence. " * 400) + "</p>",
)}
</body></html>
"""

#: A page whose OUTLINE alone is past the budget — `sections` names every heading, so the
#: data dict can spend the budget before the prose is measured at all.
MANY_SECTIONS_HTML = f"""
<html><head><title>Many Sections - RTINGS.com</title></head><body>
{_test_page_props(
    "/tv/tests/many-sections",
    "A Page With A Very Long Outline",
    intro="<p>Short.</p>",
    text="".join(
        f"<h2>Update number {n} of the accelerated longevity test</h2><p>Body {n}.</p>"
        for n in range(400)
    ),
)}
</body></html>
"""

#: Same SLUG as the learn article above, different branch — the two must not share a
#: cache entry.
TESTS_ALPHA_LINEUP_HTML = f"""
<html><head><title>Alpha Lineup Test - RTINGS.com</title></head><body>
{_test_page_props(
    "/tv/tests/alpha-lineup",
    "How We Test Alpha Lineups",
    intro="<p>Not the learn article.</p>",
    text="<h2>Method</h2><p>A test page, not a lineup.</p>",
)}
</body></html>
"""


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
        # A sku's `name` is the MANUFACTURER model number, not the product's name —
        # `{"id":"2951","name":"UN43TU7000FXZA","variation":"43\""}` on the live catalog.
        "variant_skus": [
            {"id": "s-" + pid, "name": f"XR-{tested.strip(chr(34))}A{pid}", "variation": tested},
            {"id": "other-" + pid, "name": f"XR-75A{pid}", "variation": '75"'},
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
        if "/reviews/best/static-template" in path:
            html = REC_STATIC_HTML
        elif "/reviews/best/made-up" in path:
            html = "<html><head><title>Not found</title></head><body></body></html>"
        elif "/reviews/best/" in path:
            # RTINGS answers an unknown best-of slug with the silo landing page, not a
            # 404 — which is why the brand shape is tried whenever no template matched.
            html = (
                PAGE_HTML
                if path.endswith("/alpha")
                else (getattr(self, "rec_html", None) or REC_HTML)
            )
        elif path == "/tv/reviews/alpha":
            html = REC_HTML
        elif path == "/tv/learn/alpha-lineup":
            html = ARTICLE_HTML
        elif path == "/tv/tests/alpha-longevity":
            html = LONGEVITY_HTML
        elif path == "/tv/tests/picture-quality/alpha-contrast":
            html = CONTRAST_TEST_HTML
        elif path == "/tv/tests/alpha-lineup":
            html = TESTS_ALPHA_LINEUP_HTML
        elif path == "/tv/tests/long-intro":
            html = LONG_INTRO_HTML
        elif path == "/tv/tests/many-sections":
            html = MANY_SECTIONS_HTML
        elif "/learn/" in path or "/tests/" in path:
            # RTINGS answers an unknown learn or tests slug with another page, never a 404.
            html = PAGE_HTML
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
                        # The review body carries the size lineup as skus, each named by
                        # the manufacturer's model number.
                        "reviewed_sku_id": "s-1",
                        "variant_skus": [
                            {"id": "s-1", "name": "XR-65A1", "variation": '65"'},
                            {"id": "other-1", "name": "XR-75A1", "variation": '75"'},
                        ],
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


@pytest.fixture
def flag_off_ctx(tmp_path, payloads):
    """A context with `RTINGS_MEMBER_MODE` explicitly **off**.

    The default flipped on 2026-09-06 once Phase 0 was measured (`RECON.md` §13.1), but the
    flag-off state is exactly what the write guard exists for: a live credential with the
    tier machinery pinned to `anonymous`, where a member's rows cannot honestly be labelled.
    Inheriting the default here would leave every guard test below asserting nothing.
    """
    config = load_config(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "off"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "offcfg"),
            "RTINGS_MEMBER_MODE": "0",
        }
    )
    cache = Cache(config)
    transport = StubTransport(config, payloads)
    auth = AuthManager(config=config, cache=cache, transport=transport)
    repo = Repository(config, cache, transport, auth)
    return Context(config=config, cache=cache, transport=transport, auth=auth, repo=repo)


def product_rows(out):
    """`rt_product`'s results, flattened out of their hierarchy groups."""
    return [row for group in out["data"]["results"] for row in group["tests"]]


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


async def test_a_filter_field_is_fetched_even_when_it_was_not_projected(ctx):
    """Measured live on mattress, an OPEN silo: `filters={"Thickness": ">1"}` returned all
    69 products with a warning saying no value was populated — because the test had never
    been fetched, not because RTINGS withheld it. "0 applied" reading as "no data exists"
    is the project's core failure mode wearing a different hat, and here nothing was gated
    at all."""
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="500"),
                make_test_row("2", "11", unblurred=True, value="5000"),
            ]
        }
    }
    # No `tests=` at all: the filter alone must pull test 11 in.
    out = await services.rt_ratings(ctx, "tv", filters={"11": ">1000"})
    matched = [
        row["product_id"] for group in out["data"]["groups"] for row in group["products"]
    ]
    assert matched == ["2"], "the filter must apply without being projected explicitly"
    assert not [w for w in out["warnings"] if "filter_unavailable" in w]


async def test_a_sort_field_is_fetched_even_when_it_was_not_projected(ctx):
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="500"),
                make_test_row("2", "11", unblurred=True, value="5000"),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", sort="11")
    assert out["sorted_by"]["field"] == "11", "must not fall back to released_at"
    ordered = [
        row["product_id"] for group in out["data"]["groups"] for row in group["products"]
    ]
    assert ordered[:2] == ["2", "1"], "descending by the requested test"


async def test_rows_with_no_comparable_value_sort_last_in_both_directions(ctx):
    """`reverse` flips the whole sort key, so a fixed "missing" rank put unmeasurable rows
    at the TOP of every descending sort — "the brightest TVs" led by TVs whose brightness
    is gated or untested."""
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="500"),
                make_test_row("2", "11", unblurred=True, value="5000"),
                make_test_row("3", "11", unblurred=False),  # gated: no comparable value
            ]
        }
    }
    for spec, expected_head in (("-11", "2"), ("+11", "1")):
        out = await services.rt_ratings(ctx, "tv", sort=spec)
        ordered = [
            row["product_id"]
            for group in out["data"]["groups"]
            for row in group["products"]
        ]
        assert ordered[0] == expected_head, (spec, ordered)
        assert ordered[-1] == "3", f"the unmeasurable row must sort last ({spec})"


async def test_a_test_may_be_named_wherever_an_id_is_accepted(ctx):
    """`filters` and `sort` have always taken a name, so `tests=` rejecting one made the
    documented remedy for an unapplied filter fail on the very string the filter took."""
    by_id = await services.rt_ratings(ctx, "tv", tests=["11"])
    definition = (await ctx.repo.schema("tv")).test("11")
    by_name = await services.rt_ratings(ctx, "tv", tests=[definition.name])
    assert by_name["data"]["groups"] == by_id["data"]["groups"]


async def test_an_absent_filter_field_is_not_reported_as_gated(ctx):
    """Three reasons a filter cannot apply — gated, absent, genuinely empty — and telling
    the caller the wrong one sends them to buy a membership they do not need."""
    out = await services.rt_ratings(ctx, "tv", bench=["2"], filters={"208": ">1"})
    unavailable = [w for w in out["warnings"] if "filter_unavailable" in w]
    if unavailable:
        assert not any("gated for this session" in w for w in unavailable), unavailable


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
    by_name = {v["name"]: v for v in product_rows(out)}
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


async def test_rt_graph_accepts_a_test_name_like_every_other_entry_point(ctx):
    """`rt_schema` prints a graph test's name beside its id, so the discovery step hands the
    caller the exact string `rt_graph` then rejected — the gap `_name_or_id` closed for
    `tests=`, left open on the one tool whose input always comes from `rt_schema`.

    The fixture's graph test shares its group's name on purpose: headphones, vacuum and
    toaster-oven all publish a group and a leaf under the same name, and resolving by dict
    order handed back the group — `rt_graph` then refused with "kind='group'; only kind=graph
    tests have a curve" on a name the schema publishes for a real curve. A LEAF wins the tie.
    """
    by_name = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "Picture Quality")
    by_id = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    assert by_name["data"]["test"]["original_id"] == "13907"
    assert by_name["data"]["points"] == by_id["data"]["points"]


async def test_a_group_and_a_leaf_sharing_a_name_each_resolve_to_the_right_one(ctx):
    """The two lookups lean opposite ways on purpose: `group=` addresses a section, while
    `tests=`/`filters`/`sort`/`rt_graph` address a measurement."""
    detail = await services.rt_schema(ctx, "tv", group="Picture Quality")
    assert detail["data"]["group"]["original_id"] == "900"
    assert detail["data"]["group"]["kind"] == "group"

    out = await services.rt_ratings(ctx, "tv", tests=["Picture Quality"], limit=1)
    assert out["error"] is None
    projected = {v["original_id"] for g in out["data"]["groups"]
                 for p in g["products"] for v in p["tests"]}
    assert projected == {"13907"}, "a leaf, not the group above it"


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


async def test_an_undiscovered_list_is_fetched_and_only_a_missing_page_is_refused(ctx):
    """The index is not exhaustive (a review links to a list it omits), so a slug outside
    it is fetched; a page that matches neither template is `unknown_list`."""
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_recommendations(ctx, "tv", list="made-up")
    assert excinfo.value.code == "unknown_list"


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


# -- the second best-of template (server-rendered) ----------------------------------


async def test_the_server_rendered_best_of_template_is_parsed(ctx):
    """RTINGS is migrating best-of pages off the monolithic `RecommendationVuePage` onto a
    server-rendered template with small Vue islands. Measured 2026-09-05: mattress and
    running-shoes had moved and every one of their 20 lists returned
    `recommendations_missing` — the tool advertised lists it could not fetch."""
    out = await services.rt_recommendations(ctx, "tv", list="static-template")
    assert out["error"] is None
    # The declared output model is what carries this shape to the client, so the second
    # template has to satisfy it too — not just the dict the service builds.
    models.RecommendationsEnvelope.model_validate(out)
    data = out["data"]
    assert data["title"] == "The 2 Best Static TVs of 2026"
    assert data["updated_at"] == "Aug 26, 2026 at 12:45 pm", "the 'Updated' label is dropped"
    assert data["introduction"] == "<p>static intro prose</p>"

    assert [p["rank"] for p in data["picks"]] == [1, 2]
    first = data["picks"][0]
    assert first["title"] == "Best Static TV"
    assert first["name"] == "Alpha One"
    assert first["product_id"] == "1"
    assert first["url"] == "/tv/reviews/alpha/alpha-one"
    # The wrapping rich-content div is unwrapped so both templates emit one shape.
    assert first["reasoning"] == "<p>why alpha one</p>"


async def test_the_static_template_separates_usages_from_tests(ctx):
    """The tooltip beside each featured item carries `target_type`, which is the only thing
    distinguishing a usage score from a test value on this template."""
    out = await services.rt_recommendations(ctx, "tv", list="static-template")
    first = out["data"]["picks"][0]

    ratings = {r["name"]: r for r in first["usage_scores"]}
    assert ratings["Mixed Usage"]["score"] == 8.8
    assert ratings["Mixed Usage"]["status"] == "tested_visible"
    # `target_id` is NOT the schema's original_id (live: Side Sleeping is 38309 here and
    # 36553 in the schema, and measured 2026-09-08, 0 of 9 target_ids resolve). The NAME
    # does resolve, so a usage whose name is unique on the silo gets its real id.
    assert ratings["Mixed Usage"]["original_id"] == "1"
    assert "candidates" not in ratings["Mixed Usage"]

    tests = {t["name"]: t for t in first["featured_results"]}
    assert tests["Resolution"]["display"] == "4k"
    assert tests["Resolution"]["status"] == "tested_visible"


async def test_a_static_featured_item_without_a_tooltip_still_resolves(ctx):
    """Not every item carries a `DistributionTooltip`, and the rendered label brings its own
    punctuation ("Bed-In-A-Box:&nbsp;"). The tooltip label and the props template both give
    the bare name, so the DOM fallback is trimmed to match rather than ship two shapes.
    With no tooltip there is no `target_type` either, so it is a test, not a usage."""
    out = await services.rt_recommendations(ctx, "tv", list="static-template")
    first = out["data"]["picks"][0]
    tests = {t["name"]: t for t in first["featured_results"]}
    assert "Bed-In-A-Box" in tests, sorted(tests)
    assert tests["Bed-In-A-Box"]["display"] == "Yes"
    assert tests["Bed-In-A-Box"]["status"] == "tested_visible"
    assert "Bed-In-A-Box" not in {r["name"] for r in first["usage_scores"]}


async def test_a_static_featured_item_that_rendered_nothing_is_not_called_gated(ctx):
    """Neither a score nor a value came back. That is 'I cannot classify this row', not
    'buy a membership' — inventing a paywall is the project's core failure mode."""
    out = await services.rt_recommendations(ctx, "tv", list="static-template")
    tests = {t["name"]: t for t in out["data"]["picks"][0]["featured_results"]}
    assert tests["Withheld Test"]["status"] == "unknown_row_status"
    assert tests["Withheld Test"]["gated"] is None


async def test_each_best_of_parser_declines_the_other_template(ctx):
    """Both shapes are legitimate now, so the fallback must not double-match: a parser that
    accepted the wrong page would emit a plausible half-empty ranking."""
    from rtings_mcp.repository import _extract_recommendation, _extract_recommendation_static

    assert _extract_recommendation(REC_STATIC_HTML) is None
    assert _extract_recommendation_static(REC_STATIC_HTML) is not None
    assert _extract_recommendation(REC_HTML) is not None
    assert _extract_recommendation_static(REC_HTML) is None


async def test_recommendations_missing_fires_only_when_neither_template_matches(ctx):
    """The drift alarm still has to work — it is the whole reason this path is isolated."""
    ctx.transport.rec_html = "<html><body><p>nothing like either template</p></body></html>"
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert excinfo.value.code == "recommendations_missing"
    assert "neither best-of template" in excinfo.value.message


# -- uncatalogued products ----------------------------------------------------------


async def test_products_with_results_but_no_catalog_row_are_surfaced(ctx):
    """Live, 40 of 109 mattresses are in this state with real values. Leaving them in the
    cache unreported means a comparison silently loses one and reads as 'not tested'."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    orphan = [g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    assert orphan, "a product with results and no catalog row must not vanish"
    assert orphan[0]["matched"] == 1
    # Compact by default (2026-09-06): every evaluator found nameless rows unusable for a
    # recommendation and paid for them on every call. The ids say "these exist and were
    # measured"; the rows come on request.
    assert orphan[0]["product_ids"] == ["99"]
    assert orphan[0]["products"] == []
    assert "include_uncatalogued=true" in orphan[0]["notice"]
    assert "rt_product(<id>, silo=...)" in orphan[0]["notice"]

    out = await services.rt_ratings(ctx, "tv", tests=["208"], include_uncatalogued=True)
    orphan = [g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    row = orphan[0]["products"][0]
    assert row["product_id"] == "99"
    assert row["name"] is None  # the catalog is where names live
    assert row["tests"][0]["value"] == "8k"
    assert "product_id" not in row["tests"][0], "the parent dict carries it"


async def test_the_uncatalogued_group_pages_like_every_other_group(ctx):
    """It honoured `limit` but not `offset`, so it re-served its first page forever while
    `matched` advertised the rest — reinstating, for 41 of mattress's 110 products, exactly
    the unreachability this group exists to fix."""
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row(pid, "208", unblurred=True, value="8k", score=10.0)
                for pid in ("97", "98", "99")
            ]
        }
    }

    def orphan(out):
        group = next(
            g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"
        )
        assert group["matched"] == 3, "the count must keep describing the whole population"
        return [p["product_id"] for p in group["products"]]

    first = await services.rt_ratings(
        ctx, "tv", tests=["208"], limit=1, offset=0, include_uncatalogued=True
    )
    second = await services.rt_ratings(
        ctx, "tv", tests=["208"], limit=1, offset=1, include_uncatalogued=True
    )
    assert orphan(first) == ["97"]
    assert orphan(second) == ["98"], "offset must advance this group's window too"


async def test_a_variant_nobody_was_tested_in_explains_itself(ctx):
    """RTINGS reviews ONE size per product, so filtering on a size it sells but did not test
    matches nothing. Live, `{"variant": "California King"}` on mattress returned 0 rows and
    an empty `warnings` — which reads as "RTINGS has tested no California King mattress"
    when in truth it tested the Queen of 60-odd mattresses that ship in Cal King."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"variant": "75"})
    assert out["error"] is None
    assert out["data"]["total_matched"] == 0
    assert any(
        "no product was TESTED in" in w and "sold in it" in w for w in out["warnings"]
    ), out["warnings"]

    # A size nobody even sells stays a plain empty result — there is nothing to explain.
    quiet = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"variant": "12"})
    assert quiet["data"]["total_matched"] == 0
    assert not any("sold in it" in w for w in quiet["warnings"])


# -- a name works wherever an original_id works -------------------------------------


async def test_a_group_name_works_wherever_its_id_does(ctx):
    """`rt_schema`'s tree prints `name` beside `original_id`, and the natural next call
    passes the name straight back — which failed, making the tool's own output unusable as
    its own input."""
    by_name = await services.rt_schema(ctx, "tv", group="Picture Quality")
    by_id = await services.rt_schema(ctx, "tv", group="900")
    assert by_name["data"]["group"] == by_id["data"]["group"]
    assert by_name["data"]["tests"] == by_id["data"]["tests"]

    by_name = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", group="Picture Quality"
    )
    by_id = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", group="900")
    assert by_name["data"]["results"] == by_id["data"]["results"]


async def test_an_unknown_group_name_says_so_rather_than_pretending_it_is_an_id(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_schema(ctx, "tv", group="No Such Section")
    assert excinfo.value.code == "unknown_test"
    assert "No Such Section" in excinfo.value.message


async def test_group_bounds_the_prose_it_returns(ctx):
    """`group` bounds the response and prose is the biggest thing in it, but the scope test
    sat *below* the structure-row branch — so a bounded request still carried every group's
    commentary, which on a real TV is 53 blocks for a caller who asked for one group."""
    review = ctx.transport.payloads["app/product_vue_page__page_body"]
    review["data"]["page"]["product"]["review"]["test_results"].append(
        {
            "status": "tested",
            "unblurred": False,
            "rendered_value": None,
            "linked_description": "<p>prose about the whole category</p>",
            "test": {"original_id": "31615"},
        }
    )

    scoped = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", group="900", include_prose=True
    )
    assert [c["original_id"] for c in scoped["data"]["commentary"]] == ["900"]

    # The requested group's own prose counts, and so does a group beneath it.
    category = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", group="31615", include_prose=True
    )
    assert {c["original_id"] for c in category["data"]["commentary"]} == {"900", "31615"}

    unbounded = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_prose=True
    )
    assert {c["original_id"] for c in unbounded["data"]["commentary"]} == {"900", "31615"}


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
    assert row["variants"] == [
        {"variation": '65"', "model": "XR-65A1"},
        {"variation": '75"', "model": "XR-75A1"},
    ]

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
    curve = next(v for v in product_rows(out) if v["original_id"] == "13907")
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
    from rtings_mcp.observations import PROVENANCE_ANONYMOUS, ObservationStore, ScoresAvailable

    store = ObservationStore(ctx.cache)
    wide = ScoresAvailable(has_insider=True)
    for _ in range(50):
        wide.insider_tests.add(unblurred=True)
    store.record("tv", "227", wide, provenance=PROVENANCE_ANONYMOUS)
    assert store.read("tv", "227").completeness == "full"

    narrow = ScoresAvailable(has_insider=True)
    narrow.insider_tests.add(unblurred=False)
    store.record("tv", "227", narrow, provenance=PROVENANCE_ANONYMOUS)
    assert store.read("tv", "227").insider_total == 50, "a 1-row read must not overwrite 50"

    flip = ScoresAvailable(has_insider=True)
    for _ in range(50):
        flip.insider_tests.add(unblurred=False)
    store.record("tv", "227", flip, provenance=PROVENANCE_ANONYMOUS)
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
    # Load it the way the server does (`Context.build`), rather than assigning the fields by
    # hand: the loader is what records `stored_at`, and the rotation write-back is a
    # compare-and-swap against it. Hand-building the state skipped that and made this test
    # pass against a credential shape the process never actually holds.
    member_ctx.transport.credential = load_credential(member_ctx.config)
    assert member_ctx.transport.credential.stored_at > 0
    member_ctx.transport.session_page = "member"
    member_ctx.transport.jar_cookie = "REISSUED-BY-RTINGS"

    probe = await member_ctx.auth.session_probe(force=True)
    assert probe.logged_in
    assert member_ctx.transport.credential.configured == "REISSUED-BY-RTINGS"
    assert load_credential(member_ctx.config).configured == "REISSUED-BY-RTINGS"


async def test_a_rotation_does_not_clobber_a_newer_cookie_from_another_process(member_ctx):
    """Two processes sharing a config dir each load the credential once, at startup. Without
    a compare-and-swap the one holding the stale baseline overwrites a rotation the other
    wrote seconds ago, and that process is left holding a value the server may have already
    replaced."""
    import time as _time

    from rtings_mcp.auth import load_credential, store_credential

    store_credential(member_ctx.config, "PASTED")
    member_ctx.transport.credential = load_credential(member_ctx.config)
    member_ctx.transport.credential.source = "file"

    # Another process rotates and stores a newer value.
    _time.sleep(0.01)
    store_credential(member_ctx.config, "STORED-BY-THE-OTHER-PROCESS")

    member_ctx.transport.session_page = "member"
    member_ctx.transport.jar_cookie = "OUR-OWN-REISSUE"
    probe = await member_ctx.auth.session_probe(force=True)

    assert probe.logged_in
    # Ours is adopted in memory — it is a real, proven-logged-in credential.
    assert member_ctx.transport.credential.configured == "OUR-OWN-REISSUE"
    # But the newer stored value is left alone.
    assert load_credential(member_ctx.config).configured == "STORED-BY-THE-OTHER-PROCESS"


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
                # A SUB-usage: it has a `product_score_sets` row (so its original_id is
                # discoverable) but NO entry in `score_sets` below — the shape that made
                # every mattress sleeping-position component report `component: null`.
                {
                    "id": "c",
                    # Null like its siblings: `user_has_access` is False on this fixture, so
                    # a score here would make the review partially visible and change what
                    # the gating tests are asserting about.
                    "score": None,
                    "suitable": True,
                    "score_set_id": "ss-sub",
                    "score_set__original_id": "77",
                    "linked_description": None,
                },
            ],
            "score_sets": [
                {
                    "id": "ss1",
                    "original_id": "1",
                    "name": "Mixed Usage",
                    "kind": "usage",
                    "items": [
                        {"score_set_id": "ss2", "test_id": None, "weight": 40.0},
                        {"score_set_id": "ss-sub", "test_id": None, "weight": 60.0},
                    ],
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
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True, include_scoring=True
    )
    mixed = next(s for s in out["data"]["scoring"] if s["name"] == "Mixed Usage")
    assert mixed["components"][0]["weight_pct"] == 40.0
    assert mixed["components"][0]["component"] == "Home Theater"


async def test_a_sub_usage_scoring_component_resolves_to_a_name(ctx):
    """A sub-usage's score set is absent from `score_sets`, so the component resolved to
    nothing and the recipe read "33.4% of something we won't name". Live on mattress, all
    three sleeping positions were composed entirely of such components. The id -> original_id
    mapping is in `product_score_sets` and the name is in the schema."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True, include_scoring=True
    )
    mixed = next(s for s in out["data"]["scoring"] if s["name"] == "Mixed Usage")
    sub = mixed["components"][1]
    assert sub["weight_pct"] == 60.0
    assert sub["component"] == "Legacy Usage"
    assert sub["original_id"] == "77", "the id must join back to rt_schema"
    assert sub["component_kind"] == "usage"


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
    for row in product_rows(out):
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
    assert "Early Access" in out["data"]["notice"]
    # `published:false` is Early Access and a membership DOES lift it. The notice used to
    # assert the opposite — "blurred for everyone, a membership does not lift it" — which is
    # the pre-correction reading, told to the user as fact.
    assert "does not lift" not in out["data"]["notice"]
    assert "for everyone" not in out["data"]["notice"]
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
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True, include_scoring=True
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


async def test_a_silo_outside_the_release_hint_still_works_and_warns(ctx):
    """SPEC §7 and CLAUDE.md both promise `silo_hint_drift`, and it was never emitted:
    `grep silo_hint_drift src/` found only a comment. Validation is against RTINGS' LIVE
    list, so a category added mid-release must work — the warning says the shipped tool
    description is stale, not that the data is wrong."""
    from rtings_mcp import repository as repo_module

    original = repo_module.SILO_HINT_SET
    repo_module.SILO_HINT_SET = frozenset(original - {"tv"})
    try:
        out = await services.rt_silos(ctx)
        assert not [w for w in out["warnings"] if "silo_hint_drift" in w], (
            "listing silos must not warn; the hint is about a REQUESTED silo"
        )
        out = await services.rt_schema(ctx, "tv")
    finally:
        repo_module.SILO_HINT_SET = original

    assert out["data"], "a silo outside the hint must still be served"
    assert any("silo_hint_drift" in w for w in out["warnings"]), out["warnings"]

    out = await services.rt_schema(ctx, "tv")
    assert not [w for w in out["warnings"] if "silo_hint_drift" in w]


async def test_a_stale_review_against_a_newer_schema_is_not_a_false_not_tested(ctx, monkeypatch):
    """A review body normally carries every test on its own bench, so `missing` is empty. It
    stops being empty exactly when a newer schema lists a test the cached review predates —
    and calling that `not_tested` asserts "RTINGS did not measure this" by comparing two
    documents of different ages. SPEC §8 promised a `bench_mismatch` guard; this is it."""

    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    by_name = {v["name"]: v for v in product_rows(out)}
    assert by_name["Peak Brightness"]["status"] == "not_tested", "fresh: a real answer"

    # Now make the cached review past-TTL, with a schema fetched after it.
    real_review = ctx.repo.review

    async def stale_review(*args, **kwargs):
        envelope, _ = await real_review(*args, **kwargs)
        return envelope, True

    monkeypatch.setattr(ctx.repo, "review", stale_review)
    monkeypatch.setattr(
        ctx.repo, "schema_meta", lambda silo: (time.time() + 3600, False)
    )

    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    by_name = {v["name"]: v for v in product_rows(out)}
    assert by_name["Peak Brightness"]["status"] == "coverage_unknown"
    assert any("bench_mismatch" in w for w in out["warnings"]), out["warnings"]


# -- the anonymous-label guard: member mode OFF, a member cookie live -------------------
#
# Measured 2026-09-06: with a member cookie stored and RTINGS_MEMBER_MODE off, a tv fetch
# came back 98/98 unblurred and was written as `tests/227/141.anonymous.<ts>.json`. The label
# promises "a signed-out session would have received these bytes"; it did not. And the same
# fetch's observation made `rt_silos()` call tv `full` on that machine, permanently.
#
# `ctx` here has the flag OFF; `member_probe(ctx)` injects the credential and the probe.


def anonymous_proof_of_open(ctx, bench="227", *, insider=20, usage=5):
    """What a signed-out fetch of an OPEN silo leaves behind (mattress-shaped)."""
    from rtings_mcp.observations import PROVENANCE_ANONYMOUS, ObservationStore, ScoresAvailable

    seen = ScoresAvailable(has_insider=True, has_usages=True)
    for _ in range(insider):
        seen.insider_tests.add(unblurred=True)
    for _ in range(usage):
        seen.usage_ratings.add(unblurred=True)
    ObservationStore(ctx.cache).record("tv", bench, seen, provenance=PROVENANCE_ANONYMOUS)


def unblurred_insider_payload(*pids):
    return {
        "data": {
            "test_results": [
                make_test_row(pid, "11", unblurred=True, value="5000", score=8.0) for pid in pids
            ]
        }
    }


async def test_member_data_is_served_but_never_written_under_an_anonymous_label(flag_off_ctx):
    """Defect 1, tv-shaped: no signed-out fetch has shown tv to be open, so the unblurred
    insider rows may be the membership's doing. The response still carries them — the
    caller asked and they are real — but no `anonymous` file is written, the call says so,
    and the next call fetches again rather than serving a lie as a hit."""
    member_probe(flag_off_ctx)
    flag_off_ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload(
        "1", "2"
    )

    out = await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    row = values(out, "1")["tests"][0]
    assert row["status"] == "tested_visible" and row["value"] == 5000.0, "served, not lost"
    assert out["data_tier"] == "unblurred"
    assert any(w.startswith("not_cached:") for w in out["warnings"]), out["warnings"]
    assert flag_off_ctx.cache.list_variants("tests", "227", key="11") == [], "nothing was written"

    before = flag_off_ctx.transport.calls.count("table_tool__test_results")
    again = await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    assert flag_off_ctx.transport.calls.count("table_tool__test_results") == before + 1
    assert values(again, "1")["tests"][0]["value"] == 5000.0


async def test_member_observations_never_become_the_published_completeness(ctx):
    """Defect 2: the same fetch used to make `rt_silos()` report tv as `full` — the routing
    signal the calling LLM is told to trust — and nothing would ever replace it, because
    with a stored credential no signed-out fetch of equal width happens again."""
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "2")
    await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])

    out = await services.rt_silos(ctx)
    tv = out["data"]["silos"][0]
    assert tv["data_completeness"] == "unknown", "never `full` off a signed-in fetch"
    assert tv["observed"]["provenance"] == "logged_in"
    assert tv["observed"]["insider_unblurred_ratio"] == 1.0, "the counts are still reported"


async def test_a_signed_out_proof_of_open_lets_the_signed_in_write_through(flag_off_ctx):
    """The trap, mattress-shaped: 16 of 28 silos serve insider rows unblurred anonymously,
    so a member's bytes there ARE what anonymous gets and refusing would refuse every
    legitimate write on those silos whenever a user is signed in. With a signed-out
    observation of the same bench on disk, the write goes through, silently, and the
    signed-in fetch's own observation does not overwrite the signed-out one."""
    anonymous_proof_of_open(flag_off_ctx)
    member_probe(flag_off_ctx)
    flag_off_ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload(
        "1", "2"
    )

    out = await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    assert values(out, "1")["tests"][0]["value"] == 5000.0
    assert not any(w.startswith("not_cached:") for w in out["warnings"]), out["warnings"]
    tiers = {v.tier for v in flag_off_ctx.cache.list_variants("tests", "227", key="11")}
    assert tiers == {"anonymous"}, "an honest anonymous write"

    before = flag_off_ctx.transport.calls.count("table_tool__test_results")
    await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    assert flag_off_ctx.transport.calls.count("table_tool__test_results") == before, "a real hit"

    silos = await services.rt_silos(flag_off_ctx)
    assert silos["data"]["silos"][0]["data_completeness"] == "full"
    assert silos["data"]["silos"][0]["observed"]["provenance"] == "anonymous"


async def test_an_unblurred_early_access_row_is_refused_even_with_the_proof(flag_off_ctx):
    """Early Access is blurred for anonymous on every silo and a membership lifts it, so a
    member's unblurred row for product 3 (`published:false`) is member-only data even on
    an open silo. The row is still served — as `tested_visible`, which is what it is for
    this session — but it is not filed as what anonymous gets."""
    anonymous_proof_of_open(flag_off_ctx)
    member_probe(flag_off_ctx)
    flag_off_ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload(
        "1", "3"
    )

    out = await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    assert values(out, "3")["tests"][0]["status"] == "tested_visible"
    assert values(out, "3")["tests"][0]["value"] == 5000.0
    assert any("Early Access" in w for w in out["warnings"]), out["warnings"]
    assert flag_off_ctx.cache.list_variants("tests", "227", key="11") == []


async def test_the_guard_is_inert_for_an_anonymous_session(ctx):
    """No credential, open-silo-shaped response: written, no warning, no extra request —
    the ordinary user must see no behaviour change at all, and `rt_silos()` learns the
    silo is open from their fetch exactly as before."""
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "2")

    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])
    assert values(out, "1")["tests"][0]["value"] == 5000.0
    assert out["warnings"] == []
    probe = ctx.auth.cached_probe()
    assert probe.session == "anonymous" and probe.source_url == "", "no HTML probe ran"
    assert {v.tier for v in ctx.cache.list_variants("tests", "227", key="11")} == {"anonymous"}

    silos = await services.rt_silos(ctx)
    assert silos["data"]["silos"][0]["data_completeness"] == "full"
    assert silos["data"]["silos"][0]["observed"]["provenance"] == "anonymous"


async def test_usage_scores_unblurred_on_a_signed_in_session_are_not_written(flag_off_ctx):
    """The ratings surface has neither `status` nor `insider_only`, so it needs its own
    branch: every unblurred usage row is gate-relevant, and the proof is the signed-out
    observation of the USAGE surface, not of the insider tests."""
    member_probe(flag_off_ctx)  # the stock payload already carries an unblurred score for product 1
    out = await services.rt_ratings(flag_off_ctx, "tv", usages=["1"])
    assert values(out, "1")["usage_scores"][0]["score"] == 8.1
    assert any("ratings" in w and w.startswith("not_cached:") for w in out["warnings"])
    assert flag_off_ctx.cache.list_variants("ratings", "227", key="1") == []

    anonymous_proof_of_open(flag_off_ctx)
    out = await services.rt_ratings(flag_off_ctx, "tv", usages=["1"])
    assert not any(w.startswith("not_cached:") for w in out["warnings"])
    tiers = {v.tier for v in flag_off_ctx.cache.list_variants("ratings", "227", key="1")}
    assert tiers == {"anonymous"}


async def test_a_review_unblurred_on_a_signed_in_session_is_not_written(flag_off_ctx):
    """The review path writes `reviews/` with the same label and has the same exposure — a
    member's page body on tv carries every insider value. Served; not cached."""
    member_probe(flag_off_ctx)
    page = flag_off_ctx.transport.payloads["app/product_vue_page__page_body"]
    rows = page["data"]["page"]["product"]["review"]["test_results"]
    rows[1] = {
        "status": "tested",
        "unblurred": True,
        "rendered_value": "1873 cd/m²",
        "score": 8.8,
        "test": {"original_id": "11"},
    }
    out = await services.rt_product(flag_off_ctx, "/tv/reviews/alpha/alpha-one")
    gated = [v for v in product_rows(out) if v["original_id"] == "11"]
    assert gated and gated[0]["status"] == "tested_visible"
    assert any(w.startswith("not_cached:") for w in out["warnings"]), out["warnings"]
    assert flag_off_ctx.cache.list_variants("reviews", key="1") == []

    before = flag_off_ctx.transport.calls.count("app/product_vue_page__page_body")
    await services.rt_product(flag_off_ctx, "/tv/reviews/alpha/alpha-one")
    assert flag_off_ctx.transport.calls.count("app/product_vue_page__page_body") == before + 1


async def test_a_blurred_review_on_a_signed_in_session_is_still_written(ctx):
    """Vacuous under the predicate: nothing came through that anonymous would not get, so
    the write is honest and a member with a lapsed session is not refused forever."""
    member_probe(ctx)
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert not any(w.startswith("not_cached:") for w in out["warnings"])
    assert ctx.cache.list_variants("reviews", key="1")


async def test_verdict_scores_unblurred_on_a_signed_in_session_are_not_written(flag_off_ctx):
    """Verdict scores are usage ratings by another name, and `verdicts/` is the surface
    that had no demotion at all. A non-null score under a session that may have unblurred
    it, on a silo not proven to serve usage scores anonymously, is not filed as anonymous."""
    import copy

    member_probe(flag_off_ctx)
    payload = copy.deepcopy(SBS_PAYLOAD)
    payload["data"]["review"]["user_has_access"] = True
    payload["data"]["review"]["product_score_sets"][0]["score"] = 8.2
    flag_off_ctx.transport.payloads["app/side_by_side__review"] = payload

    out = await services.rt_product(
        flag_off_ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    assert out["data"]["verdicts"][0]["score"] == 8.2
    assert any("verdicts" in w and w.startswith("not_cached:") for w in out["warnings"])
    assert flag_off_ctx.cache.list_variants("verdicts", key="1") == []

    anonymous_proof_of_open(flag_off_ctx)
    out = await services.rt_product(
        flag_off_ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True
    )
    assert not any("verdicts" in w and w.startswith("not_cached:") for w in out["warnings"])
    assert flag_off_ctx.cache.list_variants("verdicts", key="1")


async def test_a_refused_slice_is_served_only_within_the_call_that_fetched_it(flag_off_ctx):
    """The hold is per call, like warnings: the repository is process-wide, and an instance
    dict would serve one call's member-only rows to the next call as a 'hit'."""
    member_probe(flag_off_ctx)
    flag_off_ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload(
        "1", "2"
    )
    await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    assert flag_off_ctx.repo.read_slice("tests", "227", "11", demand="anonymous") is None
    assert flag_off_ctx.repo.slice_meta("tests", "227", "11", demand="anonymous") is None


async def test_a_refused_slice_is_not_refetched_within_the_same_call(ctx):
    """Inside one call the hold stands in for the file: a second `ensure_*` for the same
    pair must not fetch again, or a tool that touches a pair twice spends two requests."""
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "2")
    with ctx.repo.warning_scope():
        await ctx.repo.ensure_test_slices("tv", ["227"], ["11"])
        before = ctx.transport.calls.count("table_tool__test_results")
        await ctx.repo.ensure_test_slices("tv", ["227"], ["11"])
        assert ctx.transport.calls.count("table_tool__test_results") == before
        assert ctx.repo.read_slice("tests", "227", "11", demand="anonymous")["1"].row["value"]


async def test_uncatalogued_rows_get_the_same_guard(flag_off_ctx):
    """With the flag off, `_unassigned` files are `anonymous`-labelled like everything else
    and hold real rows for products outside every catalog generation — on tv those are
    blurred anonymously, so a member's unblurred ones are member-only too. Served in the
    uncatalogued group; not written."""
    ctx = flag_off_ctx
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "99")
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[], include_uncatalogued=True)
    orphan = [g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    assert orphan and orphan[0]["products"][0]["product_id"] == "99"
    assert orphan[0]["products"][0]["tests"][0]["value"] == 5000.0
    assert ctx.cache.list_variants("tests", "_unassigned", key="11") == []


# -- member mode ON: the `_unassigned` bucket carries the response's tier ---------------


def tiers_of(ctx, directory, bench, key):
    return {v.tier for v in ctx.cache.list_variants(directory, bench, key=key)}


async def test_uncatalogued_rows_are_written_at_the_response_tier_with_member_mode_on(ctx):
    """Measured live 2026-09-06 after the flag flipped: `tests/227/141.member.<ts>.json`
    was written and hit, yet a `not_cached` warning fired for the 9 uncatalogued products
    on every call. The `_unassigned` envelope was built BEFORE the tier was resolved and so
    hardcoded `anonymous`, which the label guard then (correctly) refused — forever, since
    nothing about the next call differed. Same response, same tier: the orphan bucket is
    written `member` beside the catalogued ones, with no warning, and the next call hits."""
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "99")
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[], include_uncatalogued=True)
    orphan = [g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    assert orphan and orphan[0]["products"][0]["tests"][0]["value"] == 5000.0
    assert not any(w.startswith("not_cached:") for w in out["warnings"]), out["warnings"]
    assert tiers_of(ctx, "tests", "227", "11") == {"member"}
    assert tiers_of(ctx, "tests", "_unassigned", "11") == {"member"}, "same tier as the rest"

    before = ctx.transport.calls.count("table_tool__test_results")
    again = await services.rt_ratings(
        ctx, "tv", tests=["11"], usages=[], include_uncatalogued=True
    )
    assert ctx.transport.calls.count("table_tool__test_results") == before, "a real hit"
    assert not any(w.startswith("not_cached:") for w in again["warnings"]), again["warnings"]
    orphan = [g for g in again["data"]["groups"] if g.get("coverage") == "uncatalogued"]
    assert orphan and orphan[0]["products"][0]["tests"][0]["value"] == 5000.0


async def test_uncatalogued_rows_are_demoted_with_the_rest_of_the_response(ctx):
    """Write-time demotion applies to the orphan bucket too: a member probe whose response
    came back fully withheld must not leave a `member` orphan file beside demoted
    `anonymous` catalogued ones — a hit on it would serve nulls as member data for the TTL.
    All-blurred is vacuous under the label guard, so nothing is refused or warned."""
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {"test_results": [make_test_row("1", "11"), make_test_row("99", "11")]}
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])
    assert not any(w.startswith("not_cached:") for w in out["warnings"]), out["warnings"]
    assert tiers_of(ctx, "tests", "227", "11") == {"anonymous"}
    assert tiers_of(ctx, "tests", "_unassigned", "11") == {"anonymous"}


# -- the refusal warning names the real reason for the label --------------------------


async def test_a_flag_off_refusal_says_the_flag_is_off(flag_off_ctx):
    member_probe(flag_off_ctx)
    flag_off_ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload(
        "1", "2"
    )
    out = await services.rt_ratings(flag_off_ctx, "tv", tests=["11"], usages=[])
    warning = next(w for w in out["warnings"] if w.startswith("not_cached:"))
    assert "RTINGS_MEMBER_MODE is off" in warning
    assert "Enable RTINGS_MEMBER_MODE" in warning


async def test_a_flag_on_refusal_never_tells_the_user_to_enable_the_flag(ctx):
    """A free account on the table path: the flag is on, but `free` justifies no tier
    above anonymous there, so an unblurred insider row is refused — and the old wording
    told the user to enable a flag that was already enabled."""
    free_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "2")
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])
    assert values(out, "1")["tests"][0]["value"] == 5000.0
    warning = next(w for w in out["warnings"] if w.startswith("not_cached:"))
    assert "'free'" in warning and "rt_auth_status" in warning
    assert "RTINGS_MEMBER_MODE" not in warning
    assert ctx.cache.list_variants("tests", "227", key="11") == []


async def test_a_demoted_refusal_names_the_demotion(ctx):
    """Member probe, catalogued rows withheld (so the slice demotes to anonymous), but an
    Early Access row came through unblurred: the label guard refuses on the Early Access
    row, and the warning must say the slice was demoted rather than blame the flag."""
    member_probe(ctx)
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11"),
                make_test_row("3", "11", unblurred=True, value="5000", score=8.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])
    warning = next(w for w in out["warnings"] if w.startswith("not_cached:"))
    assert "demoted" in warning and "Early Access" in warning
    assert "RTINGS_MEMBER_MODE" not in warning
    assert ctx.cache.list_variants("tests", "227", key="11") == []


async def test_the_tier_is_labelled_from_a_re_probe_taken_after_the_fetch(ctx):
    """The re-probe that precedes any tier-keyed write is unthrottled and must run AFTER
    the fetch: a probe from before it cannot vouch for what the session was when the rows
    came back. Here the in-memory probe says `member` (and is far too fresh for the
    throttled path to refresh), but the page now says logged out. Every file from the
    response — the catalogued bucket and the orphan one alike — must carry a tier the
    re-probe can justify, never `member` off the stale reading."""
    member_probe(ctx)
    ctx.transport.session_page = "anonymous"  # the configured cookie now reads logged out
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1", "99")
    await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])

    calls = ctx.transport.calls
    post = calls.index("table_tool__test_results")
    assert "GET /tv/tools/table" in calls[post + 1 :], "no re-probe after the fetch"
    assert ctx.auth.cached_probe().session == "expired"
    assert "member" not in tiers_of(ctx, "tests", "227", "11")
    assert "member" not in tiers_of(ctx, "tests", "_unassigned", "11")


# -- the shopper round (2026-09-06) -----------------------------------------------------
# Five agents answered real shopping questions through the MCP wire. Each test below is one
# thing they tripped over.


async def test_product_ids_filter_selects_exactly_those_products(ctx):
    """"Compare exactly these two" had no path: agents guessed a `name_contains` substring
    after an rt_search, which matched siblings. Ids are identity, catalogued or not."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"product_ids": ["2"]})
    assert out["data"]["total_matched"] == 1
    assert values(out, "2")["name"] == "Alpha Two"
    assert not any("filter_unavailable" in w for w in out["warnings"]), out["warnings"]

    # An uncatalogued id asked for by name gets its rows without a second flag.
    out = await services.rt_ratings(
        ctx, "tv", tests=["208"], filters={"product_ids": "99"}
    )
    orphan = next(g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued")
    assert [p["product_id"] for p in orphan["products"]] == ["99"]
    assert out["data"]["total_matched"] == 0, "the catalogued group matched nothing"


async def test_nested_rows_do_not_repeat_the_product_id(ctx):
    """Rows under a product all belong to it; repeating the id per row was bytes."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    row = values(out, "1")
    assert row["product_id"] == "1"
    assert all("product_id" not in t for t in row["tests"])
    assert all("product_id" not in u for u in row["usage_scores"])


async def test_an_oversized_window_is_trimmed_with_a_warning_not_dropped(ctx):
    """Three of five agents lost their first ranking call to the client's tool-result cap:
    no partial result, nothing saying which knob to turn. The server trims per group from
    the tail and says where to page from."""
    ctx.transport.payloads["table_tool__products_list"] = {
        "data": {"products": [product(str(i), f"Alpha {i}") for i in range(1, 40)]}
    }
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row(str(i), "208", unblurred=True, value="4k", score=9.0)
                for i in range(1, 40)
            ]
        }
    }
    ctx.config.max_response_chars = 6_000
    out = await services.rt_ratings(ctx, "tv", tests=["208"], limit=30)
    assert out["error"] is None
    group = out["data"]["groups"][0]
    assert group["matched"] == 39, "the count still describes the whole population"
    served = len(group["products"])
    assert 1 <= served < 30
    assert out["data"]["truncated_to"] == served
    assert out["data"]["returned"] == served
    warning = next(w for w in out["warnings"] if w.startswith("response_truncated"))
    assert f"offset={served}" in warning
    assert "RTINGS_MAX_RESPONSE_CHARS" in warning
    # The head of the ranking survives: the default sort is release date desc, which the
    # fixture ties, so the first served row is the first row of the untrimmed window.
    ctx.config.max_response_chars = 400_000
    full = await services.rt_ratings(ctx, "tv", tests=["208"], limit=30)
    assert [p["product_id"] for p in group["products"]] == [
        p["product_id"] for p in full["data"]["groups"][0]["products"][:served]
    ]
    assert "truncated_to" not in full["data"]


async def test_graph_header_and_axes_come_from_the_series_labels_when_there_is_no_header(ctx):
    """Headphones and monitor curves ship with no `header` key: 13 unlabelled columns that
    two agents could not interpret. The labels are at options.series[].label."""

    async def new_shape(path):
        return {
            "data": [[20, 9.8, 10.7], [40, 9.2, 10.1]],
            "options": {
                "series": [{"label": "Left"}, {"label": "Right"}],
                "x": {"title": "Frequency (Hz)", "scale": "log"},
                "y": {"title": "Amplitude (dBr)", "scale": "linear"},
            },
        }

    ctx.transport.cdn_get_json = new_shape
    out = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    assert out["error"] is None, out["error"]
    assert out["data"]["header"] == ["Frequency (Hz)", "Left", "Right"]
    assert out["data"]["axes"] == {
        "x": {"title": "Frequency (Hz)", "scale": "log"},
        "y": {"title": "Amplitude (dBr)", "scale": "linear"},
    }


async def test_the_google_charts_graph_shape_keeps_its_own_header(ctx):
    """The older shape has a literal header AND a `series` list indexed "0", "1", … —
    styling slots, not names. They must not replace the header."""

    async def old_shape(path):
        return {
            "header": ["Input Stimulus", "PQ EOTF Target"],
            "data": [[0, 0], [1, 1]],
            "options": {
                "series": [{"label": "0"}, {"label": "1"}],
                "hAxis": {"title": "Signal Input Stimulus"},
            },
        }

    ctx.transport.cdn_get_json = old_shape
    out = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    assert out["data"]["header"] == ["Input Stimulus", "PQ EOTF Target"]
    assert out["data"]["axes"]["x"] == {"title": "Signal Input Stimulus", "scale": None}


async def test_a_word_vocabulary_is_capped_with_its_true_count():
    """Mattress "Firmness Level" carries ~100 distinct display strings; dumping them all made
    the group's schema mostly enum."""
    from dataclasses import replace

    from rtings_mcp.schema import parse_column_options
    from rtings_mcp.services import MAX_SCHEMA_WORDS, _test_json

    schema = parse_column_options(
        "tv", json.loads((FIXTURES / "column_options_min.json").read_text())
    )
    test = schema.test("208")
    many = replace(test, words=tuple(f"Word {i}" for i in range(MAX_SCHEMA_WORDS + 10)))
    out = _test_json(schema, many)
    assert len(out["words"]) == MAX_SCHEMA_WORDS
    assert out["words_total"] == MAX_SCHEMA_WORDS + 10
    assert "words_total" not in _test_json(schema, test)


async def test_rt_product_identifies_an_uncatalogued_id_through_the_compare_tool(ctx):
    """The uncatalogued group's notice sent callers to rt_product by id, and rt_product
    answered `unknown_product` — a dead end the server itself recommended. The compare tool
    resolves a bare id and its `product` block carries the review URL."""
    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["product"] = {
        "id": "99",
        "fullname": "Alpha Ninety-Nine",
        "product_page__url": "/tv/reviews/alpha/alpha-ninety-nine",
        "silo__url_part": "tv",
        "product_page__early_access": False,
    }
    ctx.transport.payloads["app/side_by_side__review"] = payload
    ref = await ctx.repo.resolve_product("99", silo="tv")
    assert ref.name == "Alpha Ninety-Nine"
    assert ref.url_path == "/tv/reviews/alpha/alpha-ninety-nine"
    assert ref.bench_id == "227"
    assert ref.published is True
    assert "app/side_by_side__review" in ctx.transport.calls
    # And the tool itself no longer dead-ends on the id its own notice recommended.
    out = await services.rt_product(ctx, "99", silo="tv")
    assert out["error"] is None, out["error"]

    # A catalogued id never takes the detour.
    calls_before = ctx.transport.calls.count("app/side_by_side__review")
    assert (await ctx.repo.resolve_product("1", silo="tv")).name == "Alpha One"
    assert ctx.transport.calls.count("app/side_by_side__review") == calls_before


async def test_recommendation_lists_are_tier_keyed(ctx, tmp_path):
    """A best-of page carries each pick's `unblurred` bits, so it is session-dependent. It
    was cached untiered, and a member was served a two-day-old anonymous copy — every
    featured score `tested_gated` — with nothing saying a refresh would help."""
    anon = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert anon["data"]["picks"][0]["featured_results"][0]["status"] == "tested_gated"
    files = sorted(p.name for p in (ctx.config.cache_dir / "recs" / "tv").iterdir())
    assert any(".anonymous." in f for f in files), files

    # Now a member. The anonymous file no longer satisfies the demand tier, so the page is
    # fetched again — and, unblurred this time, written under `member`.
    member_probe(ctx)
    ctx.transport.rec_html = REC_HTML.replace(
        _props_fragment('"unblurred": false'), _props_fragment('"unblurred": true')
    ).replace(
        _props_fragment('"rendered_value": null'),
        _props_fragment('"rendered_value": "5000:1"'),
    )
    assert ctx.transport.rec_html != REC_HTML, "the fixture fragment must have matched"
    calls_before = len(ctx.transport.calls)
    member = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert len(ctx.transport.calls) > calls_before, "a member must not be served the anon copy"
    assert member["data"]["picks"][0]["featured_results"][0]["status"] == "tested_visible"
    files = sorted(p.name for p in (ctx.config.cache_dir / "recs" / "tv").iterdir())
    assert any(".member." in f for f in files), files

    # And the member copy is a hit for the member.
    calls_before = len(ctx.transport.calls)
    again = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert len(ctx.transport.calls) == calls_before
    assert again["from_cache"] is True


def _props_fragment(json_text: str) -> str:
    """The HTML-escaped form of a JSON fragment inside a `data-props` attribute."""
    return html_module.escape(json_text, quote=True)


async def test_a_blurred_best_of_page_under_a_member_probe_is_demoted(ctx):
    """The probe and the fetch race here too: a member probe with a page that came back
    blurred must not be stamped `member`, or the nulls are served for the full TTL."""
    member_probe(ctx)
    out = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert out["data"]["picks"][0]["featured_results"][0]["status"] == "tested_gated"
    files = sorted(p.name for p in (ctx.config.cache_dir / "recs" / "tv").iterdir())
    assert all(".member." not in f for f in files), files


async def test_a_group_with_no_scored_test_says_where_its_content_lives(ctx):
    """Monitor "Text Clarity" and robot-vacuum "Pet Hair Pickup" are groups with no leaf
    test. Left unmarked, agents drilled into them and then guessed."""
    from dataclasses import replace

    from rtings_mcp.services import NO_LEAF_NOTE, _schema_tree

    schema = await ctx.repo.schema("tv")
    tests = schema.tests_for_bench("227")
    empty = replace(
        schema.test("900"),
        original_id="901",
        name="Text Clarity",
        parent_original_id=None,
        derived_category_id=None,
    )
    tree = _schema_tree(schema, [*tests, empty])
    by_name = {node["name"]: node for node in tree}
    assert by_name["Text Clarity"]["leaf_test_count"] == 0
    assert by_name["Text Clarity"]["no_scored_tests"] is True
    assert all("no_scored_tests" not in node for node in tree if node["name"] != "Text Clarity")
    top = await services.rt_schema(ctx, "tv")
    assert NO_LEAF_NOTE in top["data"]["notice"]



async def test_rt_silos_tells_a_member_the_completeness_column_is_not_about_them(ctx):
    member_probe(ctx)
    out = await services.rt_silos(ctx)
    assert "SIGNED IN AS AN INSIDER" in out["data"]["notice"]
    anon_ctx_notice = (await services.rt_silos(ctx))["data"]["notice"]
    assert anon_ctx_notice.startswith("data_completeness is derived")


async def test_the_scoring_recipe_is_opt_in(ctx):
    """A third of every verdicts response was the weight recipe, and no shopper question
    used it. The notice says how to get it."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True)
    assert out["data"]["scoring"] is None
    assert "include_scoring=true" in out["data"]["verdicts_notice"]
    assert out["data"]["verdicts"], "the verdicts themselves are unaffected"


# -- the shopper round, batch 1 (2026-09-06) --------------------------------------------


async def test_an_infinite_reading_is_carried_honestly_on_both_paths(ctx):
    """RTINGS reports an OLED's contrast as `value: "Inf"`, `rendered_value: "Inf : 1"`.
    The table path turned that into a null that read as an empty row; the review path
    parsed the "1" out of the unit text and served 1.0 — the worst possible contrast — for
    the two best TVs a dark-room shopper was comparing."""
    from rtings_mcp.normalize import parse_rendered_number

    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                {**make_test_row("1", "11", unblurred=True, value="Inf", score=10.0),
                 "rendered_value": "Inf : 1"},
                make_test_row("2", "11", unblurred=True, value="5000", score=8.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[], sort="-11")
    rows = [p for g in out["data"]["groups"] for p in g["products"] if p.get("name")]
    first = rows[0]["tests"][0]
    assert rows[0]["product_id"] == "1", "infinite ranks above every finite value"
    assert first["status"] == "tested_visible"
    assert first["value"] is None and first["is_infinite"] is True
    assert first["display"] == "Inf : 1"
    assert first["gated"] is False
    assert "infinite" in first["warning"]
    assert "Infinity" not in json.dumps(out), "JSON has no infinity; the wire must not carry one"

    matched = await services.rt_ratings(
        ctx, "tv", tests=["11"], usages=[], filters={"11": ">10000"}
    )
    assert [p["product_id"] for g in matched["data"]["groups"] for p in g["products"]] == ["1"]

    schema = await ctx.repo.schema("tv")
    value, warning = parse_rendered_number(schema.test("11"), "Inf : 1")
    assert value == float("inf") and warning is None
    value, _ = parse_rendered_number(schema.test("11"), "-Inf")
    assert value == float("-inf")
    value, _ = parse_rendered_number(schema.test("11"), "49,776 : 1")
    assert value == 49776.0


async def test_a_converted_test_reports_the_unit_of_its_value(ctx):
    """Monitor Height Adjustment: `value: 10.7` labelled "inches" beside `display: 4.2"
    (10.7 cm)`. The machine value is in `number_input_unit`; the display unit is another."""
    from dataclasses import replace

    from rtings_mcp.normalize import normalize_table_row
    from rtings_mcp.schema import (
        cacheable_is_current,
        schema_from_cacheable,
        schema_to_cacheable,
    )
    from rtings_mcp.services import _test_json

    schema = await ctx.repo.schema("tv")
    converted = replace(
        schema.test("11"),
        number_input_unit="centimeters",
        number_input_precision=1,
        number_display_unit="inches",
        number_display_precision=1,
    )
    row = normalize_table_row(
        make_test_row("1", "11", unblurred=True, value="10.7"), converted, schema=schema
    ).to_json()
    assert row["value"] == 10.7
    assert row["unit"] == "centimeters"
    assert row["display_unit"] == "inches"

    definition = _test_json(schema, converted)
    assert definition["unit"] == "centimeters" and definition["display_unit"] == "inches"
    plain = _test_json(schema, schema.test("11"))
    assert "display_unit" not in plain

    # The cacheable form round-trips the input unit, and an older form is not current.
    schema.tests["11"] = converted
    cacheable = schema_to_cacheable(schema)
    assert cacheable_is_current(cacheable)
    back = schema_from_cacheable(cacheable)
    assert back.test("11").value_unit == "centimeters"
    cacheable.pop("cacheable_version")
    assert not cacheable_is_current(cacheable)


async def test_an_old_cacheable_schema_is_refetched_not_served(ctx):
    """A cached parse from before the unit fields were stored would mislabel every
    converted value for the rest of its 30-day TTL."""
    await ctx.repo.schema("tv")
    calls = ctx.transport.calls.count("table_tool__column_options")
    path = ctx.config.cache_dir / "schema" / "tv.json"
    stored = json.loads(path.read_text())
    stored["payload"].pop("cacheable_version")
    path.write_text(json.dumps(stored))
    ctx.repo._schema_memo.clear()
    await ctx.repo.schema("tv")
    assert ctx.transport.calls.count("table_tool__column_options") == calls + 1


async def test_a_requested_product_id_that_is_absent_is_explained(ctx):
    """Three ids in, two rows out and no word about the third — which was tested on a
    bench outside the recent set. `matched` shrinking silently reads as "never tested"."""
    payload = json.loads(json.dumps(SBS_PAYLOAD))
    payload["data"]["review"]["test_bench"] = {"id": "2", "name": "1.0", "tests": []}
    payload["data"]["review"]["product"] = {
        "id": "99",
        "fullname": "Alpha Legacy",
        "product_page__url": "/tv/reviews/alpha/alpha-legacy",
        "silo__url_part": "tv",
        "product_page__early_access": False,
    }
    ctx.transport.payloads["app/side_by_side__review"] = payload
    out = await services.rt_ratings(
        ctx, "tv", tests=["208"], filters={"product_ids": ["1", "99"]}
    )
    assert out["data"]["total_matched"] == 1
    explained = [w for w in out["warnings"] if w.startswith("product_ids: 99")]
    assert explained and "Alpha Legacy" in explained[0] and "bench=['2']" in explained[0]
    assert "rt_product(" in explained[0]

    # An id nobody has: excluded, not unmatched.
    ctx.transport.payloads.pop("app/side_by_side__review")
    out = await services.rt_ratings(
        ctx, "tv", tests=["208"], filters={"product_ids": ["1", "424242"]}
    )
    assert any("no RTINGS tv product has id '424242'" in w for w in out["warnings"])


async def test_rt_product_can_serve_the_words_without_the_rows(ctx):
    """One verdicts-and-prose call was 129 K characters, 75 K of them rows the caller
    already had from rt_ratings."""
    ctx.transport.payloads["app/side_by_side__review"] = SBS_PAYLOAD
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", include_verdicts=True, include_results=False
    )
    assert out["error"] is None
    assert out["data"]["results"] == [] and out["data"]["result_count"] == 0
    assert out["data"]["verdicts"]


async def test_featured_media_rows_are_not_reported_as_results(ctx):
    """Five of eleven featured rows per PS5-list pick were pictures and graphs with every
    field null."""
    from rtings_mcp.services import _featured_results

    rows = [
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": "5000 : 1",
            "score": 9.0,
            "test": {"name": "Native Contrast", "kind": "number", "insider_only": True},
        },
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": None,
            "score": None,
            "test": {"name": "Pre Color Picture", "kind": "picture", "insider_only": True},
        },
        {
            "status": "tested",
            "unblurred": False,
            "rendered_value": None,
            "score": None,
            "test": {"name": "PQ EOTF Graph", "kind": "graph", "insider_only": True},
        },
    ]
    out = _featured_results(rows)
    assert [r["name"] for r in out] == ["Native Contrast"]
    assert out[0]["display"] == "5000 : 1"


async def test_the_uncatalogued_notice_names_no_other_category(ctx):
    """The notice quoted a TV and a mattress as examples on a headphones response, which
    read as cross-category leakage."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"])
    orphan = next(g for g in out["data"]["groups"] if g.get("coverage") == "uncatalogued")
    assert "LG G5" not in orphan["notice"] and "Boring" not in orphan["notice"]
    assert "(Copy)" in orphan["notice"]
    assert not any("LG G5" in w for w in out["warnings"])


async def test_a_repeated_test_name_must_be_qualified(ctx):
    """headphones has three leaves called "RMS Deviation From Target"; a bare name silently
    took the first, ranking the bass band for a caller who asked for treble."""
    from dataclasses import replace

    from rtings_mcp.services import _field_lookup

    schema = await ctx.repo.schema("tv")
    twin = replace(schema.test("12000"), original_id="12001", parent_original_id="31615")
    schema.tests["12001"] = twin
    with pytest.raises(RtingsError) as excinfo:
        _field_lookup(schema, "Peak Brightness")
    assert excinfo.value.code == "unknown_test"
    assert len(excinfo.value.details["matches"]) == 2
    assert _field_lookup(schema, "Picture/Peak Brightness") == ("test", "12001")
    assert _field_lookup(schema, "Native Contrast") == ("test", "11"), "unique names still resolve"


async def test_rt_schema_find_searches_the_bench_by_name(ctx):
    out = await services.rt_schema(ctx, "tv", find="contrast")
    names = [t["name"] for t in out["data"]["tests"]]
    assert "Native Contrast" in names
    assert out["data"]["test_matches"] == len(names)
    assert all("hierarchy" in t for t in out["data"]["tests"])
    empty = await services.rt_schema(ctx, "tv", find="no such thing")
    assert empty["data"]["tests"] == [] and "NAMING miss" in empty["data"]["notice"]


async def test_rt_silos_can_return_one_row(ctx):
    out = await services.rt_silos(ctx, silos=["tv"])
    assert [s["silo"] for s in out["data"]["silos"]] == ["tv"]
    none = await services.rt_silos(ctx, silos=["not-a-silo"])
    assert none["data"]["silos"] == [] and none["data"]["notice"].startswith("none of")


async def test_recommendation_picks_can_be_capped(ctx):
    out = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market", limit=1)
    assert len(out["data"]["picks"]) == 1
    assert out["data"]["pick_count"] >= 1


async def test_find_matches_group_names_and_word_stems(ctx):
    """"print speed" found only "Scan Speed": the Printing Speed group's tests are named
    "Black Only Text Document". Words are matched over the whole path and ranked."""
    from rtings_mcp.services import _find_words

    assert _find_words("cost per page") == ["cost", "page"]
    assert _find_words("Printing Speed") == ["print", "speed"]
    out = await services.rt_schema(ctx, "tv", find="picture quality brightness")
    names = [t["name"] for t in out["data"]["tests"]]
    assert "Peak Brightness" in names, names
    assert names[0] == "Peak Brightness", "the test matching the most words ranks first"


def test_reasoning_wrapper_divs_are_stripped():
    from rtings_mcp.services import _strip_wrappers

    assert (
        _strip_wrappers('<div><div class="x"><p>Keep <a href="/tv">this</a>.</p></div></div>')
        == '<p>Keep <a href="/tv">this</a>.</p>'
    )
    assert _strip_wrappers(None) is None


async def test_recommendation_prose_can_be_left_out(ctx):
    out = await services.rt_recommendations(
        ctx, "tv", list="tvs-on-the-market", include_reasoning=False
    )
    pick = out["data"]["picks"][0]
    assert "reasoning" not in pick or pick["reasoning"] is None
    assert pick["name"] == "Alpha One" and pick["featured_results"]


async def test_ratings_rows_carry_the_answer_and_the_legend_carries_the_definition(ctx):
    """3 tests x 83 switches fitted 11 products in the budget because every row repeated
    its test's name, unit, hierarchy and flags. Those live once in `data.tests` now."""
    # The uncatalogued rows follow the same shape (checked first, on the stock payload).
    orphans = await services.rt_ratings(
        ctx, "tv", tests=["208"], include_uncatalogued=True
    )
    orphan = next(g for g in orphans["data"]["groups"] if g.get("coverage") == "uncatalogued")
    assert "hierarchy" not in orphan["products"][0]["tests"][0]

    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="7000", score=9.0),
                make_test_row("2", "11", unblurred=True, value="5000", score=7.0),
                make_test_row("3", "11", unblurred=True, value="1000", score=3.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], usages=["1"], refresh=True)
    row = values(out, "1")["tests"][0]
    assert set(row) <= {
        "original_id", "status", "value", "gated", "score", "display", "as_of",
        "warning", "is_infinite", "infinity_sign", "superseded_at", "value_kind",
    }, sorted(row)
    legend = out["data"]["tests"]["11"]
    assert legend["name"] == "Native Contrast" and legend["kind"] == "number"
    assert legend["unit"] == ": 1" and legend["insider_only"] is True
    assert legend["hierarchy"]
    assert legend["score_direction"] == "higher_is_better"
    assert out["data"]["usages"]["1"]["name"]


def test_score_direction_reads_lower_is_better_off_the_scores():
    from rtings_mcp.services import _score_direction

    def row(value, score):
        return {
            "tests": [
                {"original_id": "x", "status": "tested_visible", "value": value, "score": score}
            ]
        }

    assert _score_direction([row(20, 9.0), row(40, 6.0), row(80, 2.0)], "x") == "lower_is_better"
    assert _score_direction([row(20, 9.0), row(40, 6.0)], "x") is None, "too few to say"
    assert _score_direction([row(1, 5.0), row(2, 9.0), row(3, 1.0), row(4, 7.0)], "x") == "mixed"


async def test_a_rendered_number_is_labelled_with_the_unit_it_was_parsed_in(ctx):
    """Laptop Weight on rt_product: `value: 2.1` parsed from "2.1 lbs (1.0 kg)" was labelled
    "kilograms" once the table path learned about input units. A rendered number is in
    the DISPLAY unit."""
    from dataclasses import replace

    from rtings_mcp.normalize import normalize_review_row

    schema = await ctx.repo.schema("tv")
    converted = replace(
        schema.test("11"),
        number_input_unit="kilograms",
        number_input_precision=3,
        number_display_unit="pounds",
        number_display_precision=1,
    )
    row = normalize_review_row(
        {"status": "tested", "unblurred": True, "rendered_value": "2.1 lbs (1.0 kg)", "score": 8.0},
        converted,
        product_id="1",
        schema=schema,
    ).to_json()
    # RTINGS shows the stored unit in parentheses: that figure is served, so this path
    # agrees with rt_ratings (kilograms) instead of labelling the test two ways.
    assert row["value"] == 1.0 and row["value_source"] == "rendered"
    assert row["unit"] == "kilograms" and row["display_unit"] == "pounds"
    assert row["precision"] == 3
    # Without a parenthesised figure the display unit is the only honest label.
    single = normalize_review_row(
        {"status": "tested", "unblurred": True, "rendered_value": "2.1 lbs", "score": 8.0},
        converted,
        product_id="1",
        schema=schema,
    ).to_json()
    assert single["value"] == 2.1 and single["unit"] == "pounds"
    assert "display_unit" not in single


async def test_a_filter_takes_a_two_sided_range(ctx):
    from rtings_mcp.services import _parse_clauses

    assert _parse_clauses("13..14") == [(">=", 13.0), ("<=", 14.0)]
    assert _parse_clauses("14 to 13") == [(">=", 13.0), ("<=", 14.0)]
    assert _parse_clauses(">=13 <=14") == [(">=", 13.0), ("<=", 14.0)]
    assert _parse_clauses(">=13,<=14") == [(">=", 13.0), ("<=", 14.0)]
    assert _parse_clauses(">1000") == [(">", 1000.0)]
    assert _parse_clauses("4k") == [("=", "4k")]
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="1200", score=5.0),
                make_test_row("2", "11", unblurred=True, value="5000", score=8.0),
                make_test_row("3", "11", unblurred=True, value="9000", score=9.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", tests=["11"], filters={"11": "2000..8000"})
    assert [p["product_id"] for g in out["data"]["groups"] for p in g["products"]] == ["2"]


async def test_find_matches_word_starts_and_prefers_the_phrase(ctx):
    from rtings_mcp.services import _find_score

    assert _find_score(["pet"], "pet", "low-pile carpet") == 0, "'pet' must not hit 'carpet'"
    assert _find_score(["pet", "hair"], "pet hair", "performance pet hair pickup") == 12
    assert _find_score(["pet", "hair"], "pet hair", "hair pet tool") == 2


async def test_a_colliding_numeric_id_must_say_which_space(ctx):
    from dataclasses import replace

    from rtings_mcp.services import _field_lookup

    schema = await ctx.repo.schema("tv")
    usage = next(iter(schema.usages.values()))
    schema.usages["11"] = replace(usage, original_id="11")
    with pytest.raises(RtingsError) as excinfo:
        _field_lookup(schema, "11")
    assert "test:11" in str(excinfo.value)
    assert _field_lookup(schema, "test:11") == ("test", "11")
    assert _field_lookup(schema, "usage:11") == ("usage", "11")
    assert _field_lookup(schema, "usage:Native Contrast") is None


async def test_featured_results_are_tied_to_the_schema_by_name(ctx):
    """A pick's featured stub has no original_id. A unique name is resolved; a repeated
    name (air-purifier's two "Measured PM1.0 CADR") lists the candidates instead."""
    from dataclasses import replace

    from rtings_mcp.services import _featured_results

    schema = await ctx.repo.schema("tv")
    rows = [
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": "5000 : 1",
            "score": 9.0,
            "test": {"name": "Native Contrast", "kind": "number", "insider_only": True},
        },
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": "1000 cd/m²",
            "score": 8.0,
            "test": {"name": "Peak Brightness", "kind": "number", "insider_only": True},
        },
    ]
    out = _featured_results(rows, schema)
    assert out[0]["original_id"] == "11" and out[0]["hierarchy"]
    schema.tests["12001"] = replace(
        schema.test("12000"), original_id="12001", parent_original_id="31615",
        derived_category_id=None,
    )
    out = _featured_results(rows, schema)
    assert "original_id" not in out[1]
    assert {c["original_id"] for c in out[1]["original_id_candidates"]} == {"12000", "12001"}
    ranked = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert ranked["data"]["picks"][0]["featured_results"][0]["original_id"] == "11"


async def test_find_says_no_prices_instead_of_try_a_synonym(ctx):
    out = await services.rt_schema(ctx, "tv", find="filter cost")
    assert "publishes no prices" in out["data"]["notice"]


async def test_find_takes_several_terms_in_one_call(ctx):
    out = await services.rt_schema(ctx, "tv", find="contrast, brightness")
    names = {t["name"]: t["matched_terms"] for t in out["data"]["tests"]}
    assert names["Native Contrast"] == ["contrast"]
    assert names["Peak Brightness"] == ["brightness"]


async def test_an_unscored_featured_spec_carries_no_score(ctx):
    from dataclasses import replace

    from rtings_mcp.services import _featured_results

    schema = await ctx.repo.schema("tv")
    schema.tests["208"] = replace(schema.test("208"), has_score=False)
    rows = [
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": "4k",
            "score": 0.0,
            "test": {"name": "Resolution", "kind": "word", "insider_only": False},
        }
    ]
    out = _featured_results(rows, schema)
    assert out[0]["display"] == "4k" and out[0]["score"] is None


async def test_a_clock_display_is_seconds_on_both_paths(ctx):
    """Toaster-oven "Time To Reach 350°F": the table serves 105 (seconds) and the review
    path parsed "01:45" as 1.0. Both now say 105 seconds, displayed as mm:ss."""
    from dataclasses import replace

    from rtings_mcp.normalize import normalize_review_row, normalize_table_row

    schema = await ctx.repo.schema("tv")
    clock = replace(
        schema.test("11"), number_input_unit="mm:ss", number_display_unit="mm:ss"
    )
    table = normalize_table_row(
        {**make_test_row("1", "11", unblurred=True, value="105"), "rendered_value": "01:45"},
        clock,
        schema=schema,
    ).to_json()
    assert table["value"] == 105.0 and table["unit"] == "seconds"
    assert table["display_unit"] == "mm:ss" and table["display"] == "01:45"
    review = normalize_review_row(
        {"status": "tested", "unblurred": True, "rendered_value": "01:45", "score": 9.0},
        clock,
        product_id="1",
        schema=schema,
    ).to_json()
    assert review["value"] == 105.0 and review["unit"] == "seconds"
    assert review["display_unit"] == "mm:ss"
    long = normalize_review_row(
        {"status": "tested", "unblurred": True, "rendered_value": "1:02:03", "score": 9.0},
        clock,
        product_id="1",
        schema=schema,
    ).to_json()
    assert long["value"] == 3723.0


async def test_find_searches_a_word_tests_values(ctx):
    """"countertop" is a VALUE of the microwave "Installation" test; an agent spent three
    schema calls finding it."""
    out = await services.rt_schema(ctx, "tv", find="4k")
    hit = next(t for t in out["data"]["tests"] if t["name"] == "Resolution")
    assert hit["match"] == "value" and "4k" in [v.lower() for v in hit["matched_values"]]
    miss = await services.rt_schema(ctx, "tv", find="no such thing")
    assert "This bench's usages:" in miss["data"]["notice"]


async def test_a_zero_row_response_proves_nothing(ctx):
    """`product_ids` matching nothing still reported `data_tier: unblurred` off rows the
    caller never saw."""
    ctx.transport.payloads["table_tool__test_results"] = unblurred_insider_payload("1")
    out = await services.rt_ratings(
        ctx, "tv", tests=["11"], usages=[], filters={"product_ids": ["424242"]}
    )
    assert out["data"]["returned"] == 0
    assert out["data_tier"] == "unproven"
    served = await services.rt_ratings(ctx, "tv", tests=["11"], usages=[])
    assert served["data_tier"] == "unblurred"


async def test_recommendation_lists_report_scores_available(ctx):
    out = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    assert out["scores_available"]["insider_tests"] == "gated"
    assert out["scores_available"]["usage_ratings"] == "gated"


async def test_rt_product_can_be_bounded_to_named_tests(ctx):
    out = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", tests=["Native Contrast", "208"]
    )
    assert out["error"] is None
    assert {r["original_id"] for r in product_rows(out)} <= {"11", "208"}
    assert out["data"]["result_count"] == len(product_rows(out))


async def test_find_names_the_terms_that_matched_nothing(ctx):
    out = await services.rt_schema(ctx, "tv", find="contrast, vent fan")
    assert out["data"]["terms_with_no_matches"] == ["vent fan"]
    assert [t["name"] for t in out["data"]["tests"]][:1] == ["Native Contrast"]


async def test_a_repeated_test_name_is_flagged_on_the_row(ctx):
    from dataclasses import replace

    schema = await ctx.repo.schema("tv")
    schema.tests["12001"] = replace(
        schema.test("12000"), original_id="12001", parent_original_id="31615",
        derived_category_id=None,
    )
    from rtings_mcp.services import _test_json

    assert _test_json(schema, schema.test("12000"))["name_repeats_on_bench"] is True
    assert _test_json(schema, schema.test("12001"))["name_repeats_on_bench"] is True
    assert "name_repeats_on_bench" not in _test_json(schema, schema.test("11"))


async def test_a_test_named_size_is_not_hijacked_by_the_variant_alias(ctx):
    """Monitor and laptop have a numeric "Size" test. `{"Size": ">31"}` was routed to the
    tested-variant alias and matched nothing, silently; by id it matched 46."""
    from dataclasses import replace

    schema = await ctx.repo.schema("tv")
    # Rename the fixture's numeric test to "Size" for this bench.
    schema.tests["11"] = replace(schema.test("11"), name="Size")
    ctx.transport.payloads["table_tool__test_results"] = {
        "data": {
            "test_results": [
                make_test_row("1", "11", unblurred=True, value="27", score=5.0),
                make_test_row("2", "11", unblurred=True, value="32", score=8.0),
            ]
        }
    }
    out = await services.rt_ratings(ctx, "tv", filters={"Size": ">31"}, usages=[])
    assert [p["product_id"] for g in out["data"]["groups"] for p in g["products"]] == ["2"]
    assert "11" in out["data"]["tests"], "the filter field was fetched"
    # Where no test is called Size, the word still means the tested variant.
    schema.tests["11"] = replace(schema.test("11"), name="Native Contrast")
    out = await services.rt_ratings(ctx, "tv", filters={"size": "55"}, usages=[])
    assert [p["product_id"] for g in out["data"]["groups"] for p in g["products"]] == ["2"]


async def test_a_test_name_containing_a_slash_resolves_whole(ctx):
    """Monitor "Rotate Portrait/Landscape" — copied from rt_schema's own output — was
    rejected as a filter key because "/" is also the Group/Name qualifier."""
    from dataclasses import replace

    from rtings_mcp.services import _field_lookup

    schema = await ctx.repo.schema("tv")
    schema.tests["11"] = replace(schema.test("11"), name="Rotate Portrait/Landscape")
    assert _field_lookup(schema, "Rotate Portrait/Landscape") == ("test", "11")
    assert _field_lookup(schema, "Picture Quality/Peak Brightness") == ("test", "12000")


async def test_a_later_field_is_not_blamed_on_the_bench_when_the_set_is_empty(ctx):
    """With zero rows left after one filter, the next field's census read "no row carries
    it" and pointed at the bench — a false diagnosis for a field the bench defines."""
    out = await services.rt_ratings(
        ctx,
        "tv",
        tests=["11"],
        usages=[],
        filters={"brand": "Nobody", "11": ">1"},
        sort="-11",
    )
    assert out["data"]["total_matched"] == 0
    assert any("earlier filter left 0 products" in w for w in out["warnings"]), out["warnings"]
    assert not any("not on the bench" in w for w in out["warnings"]), out["warnings"]
    assert out["sorted_by"].get("fallback_reason") is None


async def test_a_word_filter_that_matches_nothing_lists_the_values_seen(ctx):
    out = await services.rt_ratings(
        ctx, "tv", tests=["208"], usages=[], filters={"Resolution": "3840x2160"}
    )
    assert out["data"]["total_matched"] == 0
    hint = next(w for w in out["warnings"] if w.startswith("filter_matched_nothing"))
    assert "4k" in hint and "1080p" in hint


async def test_a_digit_string_against_a_word_test_is_a_substring(ctx):
    """`{"Native Resolution": "1440"}` parsed 1440 as a number and matched nothing; the
    docstring promised a substring match on word tests."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"], usages=[], filters={"208": "4"})
    assert [p["product_id"] for g in out["data"]["groups"] for p in g["products"]] == ["1"]
    out = await services.rt_ratings(
        ctx, "tv", tests=["208"], usages=[], filters={"Resolution": "!=4k"}
    )
    assert "1" not in [p["product_id"] for g in out["data"]["groups"] for p in g["products"]]


async def test_rt_schema_says_when_a_group_id_is_really_a_test(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_schema(ctx, "tv", group="208")
    assert excinfo.value.code == "unknown_test"
    assert "is the test 'Resolution', not a group" in str(excinfo.value)
    assert "group=900" in str(excinfo.value)


async def test_a_best_of_slug_outside_the_index_is_fetched_not_refused(ctx):
    """A review's prose links to a list the landing page's index omits; the slug was
    refused as `unknown_product` before any fetch."""
    out = await services.rt_recommendations(ctx, "tv", list="off-index")
    assert out["error"] is None and out["data"]["picks"]
    assert any(w.startswith("list_not_in_index") for w in out["warnings"])


async def test_rt_product_lists_the_size_lineup(ctx):
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_results=False)
    assert out["data"]["product"]["variants"] or out["data"]["product"]["variants"] is None
    assert "recommended_sku" not in json.dumps(
        await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    )


async def test_a_multi_word_find_term_is_matched_only_when_all_its_words_hit(ctx):
    out = await services.rt_schema(ctx, "tv", find="wind contrast, brightness")
    contrast = next(t for t in out["data"]["tests"] if t["name"] == "Native Contrast")
    assert contrast.get("matched_terms") == []
    assert contrast["partially_matched_terms"] == ["wind contrast"]
    assert out["data"]["terms_with_no_matches"] == []


async def test_a_pick_says_which_size_and_bench_rtings_tested(ctx):
    out = await services.rt_recommendations(ctx, "tv", list="tvs-on-the-market")
    pick = out["data"]["picks"][0]
    assert pick["product_id"] == "1"
    assert pick["tested_variant"] == '65"'
    assert pick["test_bench"]["id"] == "227"


def test_find_words_match_whole_words_or_their_plural_and_ing_forms():
    from rtings_mcp.services import _find_score

    assert _find_score(["weight"], "weight", "design weighted thd") == 0
    assert _find_score(["weight"], "weight", "design weight") == 11
    assert _find_score(["print"], "print", "printing speed black only") >= 1
    assert _find_score(["window"], "window", "peak 2% windows") >= 1


async def test_dual_y_axes_declared_under_vaxes_are_read(ctx):
    """tv's PQ EOTF curve declares its two y axes under `vAxes` ("0" stimulus, "1"
    luminance) with `vAxis` left as a placeholder, and spells the scale `scaleType`. Read
    from `vAxis`/`scale` alone the curve had no y axis (member round S8, 2026-09-07)."""

    async def dual(path):
        return {
            "header": ["Input Stimulus", "Target", "Measured"],
            "data": [[0, 0, 0], [1, 1, 0.86]],
            "options": {
                "hAxis": {"scaleType": "linear", "title": "Signal Input Stimulus"},
                "vAxis": {"ignore": "me"},
                "vAxes": {
                    "0": {"scaleType": "linear", "title": "Measured Output Stimulus"},
                    "1": {"scaleType": "linear", "title": "Measured Output Luminance"},
                },
                "series": {"0": {"targetAxisIndex": 1}, "1": {"targetAxisIndex": 0}},
            },
        }

    ctx.transport.cdn_get_json = dual
    out = await services.rt_graph(ctx, "/tv/reviews/alpha/alpha-one", "13907")
    assert out["error"] is None, out["error"]
    axes = out["data"]["axes"]
    assert axes["x"] == {"title": "Signal Input Stimulus", "scale": "linear"}
    assert axes["y"] == {"title": "Measured Output Stimulus", "scale": "linear"}
    assert [a["title"] for a in axes["y_axes"]] == [
        "Measured Output Stimulus",
        "Measured Output Luminance",
    ]
    assert axes["series_y_axis_index"] == [1, 0]


async def test_a_wrong_qualifier_lists_the_tests_with_that_leaf_name(ctx):
    """`Treble/RMS Deviation From Target` guesses the group; the real qualifier is the full
    group name. "No test named" sent the caller back to the schema (member round S3)."""
    from dataclasses import replace

    from rtings_mcp.services import _field_lookup

    schema = await ctx.repo.schema("tv")
    twin = replace(schema.test("12000"), original_id="12001", parent_original_id="31615")
    schema.tests["12001"] = twin
    with pytest.raises(RtingsError) as excinfo:
        _field_lookup(schema, "Nowhere/Peak Brightness")
    assert excinfo.value.code == "unknown_test"
    assert len(excinfo.value.details["matches"]) == 2
    assert any("(12001)" in m for m in excinfo.value.details["matches"])
    assert _field_lookup(schema, "Nowhere/No Such Leaf") is None


async def test_a_group_named_in_tests_lists_its_leaves(ctx):
    """mattress `tests=["Firmness"]` resolved to the group and stopped at "structure, not a
    result"; the leaf "Firmness Level" and the usage of the same name were the answer
    (member round S12, 2026-09-07)."""
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_ratings(ctx, "tv", tests=["900"])
    assert excinfo.value.code == "unknown_test"
    assert "Native Contrast" in excinfo.value.message
    leaves = {t["original_id"] for t in excinfo.value.details["tests"]}
    assert {"11", "12000", "208"} <= leaves


def test_reasoning_nolink_shortcodes_are_unwrapped():
    """Best-of prose ships "[nolink:Sonos Beam (Gen 2)]" for a product RTINGS chose not to
    link; the name is the text (member round S9, 2026-09-07)."""
    from rtings_mcp.services import _strip_wrappers

    assert (
        _strip_wrappers("<div>The [nolink:Sonos Beam (Gen 2)] beats the [nolink:Arc Ultra].</div>")
        == "The Sonos Beam (Gen 2) beats the Arc Ultra."
    )


async def test_find_marks_an_unscored_usage(ctx):
    """mattress "Firmness" is an `is_unscored` usage: sorting on it can only fall back, and
    `find` listed it like any other (member round S12, 2026-09-07)."""
    schema = await ctx.repo.schema("tv")
    from dataclasses import replace

    schema.usages["1"] = replace(schema.usages["1"], is_unscored=True)
    out = await services.rt_schema(ctx, "tv", find="mixed usage")
    hit = next(u for u in out["data"]["usages"] if u["original_id"] == "1")
    assert hit["is_unscored"] is True


async def test_a_partial_only_hit_does_not_mark_every_term_matched(ctx):
    """`_unmatched_terms` fell back to `[term]` on an empty `matched_terms`, so one hit
    that matched only part of one term reported every term as matched."""
    out = await services.rt_schema(ctx, "tv", find="wind contrast, vent fan")
    contrast = next(t for t in out["data"]["tests"] if t["name"] == "Native Contrast")
    assert contrast["matched_terms"] == []
    assert out["data"]["terms_with_no_matches"] == ["vent fan"]


def test_a_lone_digit_scores_only_beside_a_word_of_the_same_term():
    """`find="hdmi 2.1"` split into hdmi/2/1 and the bare digits matched "Peak 2%
    Window", "USB Ports" (values 1, 2) and sixty more rows on their own, filling the
    60-row cap with noise while the real matches sat past it (shopper round 3)."""
    from rtings_mcp.services import _find_score, _find_words

    words = _find_words("hdmi 2.1")
    assert words == ["hdmi", "2", "1"]
    assert _find_score(words, "hdmi 2.1", "peak 2% window") == 0
    assert _find_score(words, "hdmi 2.1", "usb ports") == 0
    assert _find_score(words, "hdmi 2.1", "hdmi 2.1 bandwidth") == 13
    # hdmi alone still counts; so does the digit once its word is there.
    assert _find_score(words, "hdmi 2.1", "hdmi input lag") == 1
    # A term that is only digits keeps them — it is all the caller gave us.
    assert _find_score(["1440"], "1440", "native resolution 1440") == 11
    assert _find_score(["2"], "2", "peak 2% window") == 11


async def test_an_off_bench_usage_is_a_warning_not_a_failed_call(ctx):
    """`usages=["1", "77"]` on bench 227 failed the whole call over the one usage that
    had moved bench, while an off-bench `product_ids` entry gets a warning and a partial
    answer (shopper round 3, 2026-09-07)."""
    out = await services.rt_ratings(ctx, "tv", bench=["227"], usages=["1", "77"], limit=3)
    assert out["error"] is None
    warning = next(w for w in out["warnings"] if w.startswith("usages:"))
    assert "77" in warning and "Legacy Usage" in warning and "bench=['2']" in warning
    scored = [u["original_id"] for g in out["data"]["groups"] for u in g.get("usages", [])]
    assert "77" not in scored


async def test_an_off_bench_test_is_a_warning_not_a_failed_call(ctx):
    out = await services.rt_ratings(ctx, "tv", bench=["227"], tests=["11", "5"], limit=3)
    assert out["error"] is None
    warning = next(w for w in out["warnings"] if w.startswith("tests:"))
    assert "5" in warning and "Bright Room" in warning


async def test_a_request_of_only_off_bench_usages_still_errors(ctx):
    """Dropping every usage would leave `usages=[]`, which reads as "no scores exist";
    the error's `available` list is the actionable answer instead."""
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_ratings(ctx, "tv", bench=["227"], usages=["77"])
    assert excinfo.value.code == "unknown_test"
    assert excinfo.value.details["on_other_benches"] == {"77": ["2"]}
    assert "1" in excinfo.value.details["available"]

    with pytest.raises(RtingsError) as excinfo:
        await services.rt_ratings(ctx, "tv", bench=["227"], tests=["5"])
    assert excinfo.value.details["on_other_benches"] == {"5": ["2"]}


async def test_sold_in_filters_on_the_sizes_a_product_is_offered_in(ctx):
    """`variant` matches the TESTED sku only, so "which of these is SOLD at 75 inches"
    had no filter and one session scanned `variants` by eye (shopper round 3)."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"sold_in": ">=75"})
    ids = {r["product_id"] for g in out["data"]["groups"] for r in g["products"]}
    assert ids == {"1", "2", "3"}, "all are sold at 75 inches; only one was tested there"

    # Textual variations work the same loose way `variant` does.
    out = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"sold_in": "55-inch"})
    ids = {r["product_id"] for g in out["data"]["groups"] for r in g["products"]}
    assert ids == {"2"}


async def test_sold_in_says_so_when_nobody_sells_that_size(ctx):
    """A bare 0 must not read as "RTINGS tested nothing that size"."""
    out = await services.rt_ratings(ctx, "tv", tests=["208"], filters={"sold_in": ">=83"})
    assert not [r for g in out["data"]["groups"] for r in g["products"]]
    warning = next(w for w in out["warnings"] if w.startswith("filter_unavailable"))
    assert "no product in this call is sold in" in warning and '75"' in warning


async def test_each_variant_carries_the_manufacturer_model_number(ctx):
    """`variant_skus[].name` is the model number a retailer is searched by; it was
    dropped, and one session went to a web search for it (shopper round 3)."""
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", include_results=False)
    assert {"variation": '75"', "model": "XR-75A1"} in out["data"]["product"]["variants"]


async def test_find_says_when_a_term_is_a_catalog_field_not_a_test(ctx):
    """`find="size, brand, year, price"` matched nothing and said nothing; those are
    catalog facts on the product row, and the answer was one tool over the whole time
    (shopper round 3, 2026-09-07)."""
    out = await services.rt_schema(ctx, "tv", find="brand, year, price, contrast")
    hints = out["data"]["catalog_fields"]
    assert set(hints) == {"brand", "year", "price"}
    assert "name_contains" in hints["brand"] or "brand" in hints["brand"]
    assert "released_at" in hints["year"]
    assert "no price" in hints["price"]
    assert out["data"]["terms_with_no_matches"] == ["brand", "year", "price"]


async def test_a_single_term_that_matches_nothing_still_gets_its_hint(ctx):
    """`terms_with_no_matches` is a multi-term field; a one-word search that missed got
    an empty response and no explanation at all."""
    out = await services.rt_schema(ctx, "tv", find="size")
    assert not out["data"]["tests"] and not out["data"]["usages"]
    assert "sold_in" in out["data"]["catalog_fields"]["size"]


async def test_a_silo_with_a_real_size_test_gets_the_test_not_the_hint(ctx):
    """laptop and monitor DO have a numeric "Size" test; the hint must never shadow it."""
    from rtings_mcp.services import _catalog_field_hints

    assert _catalog_field_hints([]) == {}
    assert _catalog_field_hints(["contrast"]) == {}


async def test_the_brand_best_of_pages_are_discovered_and_fetchable(ctx):
    """RTINGS' Best nav lists "The 4 Best Sony TVs" at /tv/reviews/sony beside the
    /best/ lists, and the `brands` list's own prose links to it. Discovery kept only
    /best/ slugs, so `list="sony"` guessed /tv/reviews/best/sony and reported
    `unknown_list` for a page that exists (shopper round 3, 2026-09-07)."""
    index = await services.rt_recommendations(ctx, "tv")
    entry = next(e for e in index["data"]["lists"] if e["list"] == "alpha")
    assert entry["kind"] == "brand" and entry["url"] == "/tv/reviews/alpha"
    assert {e["kind"] for e in index["data"]["lists"]} == {"best", "brand"}

    out = await services.rt_recommendations(ctx, "tv", list="alpha")
    assert out["error"] is None and out["data"]["picks"]
    assert "GET /tv/reviews/alpha" in ctx.transport.calls


def test_a_slug_with_a_slash_never_reaches_the_brand_shape():
    """A product review page is /{silo}/reviews/{brand}/{model} and fetching one as HTML
    spends a preview (RECON.md §14.2). Only a single-segment slug may try that shape."""
    from rtings_mcp.repository import recommendation_paths

    assert recommendation_paths("tv", "sony") == [
        "/tv/reviews/best/sony",
        "/tv/reviews/sony",
    ]
    assert recommendation_paths("tv", "sony/a80l") == ["/tv/reviews/best/sony/a80l"]
    assert recommendation_paths("tv", "by-size/65-inch") == [
        "/tv/reviews/best/by-size/65-inch"
    ]
    assert recommendation_paths("tv", "best") == ["/tv/reviews/best/best"]


async def test_rt_search_can_be_scoped_to_one_silo(ctx):
    """"Sony A80J" returned 822 hits with cameras, headphones and soundbars mixed into
    page one of a TV question (shopper round 3, 2026-09-07)."""
    ctx.transport.payloads["app/search__search_results"] = {
        "data": {
            "search_results": {
                "query": "alpha",
                "total_count": 822,
                "results": [
                    {"kind": "page", "title": "Alpha Cam", "url": "/camera/reviews/a/cam"},
                    {"kind": "page", "title": "Alpha One TV", "url": "/tv/reviews/alpha/one"},
                    {"kind": "page", "title": "Alpha Buds", "url": "/headphones/reviews/a/b"},
                ],
            }
        }
    }
    out = await services.rt_search(ctx, "alpha", silo="tv")
    assert [h["title"] for h in out["data"]["results"]] == ["Alpha One TV"]
    assert out["data"]["silo"] == "tv"
    assert out["data"]["silo_matches"] == 1
    assert out["data"]["total_count"] == 822, "RTINGS' own count, not the filtered one"
    assert "not by RTINGS" in out["data"]["notice"]

    unfiltered = await services.rt_search(ctx, "alpha")
    assert len(unfiltered["data"]["results"]) == 3
    assert unfiltered["data"]["silo_matches"] is None


async def test_an_unknown_silo_on_rt_search_is_refused_against_the_live_list(ctx):
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_search(ctx, "alpha", silo="not-a-silo")
    assert excinfo.value.code == "unknown_silo"


async def test_rt_article_returns_a_learn_page_as_prose(ctx):
    """"Does Alpha sell a bigger OLED this year" is answered by /tv/learn/…-lineup, and
    the server had no surface for it at all (shopper round 3, 2026-09-07)."""
    out = await services.rt_article(ctx, "/tv/learn/alpha-lineup")
    data = out["data"]
    assert out["error"] is None
    assert data["title"] == "2026 Alpha Lineup"
    assert data["sections"] == ["Market Trends", "Brand Lineups", "Alpha", "Beta"]
    assert data["introduction"] == "The intro."
    assert "83 inch OLED" in data["body"]
    assert data["authors"] == ["A Writer"]
    assert data["updated_at"] == "2026-06-18"


async def test_rt_article_can_return_one_section(ctx):
    """A lineup article runs past 25,000 characters; one heading is the readable unit."""
    out = await services.rt_article(ctx, "alpha-lineup", silo="tv", section="alpha")
    assert out["data"]["section"] == "Alpha"
    assert out["data"]["body"] == "Alpha ships an 83 inch OLED this year."
    assert out["data"]["introduction"] is None

    with pytest.raises(RtingsError) as excinfo:
        await services.rt_article(ctx, "/tv/learn/alpha-lineup", section="gamma")
    assert excinfo.value.details["sections"] == [
        "Market Trends", "Brand Lineups", "Alpha", "Beta"
    ]


async def test_rt_article_can_return_the_outline_alone(ctx):
    out = await services.rt_article(ctx, "/tv/learn/alpha-lineup", include_body=False)
    assert out["data"]["body"] is None and out["data"]["sections"]


async def test_rt_article_reads_a_tests_page(ctx):
    """rt_search's top hit for "OLED burn-in longevity" is /tv/tests/…, rt_article refused
    it, and there is no burn-in test on the bench to fall back on (filed 2026-09-09).
    The meter does not move for a /tests/ GET (RECON.md §14.8)."""
    out = await services.rt_article(ctx, "/tv/tests/alpha-longevity")
    data = out["data"]
    assert out["error"] is None
    assert data["branch"] == "tests"
    assert data["title"] == "Longevity Burn-In Test: Updates And Results"
    assert "GET /tv/tests/alpha-longevity" in ctx.transport.calls


async def test_a_tests_page_whose_whole_article_is_the_introduction_still_has_a_body(ctx):
    """/tv/tests/longevity-burn-in-test-updates-and-results ships 54,291 characters of
    `introduction` and 0 of `text` (measured 2026-09-09). Reading only `text` returned an
    empty body and no sections for the one page an OLED buyer actually wants."""
    out = await services.rt_article(ctx, "/tv/tests/alpha-longevity")
    data = out["data"]
    assert data["sections"] == [
        "March 16, 2026 - Final Update",
        "August 28, 2025 - Alpha X90J",
    ]
    assert "Four panels failed outright." in data["body"]
    # The introduction IS the body here, so it is not also served as a separate field.
    assert data["introduction"] is None

    one = await services.rt_article(ctx, "/tv/tests/alpha-longevity", section="March 16")
    assert one["data"]["section"] == "March 16, 2026 - Final Update"
    assert one["data"]["body"] == "Four panels failed outright."


async def test_rt_article_reads_a_nested_tests_slug(ctx):
    """The methodology pages `rt_schema` describes numerically are two segments deep."""
    out = await services.rt_article(ctx, "/tv/tests/picture-quality/alpha-contrast")
    assert out["data"]["title"] == "Contrast Ratio"
    assert out["data"]["sections"] == ["Our Test"]
    # `text` is present here, so the introduction is a real preface and is served as one.
    assert out["data"]["introduction"] == "What contrast is."
    assert "We measure a checkerboard." in out["data"]["body"]


async def test_the_two_branches_do_not_share_a_cache_entry(ctx):
    """/tv/learn/alpha-lineup and /tv/tests/alpha-lineup are different pages; an
    unprefixed cache key would serve one as the other."""
    learn = await services.rt_article(ctx, "/tv/learn/alpha-lineup")
    tests = await services.rt_article(ctx, "/tv/tests/alpha-lineup")
    assert learn["data"]["title"] == "2026 Alpha Lineup"
    assert tests["data"]["title"] == "How We Test Alpha Lineups"
    files = sorted(p.name for p in (ctx.config.cache_dir / "articles" / "tv").iterdir())
    assert files == ["learn__alpha-lineup.json", "tests__alpha-lineup.json"], files


async def test_a_bare_slug_still_means_learn(ctx):
    out = await services.rt_article(ctx, "alpha-lineup", silo="tv")
    assert out["data"]["branch"] == "learn"
    assert out["data"]["title"] == "2026 Alpha Lineup"


async def test_rt_article_refuses_any_path_that_is_not_a_prose_page(ctx):
    """A product review page is /{silo}/reviews/{brand}/{model} and fetching one as HTML
    spends a preview (RECON.md §14.2). No accepted input may name one."""
    for bad in (
        "/tv/reviews/alpha/alpha-one",
        "/tv/reviews/best/tvs-on-the-market",
        "tv/reviews/sony/a80l",
        "/tv/learn",
        "/tv/tests",
        "/tv/discussions/abc",
    ):
        with pytest.raises(RtingsError) as excinfo:
            await services.rt_article(ctx, bad)
        assert excinfo.value.code == "unknown_list", bad
    assert not [c for c in ctx.transport.calls if "/reviews/" in c]


async def test_no_accepted_input_can_build_a_review_path(ctx):
    """The guard is STRUCTURAL, not intentional: the branch is a fixed `learn|tests`
    alternation and `article_path` re-validates it, so no `article`, `silo` or slug a
    caller can pass may reach `/{silo}/reviews/{brand}/{model}` — whose HTML GET spends a
    preview (RECON.md §14.2)."""
    hostile = [
        ("/tv/reviews/sony/a95l-oled", None),
        ("/tv/learn/../reviews/sony/a95l", None),
        ("/tv/learn/%2e%2e/reviews/sony/a95l", None),
        ("/TV/REVIEWS/SONY/A95L", None),
        ("/tv//reviews/sony/a95l", None),
        ("a95l-oled", "tv/reviews/sony"),
        ("a95l-oled", "../tv"),
        ("sony/a95l-oled", "tv"),
        ("/tv/tests/../../reviews/sony/a95l", None),
        ("/tv/learn/sony/a95l-oled/../../../reviews/x/y", None),
    ]
    for article, silo in hostile:
        with pytest.raises(RtingsError):
            await services.rt_article(ctx, article, silo=silo)
    assert not [c for c in ctx.transport.calls if "/reviews/" in c], ctx.transport.calls

    # And the path builder itself refuses a branch it was not given by the regex.
    for branch in ("reviews", "REVIEWS", "", "../reviews", "learn/../reviews"):
        with pytest.raises(RtingsError):
            repository.article_path("tv", branch, "a95l")
    assert repository.article_path("tv", "tests", "picture-quality/contrast-ratio") == (
        "/tv/tests/picture-quality/contrast-ratio"
    )


async def test_the_refusal_names_what_was_actually_passed(ctx):
    """It said "a review page is a different shape" to a caller who passed a /tests/ page,
    sending them after a review-page problem that did not exist (filed 2026-09-09)."""
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_article(ctx, "/tv/reviews/alpha/alpha-one")
    assert "product review page" in excinfo.value.message
    assert "rt_product" in excinfo.value.message

    with pytest.raises(RtingsError) as excinfo:
        await services.rt_article(ctx, "/tv/discussions/abc")
    assert "review" not in excinfo.value.message
    assert "/tests/" in excinfo.value.message


async def test_an_article_over_budget_is_cut_even_when_the_budget_is_already_negative(ctx):
    """`len(prose) > budget > 0` served an oversized response WHOLE once the rest of the
    envelope had already spent the budget — the one thing the wire bound exists to stop."""
    ctx.config.max_response_chars = 4_000  # the configured floor, not a contrived value

    # `sections` names every heading, so a 400-heading outline spends the budget before
    # the prose is measured at all. The prose then gets nothing — it is not served whole.
    out = await services.rt_article(ctx, "/tv/tests/many-sections")
    assert len(out["data"]["sections"]) == 400
    assert out["data"]["body"] == "", "served whole against a spent budget"
    assert any("response_truncated" in w for w in out["warnings"])

    # And where the budget leaves room, the cut lands inside the prose, not past it.
    out = await services.rt_article(ctx, "/tv/tests/long-intro")
    body = out["data"]["body"]
    assert 0 < len(body) < 400 * len("A body sentence. ")
    assert any("response_truncated" in w for w in out["warnings"])


async def test_a_giant_introduction_is_bounded_too(ctx):
    """A /tests/ introduction can be the whole article; an unbounded one crowds out the
    body it is supposed to preface."""
    ctx.config.max_response_chars = 4_000
    out = await services.rt_article(ctx, "/tv/tests/picture-quality/alpha-contrast")
    assert out["data"]["introduction"] == "What contrast is.", "short intro is untouched"

    out = await services.rt_article(ctx, "/tv/tests/long-intro")
    assert len(out["data"]["introduction"]) <= 1_000
    assert any("introduction_truncated" in w for w in out["warnings"])
    assert "A body sentence." in out["data"]["body"], "the body still got its budget"


async def test_search_hits_say_which_tool_takes_their_url(ctx):
    """Every hit is `kind: "page"` whatever it points at, so a caller discovered by error
    that rt_article refuses most of them (filed 2026-09-09)."""
    ctx.transport.payloads["app/search__search_results"] = {
        "data": {
            "search_results": {
                "query": "alpha",
                "total_count": 9,
                "results": [
                    {"kind": "page", "title": "L", "url": "/tv/tests/longevity-test"},
                    {"kind": "page", "title": "A", "url": "/tv/learn/2026-lineup"},
                    {"kind": "page", "title": "P", "url": "/tv/reviews/sony/a95l-oled"},
                    {"kind": "page", "title": "S", "url": "/tv/reviews/sony/a95l/settings"},
                    {"kind": "page", "title": "B", "url": "/tv/reviews/best/mini-led"},
                    {"kind": "page", "title": "N", "url": "/monitor/reviews/best/by/ultra"},
                    {"kind": "page", "title": "R", "url": "/tv/reviews/sony"},
                    {"kind": "page", "title": "E", "url": "/early-access/tv/reviews/lg/b6"},
                    {"kind": "discussion", "title": "D", "url": "/discussions/abc/x"},
                    {"kind": "page", "title": "Z", "url": "/brands/sony"},
                ],
            }
        }
    }
    out = await services.rt_search(ctx, "alpha", count=10)
    got = {h["title"]: h["read_with"] for h in out["data"]["results"]}
    assert got == {
        "L": "rt_article",
        "A": "rt_article",
        "P": "rt_product",
        "S": None,
        "B": "rt_recommendations",
        "N": "rt_recommendations",
        "R": "rt_recommendations",
        "E": "rt_product",
        "D": None,
        "Z": None,
    }


async def test_a_learn_slug_that_does_not_exist_is_not_a_plausible_empty_article(ctx):
    """RTINGS answers an unknown learn slug with another page, so "no article object" is
    the only honest signal that it is not there."""
    with pytest.raises(RtingsError) as excinfo:
        await services.rt_article(ctx, "/tv/learn/made-up")
    assert excinfo.value.code == "recommendations_missing"


async def test_rt_product_groups_its_results_under_the_breadcrumb_once(ctx):
    """The rows came back flat with `hierarchy` repeated on each one — on a TV, 243 rows
    x a 2-3 element breadcrumb, ~23% of a 56 KB response saying the same thing over."""
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    groups = out["data"]["results"]
    assert groups, "the review has results"
    assert all("hierarchy" not in row for row in product_rows(out)), (
        "the breadcrumb belongs to the group now, not to every row"
    )
    picture = next(g for g in groups if g["group"] == ["Picture", "Picture Quality"])
    assert picture["group_id"] == "900"
    assert picture["test_count"] == len(picture["tests"])
    assert out["data"]["result_count"] == len(product_rows(out)), (
        "result_count counts results, not groups"
    )
    # `group_id` is addressable: it is what re-fetches this section alone.
    scoped = await services.rt_product(
        ctx, "/tv/reviews/alpha/alpha-one", group=picture["group_id"]
    )
    assert {r["original_id"] for r in product_rows(scoped)} == {
        r["original_id"] for r in picture["tests"]
    }


async def test_rt_product_bounds_its_response(ctx, monkeypatch):
    """Measured 2026-09-08: a real TV review is 243 results and 77,407 characters in the
    indent=2 form the client counts, against a 40,000 budget — and this tool bounded
    nothing at all, while rt_ratings always has."""
    from rtings_mcp.services import _wire_size

    full = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    budget = _wire_size(full["data"]) - 200
    monkeypatch.setattr(ctx.config, "max_response_chars", budget)
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", refresh=False)
    data = out["data"]
    assert _wire_size(data) <= budget, "the whole point is that it fits"
    assert data["result_count"] == 4, "counts the WHOLE review, not what survived"
    assert len(product_rows(out)) < 4, "and something was actually cut"
    warning = next(w for w in out["warnings"] if w.startswith("response_truncated"))
    assert "group=<its group_id>" in warning

    # A floor it cannot get under says so rather than pretending to have fitted.
    monkeypatch.setattr(ctx.config, "max_response_chars", 500)
    tiny = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    assert len(product_rows(tiny)) == 1
    assert "Even one section exceeds the budget" in "".join(tiny["warnings"])


def test_the_budget_drops_whole_sections_from_the_tail_and_names_them():
    """A section dropped silently reads as a section RTINGS did not test, so the index of
    what was cut is part of the response — and therefore part of what has to fit."""
    from rtings_mcp.services import _fit_product_budget, _wire_size

    data = {
        "results": [
            {
                "group": ["Category", f"Section {n}"],
                "group_id": str(n),
                "test_count": 6,
                "tests": [
                    {"original_id": f"{n}{i}", "name": "A test with a name", "value": i}
                    for i in range(6)
                ],
            }
            for n in range(8)
        ]
    }
    warnings = _fit_product_budget(data, 6_000)
    assert _wire_size(data) <= 6_000
    assert data["groups_total"] == 8
    assert data["groups_shown"] == len(data["results"]) < 8
    # dropped from the TAIL, so the head of the review survives in its own order
    assert [g["group_id"] for g in data["results"]] == [
        str(n) for n in range(data["groups_shown"])
    ]
    assert [g["group_id"] for g in data["groups_omitted"]] == [
        str(n) for n in range(data["groups_shown"], 8)
    ]
    assert "8 section(s)" in warnings[0] or "of 8 section(s)" in warnings[0]


def test_a_review_inside_the_budget_is_left_completely_alone():
    from rtings_mcp.services import _fit_product_budget

    data = {"results": [{"group": ["A"], "group_id": "1", "test_count": 1, "tests": [{}]}]}
    assert _fit_product_budget(data, 40_000) == []
    assert "groups_omitted" not in data and "groups_shown" not in data


async def test_a_result_the_schema_does_not_define_still_lands_in_its_own_section(ctx):
    """MEASURED 2026-09-08: every TV review on legacy bench v1.11 carries two results the
    silo schema has no definition for — `12240` "1080p @ 144Hz" and `12242` "4k @ 144Hz",
    both really measured (5 of 5 cached v1.11 reviews). They nested under `group: null`,
    which sorts last, is dropped FIRST by the budget and cannot be named by `group=`."""
    page = ctx.transport.payloads["app/product_vue_page__page_body"]
    rows = page["data"]["page"]["product"]["review"]["test_results"]
    rows.append(
        {
            "status": "tested",
            "unblurred": True,
            "rendered_value": "Yes",
            "test": {
                "original_id": "99999",
                "name": "4k @ 144Hz",
                "kind": "word",
                # RTINGS' internal `id`, which is NOT the original_id the schema is keyed
                # by — the name is the only join back to the section.
                "parent": {"name": "Picture Quality", "kind": "group", "id": "18631"},
            },
        }
    )
    out = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one")
    section = next(g for g in out["data"]["results"] if g["group_id"] == "900")
    assert "99999" in {r["original_id"] for r in section["tests"]}, (
        "it belongs beside its siblings, not in an unnamed bucket"
    )
    assert not [g for g in out["data"]["results"] if g["group_id"] is None]
    # and it is addressable, which is the whole point
    scoped = await services.rt_product(ctx, "/tv/reviews/alpha/alpha-one", group="900")
    assert "99999" in {r["original_id"] for r in product_rows(scoped)}


def test_a_parent_name_two_sections_share_is_not_guessed_at(tv_schema):
    """monitor has two groups called "Inputs" and projector two called "Design". A null
    beats a guess — the same rule that makes a repeated test name an error."""
    from rtings_mcp.schema import parse_column_options
    from rtings_mcp.services import _stub_parent_id

    stub = {"parent": {"name": "Picture Quality", "kind": "group", "id": "18631"}}
    assert _stub_parent_id(tv_schema, stub) == "900"
    assert _stub_parent_id(tv_schema, {"parent": {"name": "Nowhere"}}) is None
    assert _stub_parent_id(tv_schema, {}) is None

    raw = json.loads((FIXTURES / "column_options_min.json").read_text())
    raw["test_bench"]["tests"].append(
        {
            "original_id": "901",
            "name": "Picture Quality",
            "kind": "group",
            "published": True,
            "has_score": False,
            "parent_original_id": None,
            "order": 99,
            "insider_only": True,
            "number_display_unit": None,
            "number_display_precision": 1,
            "words": [],
        }
    )
    ambiguous = parse_column_options("tv", raw)
    assert _stub_parent_id(ambiguous, stub) is None, "two sections share the name"


def test_an_omitted_section_that_group_cannot_address_names_its_test_ids():
    """The truncation warning tells the caller to fetch a dropped section with
    `group=<its group_id>`. On a section whose group_id is null that instruction does not
    work, and the rows would simply be gone — so `tests=[...]` has to be able to reach
    them."""
    from rtings_mcp.services import _fit_product_budget

    data = {
        "results": [
            {
                "group": ["Category", f"Section {n}"],
                "group_id": str(n) if n else None,
                "test_count": 6,
                "tests": [
                    {"original_id": f"{n}{i}", "name": "A test with a name", "value": i}
                    for i in range(6)
                ],
            }
            for n in range(8)
        ]
    }
    # The unaddressable section is at the HEAD here, so force it out of the response.
    data["results"].append(data["results"].pop(0))
    warnings = _fit_product_budget(data, 6_000)
    omitted = {g["group_id"]: g for g in data["groups_omitted"]}
    assert None in omitted, "the unaddressable section was dropped"
    assert omitted[None]["test_ids"] == [f"0{i}" for i in range(6)]
    assert all("test_ids" not in g for g in data["groups_omitted"] if g["group_id"])
    assert "test_ids" in warnings[0] and "cannot be addressed by group=" in warnings[0]


async def test_a_bare_call_projects_the_public_tests_when_every_score_is_withheld(ctx):
    """MEASURED 2026-09-08: a bare rt_ratings("tv") without a membership was the catalog
    plus 33 usage scores of which ZERO were visible — nothing numeric at all, on an
    agent's first call."""
    ctx.transport.payloads["table_tool__ratings"] = {
        "data": {"ratings": [rating_row("1", "1"), rating_row("2", "1")]}
    }
    out = await services.rt_ratings(ctx, "tv")
    assert out["scores_available"]["usage_ratings"] == "gated"
    assert "208" in out["data"]["tests"], "Resolution is public and now answers something"
    row = values(out, "1")
    assert [t["value"] for t in row["tests"] if t["original_id"] == "208"] == ["4k"]
    assert any("public test(s) were projected" in w for w in out["warnings"])


async def test_visible_scores_leave_the_bare_call_exactly_as_it_was(ctx):
    """On a member session — or a metered silo with its budget unspent — the scores ARE
    the answer, and the rule must not fire. It reads the rows, never the cookie."""
    out = await services.rt_ratings(ctx, "tv")
    assert out["scores_available"]["usage_ratings"] in {"partial", "available"}
    assert not (out["data"]["tests"] or {})
    assert not any("public test(s) were projected" in w for w in out["warnings"])


async def test_an_explicit_tests_argument_is_never_second_guessed(ctx):
    """`tests=[]` means "no measurements"; the projection must not override the caller."""
    ctx.transport.payloads["table_tool__ratings"] = {
        "data": {"ratings": [rating_row("1", "1"), rating_row("2", "1")]}
    }
    out = await services.rt_ratings(ctx, "tv", tests=[])
    assert not (out["data"]["tests"] or {})
    assert not any("public test(s) were projected" in w for w in out["warnings"])


def test_a_repeated_usage_name_keeps_a_null_id_and_lists_the_candidates():
    """A wrong join key is worse than none: the featured tooltip's `target_id` is in a
    different namespace, so the name is the only join, and a repeated name is ambiguous."""
    from dataclasses import dataclass
    from typing import ClassVar

    from rtings_mcp.services import _featured_ratings

    @dataclass
    class Usage:
        original_id: str
        name: str
        parent_usage_name: str | None = None

    class Schema:
        usages: ClassVar[dict] = {
            "1": Usage("1", "Cooling", "Sleep"),
            "2": Usage("2", "Cooling", "Comfort"),
            "3": Usage("3", "Side Sleeping"),
        }

    rows = [
        {"usage": {"original_id": None, "name": "Side Sleeping"}, "unblurred": True, "score": 8.3},
        {"usage": {"original_id": None, "name": "Cooling"}, "unblurred": True, "score": 7.1},
        {"usage": {"original_id": None, "name": "Nowhere"}, "unblurred": False, "score": None},
    ]
    out = {r["name"]: r for r in _featured_ratings(rows, Schema())}
    assert out["Side Sleeping"]["original_id"] == "3"
    assert out["Cooling"]["original_id"] is None
    assert [c["parent_usage_name"] for c in out["Cooling"]["candidates"]] == ["Sleep", "Comfort"]
    assert out["Nowhere"]["original_id"] is None and "candidates" not in out["Nowhere"]
    # No schema at all is the old behaviour, unchanged.
    assert _featured_ratings(rows)[0]["original_id"] is None


async def test_a_legacy_bench_review_url_resolves(ctx):
    """MEASURED 2026-09-08: `_product_from_url` scanned only the RECENT benches, so every
    legacy-bench review was unresolvable BY URL while the same product resolved by its
    numeric id — the Samsung TU7000 (bench 124) failed on the URL RTINGS' own catalog
    gives for it, under an error that reads as "no such product". A review URL is
    rt_product's documented primary input."""
    recent, legacy = [], []

    def by_bench(body):
        benches = body["variables"]["test_bench_ids"]
        rows = list(recent)
        if "2" in benches:
            rows = rows + legacy
        return {"data": {"products": rows}}

    recent[:] = [product("1", "Alpha One")]
    legacy[:] = [
        product("77", "Alpha Ancient", bench="2", url="/tv/reviews/alpha/alpha-ancient")
    ]
    ctx.transport.payloads["table_tool__products_list"] = by_bench

    ref = await ctx.repo.resolve_product("/tv/reviews/alpha/alpha-ancient", "tv")
    assert (ref.product_id, ref.bench_id, ref.name) == ("77", "2", "Alpha Ancient")

    # The recent set still answers first, and without touching a legacy catalog.
    ref = await ctx.repo.resolve_product("/tv/reviews/alpha/alpha-one", "tv")
    assert ref.product_id == "1"


async def test_a_url_on_no_bench_says_every_bench_was_searched(ctx):
    """The old message said "no product in the tv catalog has the URL", which was true of
    the recent set and read as "no such product"."""
    with pytest.raises(RtingsError) as excinfo:
        await ctx.repo.resolve_product("/tv/reviews/alpha/not-a-real-review", "tv")
    assert excinfo.value.code == "unknown_product"
    assert "on ANY bench" in str(excinfo.value)
    assert "numeric product id" in str(excinfo.value)
