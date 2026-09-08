"""Declared output models — the ``outputSchema`` of every tool (SPEC §7).

These exist so the **seven-state ``status`` enum reaches the client's schema**, not just the
prose. An agent that reads the schema can see, before it ever makes a call, that a value may
come back ``tested_gated`` ("measured, you cannot see it") or ``coverage_unknown`` ("I do not
know") — and that neither is ``not_tested``.

Models are permissive (``extra="allow"``, optional fields) because the payloads track a
third-party API: a new key must widen the response, never fail the call.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

RowStatus = Literal[
    "tested_visible",
    "tested_gated",
    "not_applicable",
    "not_tested",
    "review_unpublished",
    "coverage_unknown",
    "unknown_row_status",
]

AuthState = Literal[
    "member", "free", "anonymous", "expired", "stale_member_data", "unproven_session"
]
SessionState = Literal["member", "free", "anonymous", "expired", "unknown"]
DataTier = Literal["unblurred", "unproven"]
Availability = Literal["available", "gated", "partial", "absent", "unknown"]
Completeness = Literal["full", "gated", "partial", "unknown"]


class Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


class LeanRow(Permissive):
    """A row model that omits its null optional fields on the wire.

    The services already build lean dicts — their ``to_json`` skips null optionals — but the
    output model re-adds every declared field as ``null`` when it serializes, and that is
    what an agent actually receives. Measured on one ``rt_product`` response: 55,857 bytes of
    content became **113,406** on the wire, and a single gated row went from 168 to 424
    bytes, 256 of them nulls carrying nothing.

    **``value`` and ``gated`` are never dropped, even when null.** That pair is the safety
    property: "a gated value is null, never absent". An agent must be able to read
    ``row["value"]`` and get ``None`` rather than a KeyError, and see ``gated: true`` next to
    it. ``status`` stays for the same reason — it is the field that says *why*.

    Subclasses extend the set when a different field carries that role: a usage rating has
    **only** a score, so on :class:`RatingOut` and :class:`VerdictOut` ``score`` is what
    ``value`` is here, and dropping it left an agent testing ``"score" in row`` with an
    answer that depended on whether the row happened to be gated. It stays droppable on
    :class:`ValueOut`, where a score is secondary to the value and null on most rows.

    Envelope models deliberately do NOT inherit this: `error: null` means "no error" and
    `scores_available: null` means "not applicable here", and a caller checking those with
    `out["error"] is None` must not get a KeyError instead.
    """

    #: Present on the wire even when null.
    ALWAYS_PRESENT: ClassVar[frozenset[str]] = frozenset({"value", "gated", "status"})

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler: Any) -> dict[str, Any]:
        dumped = handler(self)
        if not isinstance(dumped, dict):  # pragma: no cover - defensive
            return dumped
        return {
            key: value
            for key, value in dumped.items()
            if value is not None or key in self.ALWAYS_PRESENT
        }


class ScoresAvailableOut(Permissive):
    """Never a boolean. Derived from observed ``unblurred`` per (silo, bench).

    ``available`` every row of that surface came back unblurred; ``gated`` none did;
    ``partial`` some did (with a ratio field alongside); ``absent`` the category has no such
    surface; ``unknown`` this call did not query that surface, so nothing can be said.
    Always the same three-key shape, on every tool.
    """

    public_tests: Availability | None = None
    insider_tests: Availability | None = None
    usage_ratings: Availability | None = None


class BenchOut(LeanRow):
    id: str | None = None
    display_name: str | None = None


class SortedByOut(Permissive):
    """What the ordering actually used — never anonymous-looking but secretly arbitrary."""

    field: str | None = None
    name: str | None = None
    gated: bool | None = Field(
        default=None, description="Whether the field ACTUALLY used is gated."
    )
    direction: str | None = None
    requested: str | None = Field(
        default=None, description="Set when the requested field could not be used."
    )
    fallback_reason: str | None = None


class ErrorOut(Permissive):
    code: str
    message: str
    retryable: bool = False
    retry_after: float | None = None
    details: dict[str, Any] | None = None


class ValueOut(LeanRow):
    """One ``(product, test)`` answer.

    ``gated`` is ``null``, never ``false``, whenever there is no value to gate: the pair
    ``{value: null, gated: false}`` is indistinguishable from a visible value that happens
    to be null, which destroys the distinction an agent is most likely to read.
    """

    original_id: str
    #: Absent on rt_ratings rows, where `data.tests[original_id]` carries the definition.
    name: str | None = None
    kind: str | None = None
    status: RowStatus
    value: Any = None
    gated: bool | None = None
    insider_only: bool | None = None
    raw_value: Any = None
    unit: str | None = Field(
        default=None, description="The unit of `value`. RTINGS may display another unit."
    )
    display_unit: str | None = Field(
        default=None,
        description="The unit RTINGS shows in `display` when it differs from `unit`.",
    )
    is_infinite: bool | None = Field(
        default=None,
        description=(
            "True when RTINGS reports the reading as infinite (an OLED's contrast). `value` "
            "is null because JSON has no infinity; it ranks above every finite value."
        ),
    )
    infinity_sign: int | None = None
    precision: int | None = None
    score: float | None = None
    product_id: str | None = None
    as_of: str | None = Field(
        default=None,
        description="When the slice this answer came from was fetched (ISO 8601, UTC).",
    )
    value_kind: Literal["curve"] | None = Field(
        default=None,
        description=(
            "'curve' means the answer is a measurement series, not a scalar — fetch it with "
            "rt_graph. A null `value` here is not an empty measurement."
        ),
    )
    value_source: Literal["value", "rendered"] | None = Field(
        default=None,
        description=(
            "'value' is RTINGS' machine value; 'rendered' was parsed back out of a display "
            "string and is therefore display-rounded."
        ),
    )
    display: str | None = None
    warning: str | None = None
    hierarchy: list[str] | None = None
    superseded_at: str | None = None
    description: str | None = None
    media: dict[str, Any] | None = None
    has_graph: bool | None = None


class RatingOut(LeanRow):
    # A usage rating has only a score, so `score` here is what `value` is on `ValueOut`:
    # "a gated score is null, never absent".
    ALWAYS_PRESENT: ClassVar[frozenset[str]] = LeanRow.ALWAYS_PRESENT | {"score"}

    original_id: str
    name: str
    product_id: str | None = None
    status: RowStatus
    score: float | None = None
    gated: bool | None = None
    suitable: bool | None = Field(
        default=None,
        description=(
            "RTINGS' own recommendation for this use: `false` is them saying this product "
            "is NOT suitable for it, which is a real answer and not the same as the field "
            "being absent (they said nothing)."
        ),
    )
    is_unscored: bool | None = None
    as_of: str | None = None


class BaseEnvelopeOut(Permissive):
    """The shared envelope. ``auth_state`` is derived; every rule keys on ``session`` or
    ``data_tier`` directly, never on the summary."""

    auth_state: AuthState
    data_tier: DataTier
    session: SessionState
    scores_available: ScoresAvailableOut | None = None
    rank_scope: Literal["within_bench"] = "within_bench"
    test_benches: list[BenchOut] = Field(default_factory=list)
    sorted_by: SortedByOut | None = None
    fetched_at: str | None = None
    from_cache: bool = False
    stale: bool = Field(
        default=False, description="TRUE means past-TTL and nothing else."
    )
    previews_remaining: int | None = None
    source_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ErrorOut | None = None


# -- per-tool payloads --------------------------------------------------------------


class SiloOut(LeanRow):
    silo: str
    name: str | None = None
    silo_group: str | None = None
    review_count: int | None = None
    reviews_in_progress_count: int | None = None
    first_published_at: str | None = None
    has_paywall: bool | None = Field(
        default=None,
        description="True on all 28 silos; it carries no information. Use data_completeness.",
    )
    current_bench: BenchOut | None = None
    data_completeness: Completeness = "unknown"
    tool_pages: list[str | None] = Field(default_factory=list)
    observed: dict[str, Any] | None = None


class SilosData(Permissive):
    silos: list[SiloOut] = Field(default_factory=list)
    notice: str | None = None


class SilosEnvelope(BaseEnvelopeOut):
    data: SilosData | None = None


class SchemaData(Permissive):
    silo: str | None = None
    bench: BenchOut | None = None
    test_count: int | None = None
    leaf_value_test_count: int | None = None
    graph_test_count: int | None = None
    insider_only_test_count: int | None = None
    groups: list[dict[str, Any]] | None = None
    usages: list[dict[str, Any]] | None = None
    group: dict[str, Any] | None = None
    tests: list[dict[str, Any]] | None = None
    structure_rows: list[dict[str, Any]] | None = None
    notice: str | None = None


class SchemaEnvelope(BaseEnvelopeOut):
    data: SchemaData | None = None


class VariantOut(LeanRow):
    """One sku of a product family: the variation, and the model number a retailer knows.

    RTINGS reviews one sku and lists the rest; the sku's ``name`` is the manufacturer's
    model number (``"XR-83A80L"``), which is what a shopper searches by. ``model`` is
    omitted where it only repeats the product's own name.
    """

    variation: str | None = None
    model: str | None = None


class ProductRowOut(LeanRow):
    product_id: str
    name: str | None = None
    brand: str | None = None
    url: str | None = None
    released_at: str | None = None
    published: bool | None = Field(
        default=None,
        description=(
            "False means an Early Access review: the data exists and is published for "
            "Insiders, but was withheld from this session. A membership lifts it."
        ),
    )
    tested_variant: str | None = Field(
        default=None,
        description=(
            "The variant RTINGS tested (e.g. '65\"'). Results describe THIS one; other "
            "sizes in the family often differ. Filter on it with `variant`."
        ),
    )
    variants: list[VariantOut] | None = Field(
        default=None,
        description=(
            "Every variation the family is SOLD in, each with its manufacturer model "
            "number. Filter on it with `sold_in` (`{'sold_in': '>=83'}`)."
        ),
    )
    test_bench: BenchOut | None = None
    image: str | None = None
    usage_scores: list[RatingOut] = Field(default_factory=list)
    tests: list[ValueOut] | None = None


class BenchGroupOut(Permissive):
    """One comparable bench population. ``limit``/``offset`` apply per group."""

    test_benches: list[BenchOut] = Field(default_factory=list)
    is_recent_set: bool | None = None
    matched: int | None = Field(
        default=None, description="Products in this group that matched, before the window."
    )
    products: list[ProductRowOut] = Field(default_factory=list)
    coverage: Literal["unavailable", "uncatalogued"] | None = Field(
        default=None,
        description=(
            "'unavailable': a bench's catalog could not be fetched, so this group is "
            "incomplete and a short list does NOT mean nothing matched. 'uncatalogued': "
            "these products returned real measurements but appear in no RTINGS catalog "
            "listing, so their name, brand and bench are unknown — the values are real."
        ),
    )
    unavailable_benches: list[str] | None = None
    notice: str | None = None


class RatingsData(Permissive):
    silo: str | None = None
    total_matched: int | None = None
    returned: int | None = None
    offset: int | None = None
    tests: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Definition of every projected test, keyed by original_id: name, kind, unit "
            "(of `value`), display_unit, precision, insider_only, hierarchy, and "
            "`score_direction` (derived from RTINGS' own scores across every product this call "
            "matched, not only the rows served: "
            "higher_is_better / lower_is_better / mixed). Rows carry only the answer."
        ),
    )
    usages: dict[str, dict[str, Any]] | None = Field(
        default=None, description="Definition of every projected usage, keyed by original_id."
    )
    groups: list[BenchGroupOut] = Field(default_factory=list)
    notice: str | None = None


class RatingsEnvelope(BaseEnvelopeOut):
    data: RatingsData | None = None


class VerdictOut(LeanRow):
    """RTINGS' written judgement for one usage, with its score when that is served."""

    #: Same reasoning as :class:`RatingOut` — a verdict's score is its measurement.
    ALWAYS_PRESENT: ClassVar[frozenset[str]] = LeanRow.ALWAYS_PRESENT | {"score"}

    original_id: str | None = None
    name: str | None = None
    kind: str | None = None
    status: RowStatus | None = None
    score: float | None = None
    gated: bool | None = None
    suitable: bool | None = None
    verdict: str | None = Field(
        default=None,
        description=(
            "RTINGS' prose for this usage. Served even where the numbers are withheld, so "
            "it is the substantive answer on a gated category."
        ),
    )


class HighlightOut(LeanRow):
    sentiment: Literal["pro", "con"] | None = None
    text: str | None = None
    title: str | None = None


class ResultGroupOut(LeanRow):
    """One section of a review, carrying its breadcrumb ONCE.

    The rows used to come back flat with `hierarchy` repeated on every one — on a TV, 243
    rows x a 2-3 element breadcrumb, about 23% of a 56 KB response saying the same thing
    over and over. RTINGS' own review page is sectioned; this is the shape the data had.
    """

    group: list[str] | None = Field(
        default=None, description="The breadcrumb these tests sit under, deepest last."
    )
    group_id: str | None = Field(
        default=None,
        description="Pass back as rt_product's `group=` to fetch this section alone.",
    )
    test_count: int | None = None
    tests: list[ValueOut] = Field(default_factory=list)


class ProductData(Permissive):
    product: dict[str, Any] | None = None
    verdicts: list[VerdictOut] | None = Field(
        default=None, description="Per-usage verdicts. Requires include_verdicts=true."
    )
    highlights: list[HighlightOut] | None = Field(
        default=None, description="RTINGS' pros and cons. Requires include_verdicts=true."
    )
    scoring: list[dict[str, Any]] | None = Field(
        default=None,
        description="How each usage score is composed, with component weights.",
    )
    verdicts_notice: str | None = Field(
        default=None,
        description=(
            "About the verdicts specifically. Separate from `notice`, which explains why the "
            "measurements are null and must not be overwritten."
        ),
    )
    results: list[ResultGroupOut] = Field(
        default_factory=list,
        description=(
            "Leaf results, grouped by the section they sit under. `result_count` counts the "
            "RESULTS, not the groups."
        ),
    )
    result_count: int | None = None
    commentary: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "RTINGS' per-section prose. It hangs on group/category rows, which are structure "
            "and never carry a value or a status — hence a separate list, not a result."
        ),
    )
    summary: dict[str, Any] | None = None
    value_source_notice: str | None = None
    notice: str | None = None


class ProductEnvelope(BaseEnvelopeOut):
    data: ProductData | None = None


class GraphData(Permissive):
    product: dict[str, Any] | None = None
    test: dict[str, Any] | None = None
    header: list[Any] = Field(
        default_factory=list,
        description=(
            "One label per column of each point: the x axis first, then each series. "
            "Empty only when RTINGS shipped no labels."
        ),
    )
    axes: dict[str, Any] | None = Field(
        default=None,
        description="Axis titles (which carry the unit) and scales as RTINGS declared them.",
    )
    n_points: int | None = None
    n_points_shipped: int | None = None
    resampled: bool | None = None
    resampling: str | None = None
    axis_bounds_of_served_points: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Bounds of the points served, deliberately NOT a measurement of the product: a "
            "curve maximum would be the gated scalar in all but name."
        ),
    )
    points: list[Any] = Field(default_factory=list)


class GraphEnvelope(BaseEnvelopeOut):
    data: GraphData | None = None


class SearchHitOut(LeanRow):
    kind: str | None = None
    title: str | None = None
    url: str | None = None
    product_id: str | None = None
    silo: str | None = None
    thumbnail: str | None = None
    excerpt: str | None = None


class SearchData(Permissive):
    query: str | None = None
    silo: str | None = None
    total_count: int | None = Field(
        default=None, description="RTINGS' match count for the WHOLE index, unfiltered."
    )
    scanned: int | None = Field(
        default=None,
        description=(
            "How many hits the `silo` filter was applied to. RTINGS' query takes no "
            "category, so a product ranked below these is not in `results`."
        ),
    )
    silo_matches: int | None = Field(
        default=None, description="How many of the scanned hits were in `silo`."
    )
    results: list[SearchHitOut] = Field(default_factory=list)
    searched: str | None = None
    notice: str | None = None


class SearchEnvelope(BaseEnvelopeOut):
    data: SearchData | None = None


class ArticleData(Permissive):
    """RTINGS' editorial prose. Never a measurement, and never gated."""

    silo: str | None = None
    article: str | None = None
    title: str | None = None
    url: str | None = None
    updated_at: str | None = None
    authors: list[str] | None = None
    sections: list[str] = Field(
        default_factory=list,
        description="The article's headings. Pass one as `section` to read just it.",
    )
    section: str | None = None
    introduction: str | None = None
    body: str | None = None
    notice: str | None = None


class ArticleEnvelope(BaseEnvelopeOut):
    data: ArticleData | None = None


class RecommendationPickOut(LeanRow):
    rank: int | None = None
    title: str | None = None
    subtitle: str | None = None
    reasoning: str | None = None
    product_id: str | None = None
    name: str | None = None
    url: str | None = None
    overall_score: float | None = None
    variants: str | None = None
    featured_results: list[dict[str, Any]] = Field(default_factory=list)
    usage_scores: list[dict[str, Any]] = Field(default_factory=list)


#: Aliases resolved at module scope. `RecommendationsData` carries a field literally named
#: `list` (the tool's own parameter name), which shadows the builtin inside the class body
#: and makes every `list[...]` annotation there unevaluatable.
_DictList = list[dict[str, Any]]
_PickList = list[RecommendationPickOut]


class RecommendationsData(Permissive):
    silo: str | None = None
    lists: _DictList | None = None
    list: str | None = None
    title: str | None = None
    url: str | None = None
    updated_at: str | None = None
    introduction: str | None = None
    picks: _PickList | None = None
    notice: str | None = None


class RecommendationsEnvelope(BaseEnvelopeOut):
    data: RecommendationsData | None = None


# -- the sign-in tools --------------------------------------------------------------
#
# These carry their OWN lean envelope rather than `BaseEnvelopeOut`. That envelope's
# `data_tier` / `scores_available` / `test_benches` describe served measurement rows, and a
# sign-in serves none: filling them in would be inventing a claim about data nobody fetched.
# `session` is the one shared field that means the same thing here.


class SignInData(Permissive):
    #: `waiting` (a window is open) / `verifying` (checking a stored cookie first) /
    #: `in_progress` / `active` (stored) / `refused` / `failed`.
    status: str
    reason: str | None = None
    instructions: str
    browser: str | None = None
    expires_in_s: int | None = None
    session: SessionState = "unknown"


class SignInEnvelope(Permissive):
    session: SessionState = "unknown"
    warnings: list[str] = Field(default_factory=list)
    error: ErrorOut | None = None
    data: SignInData | None = None


class AuthStatusData(Permissive):
    session: SessionState = "unknown"
    stored: bool = False
    #: `file` (stored by a sign-in or `rtings-mcp auth`) or `env` (RTINGS_SESSION_COOKIE).
    source: str | None = None
    stored_at: str | None = None
    sign_in: str = "idle"
    reason: str | None = None
    browser: str | None = None
    previews_remaining: int | None = None
    cookie_name: str | None = None


class AuthStatusEnvelope(Permissive):
    session: SessionState = "unknown"
    warnings: list[str] = Field(default_factory=list)
    error: ErrorOut | None = None
    data: AuthStatusData | None = None
