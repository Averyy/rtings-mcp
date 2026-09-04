"""Declared output models — the ``outputSchema`` of every tool (SPEC §7).

These exist so the **seven-state ``status`` enum reaches the client's schema**, not just the
prose. An agent that reads the schema can see, before it ever makes a call, that a value may
come back ``tested_gated`` ("measured, you cannot see it") or ``coverage_unknown`` ("I do not
know") — and that neither is ``not_tested``.

Models are permissive (``extra="allow"``, optional fields) because the payloads track a
third-party API: a new key must widen the response, never fail the call.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class ScoresAvailableOut(Permissive):
    """Never a boolean. Derived from observed ``unblurred`` per (silo, bench).

    ``available`` every row of that surface came back unblurred; ``gated`` none did;
    ``partial`` some did (with a ratio field alongside); ``absent`` the category has no such
    surface; ``unknown`` this call did not query that surface, so nothing can be said.
    """

    public_tests: Availability | None = None
    insider_tests: Availability | None = None
    usage_ratings: Availability | None = None


class BenchOut(Permissive):
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


class ValueOut(Permissive):
    """One ``(product, test)`` answer.

    ``gated`` is ``null``, never ``false``, whenever there is no value to gate: the pair
    ``{value: null, gated: false}`` is indistinguishable from a visible value that happens
    to be null, which destroys the distinction an agent is most likely to read.
    """

    original_id: str
    name: str
    kind: str
    status: RowStatus
    value: Any = None
    gated: bool | None = None
    insider_only: bool = False
    raw_value: Any = None
    unit: str | None = None
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


class RatingOut(Permissive):
    original_id: str
    name: str
    product_id: str | None = None
    status: RowStatus
    score: float | None = None
    gated: bool | None = None
    suitable: bool | None = None
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


class SiloOut(Permissive):
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


class ProductRowOut(Permissive):
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
    variants: list[str] | None = None
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
    groups: list[BenchGroupOut] = Field(default_factory=list)
    notice: str | None = None


class RatingsEnvelope(BaseEnvelopeOut):
    data: RatingsData | None = None


class VerdictOut(Permissive):
    """RTINGS' written judgement for one usage, with its score when that is served."""

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


class HighlightOut(Permissive):
    sentiment: Literal["pro", "con"] | None = None
    text: str | None = None
    title: str | None = None


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
    results: list[ValueOut] = Field(default_factory=list)
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
    header: list[Any] = Field(default_factory=list)
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


class SearchHitOut(Permissive):
    kind: str | None = None
    title: str | None = None
    url: str | None = None
    product_id: str | None = None
    silo: str | None = None
    thumbnail: str | None = None
    excerpt: str | None = None


class SearchData(Permissive):
    query: str | None = None
    total_count: int | None = None
    results: list[SearchHitOut] = Field(default_factory=list)
    searched: str | None = None
    notice: str | None = None


class SearchEnvelope(BaseEnvelopeOut):
    data: SearchData | None = None


class RecommendationPickOut(Permissive):
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
