"""MCP wiring (SPEC §7, §9).

Uses the **official** SDK. Note the API name: ``mcp`` 2.x renamed ``FastMCP`` to
``MCPServer`` (``from mcp.server.mcpserver import MCPServer``); the old
``mcp.server.fastmcp`` path raises ``ModuleNotFoundError`` with a migration message on 2.x.
This is the canonical SDK, not the third-party ``fastmcp`` 4.x package — they are separate,
diverged projects.

Logs go to **stderr**: stdout is the protocol channel.
"""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import __version__, services
from .context import Context, get_context
from .envelope import error_envelope
from .errors import RtingsError
from .models import (
    GraphEnvelope,
    ProductEnvelope,
    RatingsEnvelope,
    RecommendationsEnvelope,
    SchemaEnvelope,
    SearchEnvelope,
    SilosEnvelope,
)

log = logging.getLogger("rtings_mcp")

#: A **hint**, never a JSON-Schema ``enum``. An ``enum`` is enforced client-side, so a silo
#: RTINGS adds mid-release would be unreachable until a new release ships, and it would
#: create a second allowlist that can disagree with the live ``static.silos``. Validation
#: happens server-side against the live list only; a value outside this hint is fetched
#: normally with a ``silo_hint_drift`` warning.
SILO_HINT = (
    "tv, headphones, monitor, soundbar, mouse, keyboard, printer, robot-vacuum, vacuum, "
    "dehumidifier, projector, toaster-oven, keyboard-switch, air-purifier, running-shoes, "
    "humidifier, refrigerator, mattress, air-conditioner, microwave, blender, air-fryer, "
    "toaster, vpn, router, speaker, camera, laptop"
)

SiloParam = Annotated[
    str,
    Field(
        description=(
            "RTINGS category (its url_part). Known values as of the last release: "
            f"{SILO_HINT}. This is a hint, not a closed set — the server validates against "
            "RTINGS' live silo list, so a newly added category works immediately."
        ),
        examples=["tv", "mattress", "air-purifier", "headphones"],
    ),
]

mcp = MCPServer(
    name="rtings",
    version=__version__,
    instructions=(
        "RTINGS.com test data as structured tools.\n\n"
        "READ THIS FIRST: RTINGS gates some measurements server-side, and enforcement is "
        "PER CATEGORY. Roughly half the categories serve their full measurements and 0-10 "
        "scores anonymously; the flagship ones (tv, headphones, monitor, mouse, keyboard, "
        "soundbar, speaker, printer, laptop, robot-vacuum, projector, router) withhold them "
        "without a membership. Call rt_silos() first to see which is which right now — the "
        "split is a snapshot of RTINGS' business decisions and it changes.\n\n"
        "A gated value comes back null with status 'tested_gated'. That means RTINGS "
        "MEASURED it and is withholding it. It is NOT the same as 'not_tested' (RTINGS did "
        "not measure this product on this test), 'not_applicable' (the test does not apply), "
        "'review_unpublished' (an Early Access review: RTINGS has the data and publishes it "
        "to Insiders, but withheld it from this session), or 'coverage_unknown' (the server "
        "cannot show its cached data covers this product). Never report a gated null as "
        "'not tested'.\n\n"
        "Comparisons are scoped to a test bench, RTINGS' methodology version. Results are "
        "nested by bench and never flattened into one cross-bench ranking.\n\n"
        "HOW TO ANSWER A QUESTION:\n"
        "1. rt_silos() -> is this category 'full' or 'gated'?\n"
        "2. Need a test's id? rt_schema(silo) gives the category tree; "
        "rt_schema(silo, group=<id>) gives that category's tests with their original_ids.\n"
        "3. FULL category: rt_ratings(silo, tests=[ids], sort=<id or name>, filters=...) "
        "ranks and compares directly.\n"
        "4. GATED category: the numbers are withheld, so use rt_product(url, "
        "include_verdicts=true) for RTINGS' written verdict, pros and cons on one product, "
        "rt_recommendations(silo) for their ranking with reasoning, and rt_graph for the "
        "curves that are published anyway.\n"
        "5. filters and sort accept a test's original_id OR its name; `variant` filters by "
        "the size RTINGS tested."
    ),
)


def _ctx() -> Context:
    return get_context()


def _safe(model: Any, payload: dict[str, Any]) -> Any:
    """Validate the envelope against the declared output model.

    A validation failure is a bug in *our* shaping, so it degrades to a structured
    ``payload_missing`` rather than crashing the tool call — but it is logged loudly.
    """
    try:
        return model.model_validate(payload)
    except Exception:
        log.exception("response failed its own output schema")
        from . import errors

        return model.model_validate(
            error_envelope(
                RtingsError(errors.PAYLOAD_MISSING, "the response failed its own output schema")
            )
        )


async def _run(model: Any, coro: Any) -> Any:
    """Run one tool call: a fresh warning scope, structured errors, validated output.

    The scope wraps the error path too — warnings collected before a failure are exactly
    the ones that explain it, and resetting first would throw them away.
    """
    ctx = _ctx()
    with ctx.repo.warning_scope():
        try:
            return _safe(model, await coro)
        except RtingsError as exc:
            # Errors are structured VALUES in the envelope, never MCP protocol errors: an
            # agent must be able to tell "RTINGS has no data" from "the fetch failed".
            return _safe(
                model,
                error_envelope(
                    exc, probe=ctx.auth.cached_probe(), warnings=list(ctx.repo.warnings)
                ),
            )


@mcp.tool()
async def rt_silos(refresh: bool = False) -> SilosEnvelope:
    """List RTINGS' categories and, per category, whether its numbers are answerable now.

    Call this first. `data_completeness` is derived from what actually came back unblurred
    on this machine's last fetch of that category: `full` (values and scores served),
    `gated` (withheld without a membership), `partial`, or `unknown` (nothing fetched yet).
    `has_paywall` is true for all 28 and tells you nothing.
    """
    return await _run(SilosEnvelope, services.rt_silos(_ctx(), refresh=refresh))


@mcp.tool()
async def rt_schema(
    silo: SiloParam,
    bench: str | None = None,
    group: str | None = None,
    refresh: bool = False,
) -> SchemaEnvelope:
    """What RTINGS measures in a category: test definitions, units, hierarchy, usages.

    With no `group`, returns the group/category tree with per-group counts. Pass a group's
    `original_id` as `group` to get that group's leaf tests with their units and precision.
    `original_id` is the stable key everywhere in this server — names repeat across groups.

    `insider_only: true` marks a test *gate-able*, not gated. Whether it is actually served
    depends on the category; `rt_ratings` reports what came back.
    """
    return await _run(
        SchemaEnvelope,
        services.rt_schema(_ctx(), silo, bench=bench, group=group, refresh=refresh),
    )


@mcp.tool()
async def rt_ratings(
    silo: SiloParam,
    bench: list[str] | None = None,
    tests: list[str] | None = None,
    usages: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    limit: int = 10,
    offset: int = 0,
    refresh: bool = False,
) -> RatingsEnvelope:
    """Rank and compare products in a category: catalog, 0-10 usage scores, and measurements.

    `tests` takes test `original_id`s (from `rt_schema`) and projects those measurements
    onto every product. `usages` likewise for the 0-10 usage scores, defaulting to the
    category's headline usages — pass `usages=[]` to skip them when you only want
    measurements, or explicit ids to include sub-usages.

    `limit` defaults to 10 products per group; use `offset` to page.

    `bench` defaults to the recent-bench set RTINGS itself renders together. Results come
    back in `data.groups`, one group per comparable bench set — never flattened across
    benches, because a bench is a methodology version.

    Every value carries a `status`: `tested_visible` (here it is), `tested_gated` (RTINGS
    measured it and is withholding it), `not_applicable` (the test does not apply to this
    product), `not_tested` (RTINGS did not measure this product on this test),
    `review_unpublished` (the review is in progress) or `coverage_unknown` (the cached data
    cannot be shown to cover this product). A gated null is NOT `not_tested`.

    `filters` accepts `{"<test original_id or name>": ">1000"}` plus `brand`,
    `name_contains`, `published`, and `variant` — the size RTINGS tested, e.g.
    `{"variant": "65"}` for 65-inch TVs. Most categories have no "Size" test, so `variant`
    is the only way to ask that. `sort` takes a test/usage id or name (prefix `-` for
    descending, `+` for ascending); it defaults to release date. A filter or sort on a field
    that is gated for these rows is **not applied**, and the envelope says so — otherwise
    "0 results" would read as "no product qualifies" when the truth is "you cannot see it".
    """
    return await _run(
        RatingsEnvelope,
        services.rt_ratings(
            _ctx(),
            silo,
            bench=bench,
            tests=tests,
            usages=usages,
            filters=filters,
            sort=sort,
            limit=limit,
            offset=offset,
            refresh=refresh,
        ),
    )


@mcp.tool()
async def rt_product(
    product: str,
    silo: str | None = None,
    group: str | None = None,
    include_prose: bool = False,
    include_media: bool = False,
    include_verdicts: bool = False,
    consume_preview: bool = False,
    refresh: bool = False,
) -> ProductEnvelope:
    """Every test result for one product, grouped by RTINGS' own hierarchy.

    `product` takes a review URL, a numeric RTINGS product id, or a model name to search
    for. Pass `group` (a group `original_id`) to bound the response; `include_prose` adds
    RTINGS' per-test commentary and `include_media` adds image/video URLs.

    `include_verdicts=true` (one extra request) adds RTINGS' per-usage written verdicts
    ("good for mixed usage because..."), their pros and cons, and how each usage score is
    composed. **On a category that withholds measurements this is the substantive answer** —
    the verdicts are served even when every number comes back null, so use it whenever
    `rt_silos` says a category is `gated`.

    Numbers on this path are parsed from RTINGS' display strings and are display-rounded
    (each is labelled `value_source: "rendered"`). When you need the unrounded value, use
    `rt_ratings` with `tests=[...]`.

    COST: on a free RTINGS account this endpoint spends one of your metered review
    previews. It refuses by default; pass `consume_preview=true` to allow it. Anonymous and
    member sessions have nothing to spend.
    """
    return await _run(
        ProductEnvelope,
        services.rt_product(
            _ctx(),
            product,
            silo=silo,
            group=group,
            include_prose=include_prose,
            include_media=include_media,
            include_verdicts=include_verdicts,
            consume_preview=consume_preview,
            refresh=refresh,
        ),
    )


@mcp.tool()
async def rt_graph(
    product: str,
    test: str,
    silo: str | None = None,
    full: bool = False,
    max_points: int | None = None,
) -> GraphEnvelope:
    """The measurement curve behind one test, as RTINGS published it.

    Only `kind: "graph"` tests have a curve (`rt_schema` says which); anything else returns
    a structural `no_graph`, and a graph test whose product has no published curve returns
    `graph_not_available` — curve coverage is per product, not per category.

    Curves are resampled by SELECTING points RTINGS shipped — never interpolated, averaged
    or smoothed. `full=true` returns the raw series. No headline number is derived from the
    curve.
    """
    return await _run(
        GraphEnvelope,
        services.rt_graph(_ctx(), product, test, silo=silo, full=full, max_points=max_points),
    )


@mcp.tool()
async def rt_search(query: str, count: int = 10) -> SearchEnvelope:
    """Find a product across every RTINGS category by model name or number.

    Uses RTINGS' own live search index, so an empty result means the index has no match —
    it is not evidence that a product was never tested.
    """
    return await _run(SearchEnvelope, services.rt_search(_ctx(), query, count=count))


@mcp.tool(name="rt_recommendations")
async def rt_recommendations(
    silo: SiloParam, list: str | None = None, refresh: bool = False
) -> RecommendationsEnvelope:
    """RTINGS' editorial best-of rankings for a category, with their reasoning.

    With no `list`, returns the category's discovered best-of lists. Pass one of those
    `list` values to get that ranking: ordered picks, each with RTINGS' own explanation of
    why it is there. The ranking and prose are served regardless of membership.
    """
    return await _run(
        RecommendationsEnvelope,
        services.rt_recommendations(_ctx(), silo, list=list, refresh=refresh),
    )


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    configure_logging()
    mcp.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
