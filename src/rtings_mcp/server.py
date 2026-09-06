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
from .config import SILO_HINT
from .context import Context, get_context
from .envelope import error_envelope
from .errors import RtingsError
from .models import (
    AuthStatusEnvelope,
    GraphEnvelope,
    ProductEnvelope,
    RatingsEnvelope,
    RecommendationsEnvelope,
    SchemaEnvelope,
    SearchEnvelope,
    SignInEnvelope,
    SilosEnvelope,
)

log = logging.getLogger("rtings_mcp")

# The hint text itself lives in `config.KNOWN_SILOS` so `repository.resolve_silo` can raise
# `silo_hint_drift` against it without importing the server (a cycle).

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
        "1. rt_silos() -> is this category 'full' or 'gated'? If any envelope says "
        "session: 'member', the user is a signed-in Insider and EVERY category is full for "
        "them — skip the gated branch entirely.\n"
        "2. rt_recommendations(silo) FIRST for a shopping question: RTINGS publishes "
        "best-of lists by use, size and budget (pet-hair, by-size/65-inch, side-sleepers, "
        "budget…) with their reasoning — usually the ranking you were about to derive by "
        "hand. A list answers ONE angle: for a multi-attribute ask (quiet AND wireless AND "
        "low-profile) take candidates from the lists and confirm every attribute with ONE "
        "rt_ratings(filters={'product_ids': [the picks' product_id values]}) call, since a "
        "list's #1 can fail the attribute the list is not about.\n"
        "3. Need a test's id? rt_schema(silo) gives the category tree; "
        "rt_schema(silo, group=<id>) gives that group's tests with their original_ids. A "
        "group with leaf_test_count 0 is scored as a usage or described only in prose.\n"
        "4. FULL category: rt_ratings(silo, tests=[ids], sort=<id or name>, filters=...) "
        "ranks and compares. Keep it small: a few tests, limit<=10. The default bench set "
        "is every bench RTINGS still renders together (2 on mouse, 10 on running-shoes); "
        "narrowing with bench=[...] drops products tested on the other recent benches, so "
        "do it only when the warning that names a product's bench tells you to. "
        "Responses over ~40K characters are trimmed per group with a `response_truncated` "
        "warning — page with offset. To compare specific products use "
        "filters={'product_ids': [...]}.\n"
        "5. GATED category, not signed in: the numbers are withheld, so use rt_product(url, "
        "include_verdicts=true) for RTINGS' written verdict, pros and cons on one product, "
        "rt_recommendations(silo) for their ranking with reasoning, and rt_graph for the "
        "curves that are published anyway.\n"
        "6. filters and sort accept a test's original_id OR its name; `variant` filters by "
        "the size RTINGS tested. Each sort ranks ONE field; blend two by calling twice.\n"
        "7. RTINGS publishes no prices. Nothing here can answer 'cheapest' or 'under $X'; "
        "say so rather than guess. Spec-sheet facts RTINGS does not measure (IP/water "
        "rating, OLED burn-in/longevity, warranty) are not tests either: they appear only "
        "in verdict/recommendation prose, if at all.\n"
        "8. RTINGS tests ONE size per model: `tested_variant` on every product row is the "
        "SKU the numbers describe. A 'Best 65-inch' pick may have been measured at 77 "
        "inches; say so when it matters.\n\n"
        "SIGNING IN: rt_auth_status() reports what credential is stored and whether it is "
        "live. If the user ASKS to sign in or connect their membership, call rt_sign_in() and "
        "then rt_auth_status(wait_s=45), repeating while sign_in is 'waiting' — a human takes "
        "longer than one tool call. NEVER call rt_sign_in unasked: it opens a browser window "
        "on the user's screen. A gated category is not a reason to sign them in; say what is "
        "withheld and let them decide."
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
async def rt_silos(silos: list[str] | None = None, refresh: bool = False) -> SilosEnvelope:
    """List RTINGS' categories and, per category, whether its numbers are answerable now.

    Call this first. `data_completeness` is derived from what actually came back unblurred
    on this machine's last SIGNED-OUT fetch of that category: `full` (values and scores
    served), `gated` (withheld without a membership), `partial`, or `unknown` (nothing
    fetched yet, or only fetched while signed in, which cannot say what anonymous gets).
    When the session is a signed-in Insider, `data.this_session.access` is `full` and the
    column does not apply. `has_paywall` is true for all 28 and tells you nothing.

    `silos=["tv"]` returns just those rows.
    """
    return await _run(SilosEnvelope, services.rt_silos(_ctx(), silos=silos, refresh=refresh))


@mcp.tool()
async def rt_schema(
    silo: SiloParam,
    bench: str | None = None,
    group: str | None = None,
    find: str | None = None,
    refresh: bool = False,
) -> SchemaEnvelope:
    """What RTINGS measures in a category: test definitions, units, hierarchy, usages.

    With no `group`, returns the group/category tree with per-group counts. Pass a group's
    `original_id` (or name) as `group` to get that group's leaf tests with their units and
    precision — a top-level CATEGORY id works too and returns every test beneath it in one
    call. **`find="input lag"` searches every test and usage on the bench** by name, by
    group path ("panel" finds Panel Technology's Sub-Type) and by a word test's values, in
    one call — use it instead of walking the tree when you know roughly what the test is
    called; several terms at once as `find="face, weight, battery"` (each hit says which
    terms it matched). Feature flags and specs ("bagel", "slice capacity", "USB-C") are
    tests too, so `find` locates them faster than walking the tree. `original_id` is the
    stable key everywhere in this server — names repeat across groups (headphones has three
    "RMS Deviation From Target"); a repeated name must be passed as its id or qualified as
    "Group/Name".

    `insider_only: true` marks a test *gate-able*, not gated. Whether it is actually served
    depends on the category; `rt_ratings` reports what came back.
    """
    return await _run(
        SchemaEnvelope,
        services.rt_schema(
            _ctx(), silo, bench=bench, group=group, find=find, refresh=refresh
        ),
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
    include_uncatalogued: bool = False,
    refresh: bool = False,
) -> RatingsEnvelope:
    """Rank and compare products in a category: catalog, 0-10 usage scores, and measurements.

    **Size.** Responses are trimmed to a character budget per call: when a window would
    exceed it, each group keeps the head of its ranking and the envelope carries a
    `response_truncated` warning with the offset to continue from. To fit more products per
    call, pass fewer `tests`/`usages` or `limit<=10`. `bench=[...]` narrows to named
    benches; the default is every recent bench (2 to 10 depending on the category), and a
    product tested on an older recent bench drops out of a narrowed call (the `product_ids`
    warning names its bench).

    **Shape.** Each product's `tests` rows carry only the answer (`original_id`, `status`,
    `value`, `gated`, `score`, `display`, `as_of`); `data.tests[original_id]` holds the
    definition once — name, kind, `unit` (of `value`), `display_unit`, hierarchy — plus
    `score_direction`, derived from RTINGS' own scores in this response (`lower_is_better`
    for input lag or a scratchy factor). `sort` on a test ranks by its value, never its
    score. A `number` test whose `unit` is `"score"` is itself a 0-10 rating (robot-vacuum
    "Water Left On Floor"), not a physical quantity.

    **Specific products.** `filters={"product_ids": ["39008", "63313"]}` compares exactly
    those (ids from rt_search); `name_contains` is a substring match and can catch siblings.

    **Uncatalogued products.** Some products return measurements but are absent from
    RTINGS' product listing, so they have no name or brand here. They are summarised as a
    `coverage: "uncatalogued"` group carrying only `product_ids`; pass
    `include_uncatalogued=true` (or filter by their ids) to rank their values, and
    `rt_product(<id>, silo=...)` to identify one.

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

    `filters` accepts `{"<test original_id or name>": ">1000"}` — also a two-sided range
    (`"13..14"`, `"13 to 14"`, `">=13 <=14"`); a word test matches its value exactly or as a
    substring (`{"Bagel Mode": "One-Sided"}`) and `"!=No"` excludes — plus `brand`,
    `name_contains`, `published`,
    and `variant` — the size RTINGS tested, e.g. `{"variant": "65"}` for 65-inch TVs. TVs,
    soundbars and the like have no "Size" test, so `variant` is the way to ask that;
    laptop and monitor DO have a numeric "Size" test, and there `{"Size": "31..33"}` is
    the test (with operators and ranges), while `variant` stays the tested SKU.
    `sort` takes a test/usage id or name (prefix `-` for
    descending, `+` for ascending; **no prefix means descending**, so prefix `+` for
    lower-is-better metrics like input lag or dE); it defaults to release date.

    **A field you filter or sort on is fetched for you** — you do not also have to list it in
    `tests=`. Names and ids are interchangeable everywhere: `tests=["Thickness"]` and
    `tests=["26891"]` are the same request.

    When a field genuinely cannot be compared the predicate is **not applied** and the
    envelope names which of three things happened: it is gated for this session, it is not on
    the bench(es) queried (call rt_schema for one that is), or RTINGS published no value for
    it. Otherwise "0 results" would read as "no product qualifies" when the truth is "you
    cannot see it". Products with no comparable value sort last in both directions.
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
            include_uncatalogued=include_uncatalogued,
            refresh=refresh,
        ),
    )


@mcp.tool()
async def rt_product(
    product: str,
    silo: str | None = None,
    group: str | None = None,
    tests: list[str] | None = None,
    include_prose: bool = False,
    include_media: bool = False,
    include_verdicts: bool = False,
    include_scoring: bool = False,
    include_results: bool = True,
    consume_preview: bool = False,
    refresh: bool = False,
) -> ProductEnvelope:
    """Every test result for one product, each row carrying its place in RTINGS' hierarchy.

    `product` takes a review URL, a numeric RTINGS product id, or a model name to search
    for. Pass `group` (a group `original_id`) or `tests=[ids or names]` to bound the
    response (`tests` is the cheap cross-check of a few known numbers): `group` scopes `results`
    and the per-group `commentary`, while the review-level `summary`, `verdicts` and
    `scoring` are whole-review by nature. `include_prose` adds RTINGS' commentary (HTML,
    with site-relative links) and `include_media` adds image/video URLs. A group with no
    scored tests (rt_schema `leaf_test_count: 0`, e.g. monitor "Text Clarity") returns
    `results: []` by design — its content is the prose.

    `include_verdicts=true` (one extra request) adds RTINGS' per-usage written verdicts
    ("good for mixed usage because...") and their pros and cons; `include_scoring=true` adds
    how each usage score is composed (weights per component — rarely needed, a third of the
    response). **On a category that withholds measurements this is the substantive answer** —
    the verdicts are served even when every number comes back null, so use it whenever
    `rt_silos` says a category is `gated`. `include_results=false` drops the measurement
    rows when you only want the words (a full review is ~240 rows). `include_prose` without
    `group` returns the commentary for EVERY group of the review (~30 K characters); pass
    `group` to get one section's.

    Numbers on this path are parsed from RTINGS' display strings and are display-rounded
    (each is labelled `value_source: "rendered"`); where the display shows the stored unit
    in parentheses ("4.0 lbs (1.8 kg)") the parenthesised figure is served in that unit,
    so `unit` agrees with rt_ratings. When you need the unrounded value, use `rt_ratings`
    with `tests=[...]`.

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
            tests=tests,
            include_prose=include_prose,
            include_media=include_media,
            include_verdicts=include_verdicts,
            include_scoring=include_scoring,
            include_results=include_results,
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

    Each point is `[x, series1, series2, ...]`; `header` names the columns in that order and
    `axes` carries the axis titles (with units) and scales as RTINGS declared them.
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
    silo: SiloParam,
    list: str | None = None,
    limit: int | None = None,
    include_reasoning: bool = True,
    refresh: bool = False,
) -> RecommendationsEnvelope:
    """RTINGS' editorial best-of rankings for a category, with their reasoning.

    With no `list`, returns the category's discovered best-of lists. Pass one of those
    `list` values to get that ranking: ordered picks, each with RTINGS' own explanation of
    why it is there (`reasoning`, HTML), its `product_id` — the join key: pass the picks'
    ids to `rt_ratings(filters={"product_ids": [...]})` for one comparison table instead of
    one call per pick — the `tested_variant` and `test_bench` RTINGS measured (a "75-77
    inch" list can carry picks tested at 65") — and two blocks of numbers RTINGS chose to
    feature for that list:
    `featured_results`
    (tests AND group scores — on this surface a group carries a 0-10 score, unlike
    rt_ratings) and `usage_scores`. Both follow the same `status`/`gated` rules as
    everywhere else; `gated: null` means there was no value to gate, and a spec flag's
    `score` is RTINGS' 0-10 score for that spec. `limit` caps the picks (`pick_count` is
    the full length); `include_reasoning=false` drops the prose (most of the bytes) when
    you only want the picks and their numbers. The ranking and prose are served regardless
    of membership; the featured numbers are blurred like everything else.
    """
    return await _run(
        RecommendationsEnvelope,
        services.rt_recommendations(
            _ctx(),
            silo,
            list=list,
            limit=limit,
            include_reasoning=include_reasoning,
            refresh=refresh,
        ),
    )


@mcp.tool(name="rt_sign_in")
async def rt_sign_in(force: bool = False) -> SignInEnvelope:
    """Connect the user's RTINGS membership by signing in, in a real browser window.

    Call this ONLY when the user asks to sign in or connect their membership. It opens a
    window on RTINGS' own sign-in page; the user types there and the server never sees the
    password, only the resulting session cookie. Nothing is stored unless RTINGS confirms the
    cookie is signed in.

    It answers in about a second and does NOT wait for the human. Follow it with
    `rt_auth_status(wait_s=45)` to wait for the outcome, and repeat that while `sign_in` is
    still `waiting`. Do not call `rt_sign_in` again while one is in progress.

    `force=true` replaces a credential RTINGS currently accepts — for switching accounts.
    A rejected or missing one is replaced without it.
    """
    from . import auth_tools

    ctx = _ctx()
    with ctx.repo.warning_scope():
        try:
            data = await auth_tools.rt_sign_in(ctx, force=force)
        except RtingsError as exc:
            return _safe(SignInEnvelope, _auth_error(exc, ctx))
        return _safe(
            SignInEnvelope,
            {
                "session": data.get("session", "unknown"),
                "warnings": list(ctx.repo.warnings),
                "error": None,
                "data": data,
            },
        )


@mcp.tool(name="rt_auth_status")
async def rt_auth_status(wait_s: int = 0) -> AuthStatusEnvelope:
    """What credential is stored, and how a sign-in in progress is going.

    Safe to call any time; it never opens a window and never spends anything. `wait_s`
    long-polls for the next change in a sign-in that is running (capped at 45 s, which is
    under Claude Desktop's 60 s tool-call limit) — that is how you wait for a human to finish
    signing in without blocking a single call for minutes.

    `session` is credential health, never entitlement to data: `member`, `free`, `anonymous`,
    `expired`, or `unknown` when no probe has run yet.
    """
    from . import auth_tools

    ctx = _ctx()
    with ctx.repo.warning_scope():
        try:
            data = await auth_tools.rt_auth_status(ctx, wait_s=wait_s)
        except RtingsError as exc:
            return _safe(AuthStatusEnvelope, _auth_error(exc, ctx))
        return _safe(
            AuthStatusEnvelope,
            {
                "session": data.get("session", "unknown"),
                "warnings": list(ctx.repo.warnings),
                "error": None,
                "data": data,
            },
        )


def _auth_error(exc: RtingsError, ctx: Context) -> dict[str, Any]:
    """The lean envelope's error shape. `error_envelope` builds the measurement envelope,
    whose `data_tier`/`scores_available` have no meaning for a sign-in."""
    probe = ctx.auth.cached_probe()
    return {
        "session": probe.session if probe else "unknown",
        "warnings": list(ctx.repo.warnings),
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": bool(getattr(exc, "retryable", False)),
        },
        "data": None,
    }


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
