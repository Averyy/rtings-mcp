"""MCP wiring (SPEC §7, §9).

Uses the **official** SDK. Note the API name: ``mcp`` 2.x renamed ``FastMCP`` to
``MCPServer`` (``from mcp.server.mcpserver import MCPServer``); the old
``mcp.server.fastmcp`` path raises ``ModuleNotFoundError`` with a migration message on 2.x.
This is the canonical SDK, not the third-party ``fastmcp`` 4.x package — they are separate,
diverged projects.

Logs go to **stderr**: stdout is the protocol channel.
"""

from __future__ import annotations

import inspect
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
    ArticleEnvelope,
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

#: The routing prose the calling LLM reads. **The client cuts this at 2048 characters**, the
#: same limit it applies to a tool description (both measured 2026-09-08) — and it used to be
#: 4,314, so steps 3-8 and the whole sign-in section, including "NEVER call rt_sign_in
#: unasked", never reached anyone. Per-tool detail belongs in each tool's own description,
#: which is delivered separately; this file is for what no single tool can say: the gate, the
#: status domain, bench scoping, which tool to reach for, and the sign-in guard.
#: `test_the_instructions_fit_the_client_budget` fails if it grows past the cut again.
INSTRUCTIONS = (
    "RTINGS.com test data as structured tools.\n\n"
    "GATING IS PER CATEGORY: the flagship ones (tv, headphones, monitor, mouse, keyboard, "
    "soundbar, speaker, printer, laptop, robot-vacuum, projector, router) withhold "
    "measurements without a membership; the other 16 serve them. Call rt_silos() FIRST — "
    "that split is RTINGS' business decision and it moves. If any envelope says session: "
    "'member', every category is full.\n\n"
    "A gated value is null with status 'tested_gated': RTINGS MEASURED it and withholds it. "
    "NOT 'not_tested' (never measured), 'not_applicable', 'review_unpublished' (Early "
    "Access; a membership lifts it) or 'coverage_unknown'. NEVER report a gated null as "
    "'not tested'.\n\n"
    "A test bench is RTINGS' methodology version; a product sits on exactly one. Results are "
    "grouped by bench, never flattened.\n\n"
    "ROUTING:\n"
    "1. Shopping -> rt_recommendations(silo) FIRST: RTINGS' own best-of lists by use, size, "
    "budget and BRAND, with reasoning — usually the ranking you'd derive. A list "
    "answers ONE angle: confirm a multi-attribute ask with one rt_ratings "
    "filters={'product_ids': [the picks' ids]}.\n"
    "2. Compare/rank -> rt_ratings. Keep it small: a few tests, limit<=10.\n"
    "3. One product -> rt_product; on a GATED category include_verdicts=true is the "
    "substantive answer — verdict and pros/cons are served even where every number is "
    "withheld, and rt_graph's curves are published anyway.\n"
    "4. Which test is it? -> rt_schema(silo, find='input lag'). Find a model -> "
    "rt_search(query, silo=).\n"
    "5. Not a measurement (lineups, burn-in, how a test works) -> rt_article.\n\n"
    "RTINGS publishes NO PRICES, and specs they don't measure (IP rating, burn-in) appear "
    "only in prose — say so rather than guess at either.\n\n"
    "They test ONE size per model: `tested_variant` is the SKU the numbers describe — a "
    "'Best 65-inch' pick may have been measured at 77.\n\n"
    "NEVER call rt_sign_in unasked — it opens a browser window on the user's screen, and a "
    "gated category is no reason to sign someone in. If they DO ask: rt_sign_in() then "
    "rt_auth_status(wait_s=45), repeating while 'waiting'."
)

mcp = MCPServer(
    name="rtings",
    version=__version__,
    instructions=INSTRUCTIONS,
)


def tool(*args: Any, **kwargs: Any) -> Any:
    """Register a tool with a **Python-version-independent** description.

    CPython 3.13 strips a docstring's common leading indentation at compile time; 3.12 does
    not. The same source therefore shipped a description ~4 characters per line longer on
    3.12 — and the client cuts a description at exactly 2048 characters, so on the older
    runtime those leading spaces pushed real content off the end (measured 2026-09-08 in
    CI: `rt_ratings`' paging fell outside the window on 3.12 and inside it on 3.14).

    `inspect.cleandoc` here makes the wire description identical on every supported Python
    and spends the whole window on words rather than indentation.
    """

    def register(fn: Any) -> Any:
        if fn.__doc__:
            fn.__doc__ = inspect.cleandoc(fn.__doc__)
        return mcp.tool(*args, **kwargs)(fn)

    return register


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


@tool()
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


@tool()
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


@tool()
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

    **filters** — ids and names are interchangeable everywhere:
    * a test/usage id or name: `{"Peak Brightness": ">1000"}`, a two-sided range
      (`"13..14"`, `"13 to 14"`, `">=13 <=14"`); a **word** test matches its value exactly
      or as a substring (`{"Bagel Mode": "One-Sided"}`) and `"!=No"` excludes.
    * `{"product_ids": ["39008", "63313"]}` — compare exactly those (ids from rt_search).
    * `brand`, `name_contains` (substring, catches siblings), `published`.
    * `variant` — the size RTINGS **tested** (`{"variant": "65"}`); `sold_in` — the sizes
      a product is **sold** in, with the same operators (`{"sold_in": ">=83"}`). Most
      categories have no "Size" test, so these are how you ask that; laptop and monitor DO
      have one, and there `{"Size": "31..33"}` is the test.

    **sort** takes a test/usage id or name. **No prefix means descending** — prefix `+` for
    lower-is-better metrics (input lag, dE), `-` for explicit descending. Defaults to
    release date. One field per call; blend two by calling twice.

    A field you filter or sort on **is fetched for you**. When it cannot be compared the
    predicate is **not applied** and the envelope says which of three: gated for this
    session, not on the bench(es) queried, or RTINGS published no value — otherwise
    "0 results" reads as "nothing qualifies" when the truth is "you cannot see it". Rows
    with no comparable value sort last both ways.

    Every value carries a `status`: `tested_visible`, `tested_gated` (RTINGS measured it and
    is withholding it — **not** the same as `not_tested`), `not_applicable`, `not_tested`,
    `review_unpublished` (review in progress), `coverage_unknown`.

    `tests` projects those measurements; `usages` the 0-10 scores, defaulting to the
    headline usages (`[]` for none, explicit ids for sub-usages). `bench` defaults to the
    recent-bench set; `data.groups` is one group per bench, never flattened — a bench is a
    methodology version. `limit` is 10 per group; page with `offset`.

    **Size.** Responses are trimmed to a character budget per call: when a window would
    exceed it, each group keeps the head of its ranking and the envelope carries a
    `response_truncated` warning with the offset to continue from. To fit more products per
    call, pass fewer `tests`/`usages` or `limit<=10`. Narrowing `bench` drops a product
    tested on another recent bench — the `product_ids` warning names its bench.

    **Shape.** Each product's `tests` rows carry only the answer (`original_id`, `status`,
    `value`, `gated`, `score`, `display`, `as_of`); `data.tests[original_id]` holds the
    definition once — name, kind, `unit` (of `value`), `display_unit`, hierarchy — plus
    `score_direction`, derived from RTINGS' own scores across every product this call
    matched, not only the rows served. `sort` on a test ranks by its value, never its score.
    A `number` test whose `unit` is `"score"` is itself a 0-10 rating (robot-vacuum "Water
    Left On Floor"), not a physical quantity. Each product's `variants` carries every size
    it is sold in with that sku's manufacturer model number.

    **Uncatalogued products.** Some products return measurements but are absent from
    RTINGS' product listing, so they have no name or brand here. They are summarised as a
    `coverage: "uncatalogued"` group carrying only `product_ids`; pass
    `include_uncatalogued=true` (or filter by their ids) to rank their values, and
    `rt_product(<id>, silo=...)` to identify one.
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


@tool()
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
    """Every test result for one product, grouped by section the way RTINGS' review is.

    `data.results` is a list of sections, each carrying its `group` breadcrumb once, a
    `group_id` you can pass straight back as `group=` to re-fetch that section alone, and
    its `tests`. `result_count` counts the RESULTS, not the sections.

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


@tool()
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


@tool()
async def rt_search(
    query: str, count: int = 10, silo: SiloParam | None = None
) -> SearchEnvelope:
    """Find a product, article or test page across every RTINGS category.

    Uses RTINGS' own live search index, so an empty result means the index has no match —
    it is not evidence that a product was never tested.

    Every hit comes back `kind: "page"` whatever it points at, so each carries `read_with`:
    the tool that takes its `url` (`rt_product`, `rt_article`, `rt_recommendations`), or
    null when nothing here reads that page.

    `silo` keeps only that category's hits. RTINGS' index is cross-silo and its query takes
    no category, so the filter is applied here, over the top hits scanned (`scanned`, with
    `silo_matches` for how many survived) — a model ranked below them will not appear;
    raise `count` to scan deeper.
    """
    return await _run(
        SearchEnvelope, services.rt_search(_ctx(), query, count=count, silo=silo)
    )


@tool()
async def rt_article(
    article: str,
    silo: SiloParam | None = None,
    section: str | None = None,
    include_body: bool = True,
    refresh: bool = False,
) -> ArticleEnvelope:
    """RTINGS' prose pages: brand lineups, explainers, test methodology, longevity results.

    Two branches, both fetchable here. `/{silo}/learn/{slug}` is lineups and explainers —
    "what is Sony launching this year", "what changed between mini-LED generations".
    `/{silo}/tests/{slug}` is how a test is run (`/tv/tests/picture-quality/contrast-ratio`)
    and the multi-year investigations no measurement row carries — the 100-TV accelerated
    longevity and burn-in test. **Burn-in and long-term panel wear live only here**: there
    is no burn-in test on the current bench, so this prose is the whole answer.

    Nothing on either branch is a test result and nothing is gated; cite it as RTINGS'
    writing.

    Find one with `rt_search(query, silo=...)` and pass the `url` it returns — its
    `read_with` says whether this is the tool. A bare slug with `silo=` means `/learn/`.

    `sections` lists the article's headings. A longevity page runs past 50,000 characters
    with a heading per dated update, so pass `section="March 16"` to read one instead of
    the whole page; `include_body=false` returns the outline alone.
    """
    return await _run(
        ArticleEnvelope,
        services.rt_article(
            _ctx(),
            article,
            silo=silo,
            section=section,
            include_body=include_body,
            refresh=refresh,
        ),
    )


@tool(name="rt_recommendations")
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


@tool(name="rt_sign_in")
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


@tool(name="rt_auth_status")
async def rt_auth_status(wait_s: int = 0) -> AuthStatusEnvelope:
    """What credential is stored, and how a sign-in in progress is going.

    Safe to call any time; it never opens a window and never spends anything. `wait_s`
    long-polls for the next change in a sign-in that is running (capped at 45 s, which is
    under Claude Desktop's 60 s tool-call limit) — that is how you wait for a human to finish
    signing in without blocking a single call for minutes.

    `session` is credential health, never entitlement to data: `member`, `free`, `anonymous`
    or `expired`. With a credential stored it runs the session probe (a category table page,
    never a review, so nothing is spent) when none is cached or the cached one has aged out;
    `unknown` only when that probe itself failed.
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
