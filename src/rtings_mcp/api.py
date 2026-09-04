"""Typed wrappers for the ``/api/v2/safe/`` queries (RECON §1).

**Request shapes are per-query — do not generalize by prefix.** ``table_tool__*`` take
``{"variables": {...}}``; ``app/product_vue_page__page_body`` takes
``{"variables": {...}, share_token, url_path}``; ``app/search__search_results`` takes a
**bare** ``{count, is_admin, query, type}`` with no ``variables`` wrapper.

Two request-body rules:

* ``named_version`` is always ``"public"``. ``is_admin`` is always ``false`` — that is what
  the logged-out front end sends, and omitting the key would produce a body the real client
  never sends. Never a privileged *value* (``is_admin:true``, ``named_version:"admin"``).
* ``force_blur:false`` and ``unblur_product_ids:[]`` are sent exactly as the front end sends
  them. Populating ``unblur_product_ids`` beyond that is a client unblur hint, i.e.
  circumvention — and it was measured to be ignored anonymously anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import errors
from .config import BASE_URL
from .errors import RtingsError
from .http import Transport

NAMED_VERSION = "public"

Q_COLUMN_OPTIONS = "table_tool__column_options"
Q_PRODUCTS_LIST = "table_tool__products_list"
Q_TEST_RESULTS = "table_tool__test_results"
Q_RATINGS = "table_tool__ratings"
Q_GRAPH_URL = "graph_tool__product_graph_data_url"
Q_SEARCH = "app/search__search_results"
Q_PAGE_BODY = "app/product_vue_page__page_body"
Q_SIDE_BY_SIDE = "app/side_by_side__review"

#: ``test_results`` scales with rows, so the request side caps the test count to keep the
#: response inside the session's size ceiling.
MAX_TESTS_PER_REQUEST = 60


def _dig(payload: Any, *path: str, query: str) -> Any:
    """Walk ``data.<key>...``, raising the drift alarm rather than degrading to empty."""
    node = payload
    for key in ("data", *path):
        if not isinstance(node, dict) or key not in node:
            raise RtingsError(
                errors.PAYLOAD_MISSING,
                f"{query}: expected data.{'.'.join(path)} in the response",
            )
        node = node[key]
    return node


async def column_options(transport: Transport, silo: str) -> dict[str, Any]:
    """The silo schema — the one fixed ~357 KB payload, fetched once per silo."""
    payload = await transport.api_post(
        Q_COLUMN_OPTIONS,
        {"variables": {"silo_url_part": silo, "named_version": NAMED_VERSION}},
        referer=f"{BASE_URL}/{silo}/tools/table",
    )
    return _dig(payload, "silo", query=Q_COLUMN_OPTIONS)


async def products_list(
    transport: Transport, silo: str, bench_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """The catalog for a bench set. Carries ``published`` — the second, orthogonal blur."""
    payload = await transport.api_post(
        Q_PRODUCTS_LIST,
        {
            "variables": {
                "test_bench_ids": [str(b) for b in bench_ids],
                "named_version": NAMED_VERSION,
                "is_admin": False,
            }
        },
        referer=f"{BASE_URL}/{silo}/tools/table",
    )
    products = _dig(payload, "products", query=Q_PRODUCTS_LIST)
    if not isinstance(products, list):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_PRODUCTS_LIST}: products is not a list")
    return products


async def test_results(
    transport: Transport,
    silo: str,
    bench_ids: Sequence[str],
    original_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Scalar measurements for ``(benches x tests)``, **all products** — there is no product
    filter, which is precisely why the response is the coverage record."""
    payload = await transport.api_post(
        Q_TEST_RESULTS,
        {
            "variables": {
                "test_bench_ids": [str(b) for b in bench_ids],
                "original_ids": [str(t) for t in original_ids],
                "named_version": NAMED_VERSION,
                "is_admin": False,
                "force_blur": False,
                "unblur_product_ids": [],
            }
        },
        referer=f"{BASE_URL}/{silo}/tools/table",
    )
    rows = _dig(payload, "test_results", query=Q_TEST_RESULTS)
    if not isinstance(rows, list):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_TEST_RESULTS}: test_results is not a list")
    return rows


async def ratings(
    transport: Transport,
    silo: str,
    bench_ids: Sequence[str],
    original_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """0-10 usage scores. Usage definitions carry no ``insider_only``, so availability here
    is derived from observed ``unblurred`` only."""
    payload = await transport.api_post(
        Q_RATINGS,
        {
            "variables": {
                "test_bench_ids": [str(b) for b in bench_ids],
                "original_ids": [str(u) for u in original_ids],
                "named_version": NAMED_VERSION,
                "is_admin": False,
                "force_blur": False,
                "unblur_product_ids": [],
            }
        },
        referer=f"{BASE_URL}/{silo}/tools/table",
    )
    rows = _dig(payload, "ratings", query=Q_RATINGS)
    if not isinstance(rows, list):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_RATINGS}: ratings is not a list")
    return rows


async def graph_data_url(
    transport: Transport, silo: str, product_id: str, test_original_id: str
) -> str | None:
    """The CDN path for one ``(product, test)`` curve, or ``None`` when this product has
    none — ``graph_not_available``, which is the common case, not the exception."""
    payload = await transport.api_post(
        Q_GRAPH_URL,
        {
            "variables": {
                "named_version": NAMED_VERSION,
                "product_id": str(product_id),
                "test_original_id": str(test_original_id),
            }
        },
        referer=f"{BASE_URL}/{silo}/graph",
    )
    try:
        results = _dig(payload, "product", "review", "test_results", query=Q_GRAPH_URL)
    except RtingsError:
        return None
    if not isinstance(results, list) or not results:
        return None
    url = results[0].get("graph_data_url") if isinstance(results[0], dict) else None
    return str(url) if url else None


async def search(transport: Transport, query: str, count: int = 10) -> dict[str, Any]:
    """Cross-silo live search. Bare body, **no** ``variables`` wrapper."""
    payload = await transport.api_post(
        Q_SEARCH,
        {"count": int(count), "is_admin": False, "query": query, "type": "full"},
        referer=f"{BASE_URL}/",
    )
    result = _dig(payload, "search_results", query=Q_SEARCH)
    if not isinstance(result, dict):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_SEARCH}: search_results is not an object")
    return result


async def side_by_side_review(
    transport: Transport, silo: str, product_id: str
) -> dict[str, Any]:
    """The compare tool's view of one review — RTINGS' *words*, where the table path has
    only numbers (RECON §12.16).

    Bare ``{product_id}``, no ``variables`` wrapper. What it adds, anonymously, even on a
    category that withholds every measurement:

    * ``product_score_sets`` — the per-usage verdict prose ("good for mixed usage because…");
    * ``summaries`` — the pros/cons blurbs, with ``priority`` separating them;
    * ``score_sets`` — how each usage score is composed, with weights.

    **Its ``test_results`` are deliberately ignored.** They are a *third* row shape carrying
    no ``unblurred`` key at all, so the seven-state normalizer has nothing to branch on; the
    table and review paths already answer that question properly.

    **Metering is unverified.** This is the public compare tool rather than the metered
    review page, and a tool that spent a preview per comparison would be unusable — but that
    is reasoning, not measurement, so Phase 0 confirms it.
    """
    payload = await transport.api_post(
        Q_SIDE_BY_SIDE,
        {"product_id": str(product_id)},
        referer=f"{BASE_URL}/{silo}/tools/compare",
    )
    review = _dig(payload, "review", query=Q_SIDE_BY_SIDE)
    if not isinstance(review, dict):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_SIDE_BY_SIDE}: review is not an object")
    return review


async def page_body(transport: Transport, url_path: str) -> dict[str, Any]:
    """One full review (~442 KB).

    **This is the metered endpoint.** Every guard around spending a free account's previews
    lives in the calling tool; this function performs the POST and nothing else. The session
    runs ``max_retries=0``, so one call is one request — the default of 3 would have been
    three consumed previews.
    """
    path = url_path if url_path.startswith("/") else f"/{url_path}"
    payload = await transport.api_post(
        Q_PAGE_BODY,
        {
            "variables": {"url": path, "named_version": NAMED_VERSION, "version_id": None},
            "share_token": None,
            "url_path": path,
        },
        referer=f"{BASE_URL}{path}",
    )
    # Verified 2026-09-03: the body lands at ``data.page``, and the review itself at
    # ``data.page.product.review`` (402 rows on TV bench 227, every one carrying
    # ``rendered_value`` + ``score`` and **no** ``value`` key).
    page = _dig(payload, "page", query=Q_PAGE_BODY)
    if not isinstance(page, dict):
        raise RtingsError(errors.PAYLOAD_MISSING, f"{Q_PAGE_BODY}: page is not an object")
    return page
