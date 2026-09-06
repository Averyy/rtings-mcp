"""The row normalizer — the project's core safety property (SPEC §5, §7; RECON §6, §11).

**An agent must never read "no member session" as "RTINGS did not test this."** That is the
most dangerous failure mode in the project, and it is reached by several different routes,
so the normalizer emits **seven** states and never collapses two of them into one null:

``tested_visible`` / ``tested_gated`` / ``not_applicable`` / ``not_tested`` /
``review_unpublished`` / ``coverage_unknown`` / ``unknown_row_status``.

**Branch on ``status`` before ``unblurred``.** Measured necessary in both directions: an
``na`` row with ``unblurred:false`` is byte-identical to a gated row (so reading ``unblurred``
alone says "measured, buy a membership" about a test that never applied), *and* 1,025 of
2,186 ``na`` rows are ``unblurred:true`` (so reading ``unblurred`` alone makes *those*
``tested_visible`` with a null value — "measured, and the answer is nothing").
"""

from __future__ import annotations

import html as html_module
import math
import re
import time
from dataclasses import dataclass
from typing import Any

from .schema import CLOCK_UNITS, SiloSchema, TestDef

TESTED_VISIBLE = "tested_visible"
TESTED_GATED = "tested_gated"
NOT_APPLICABLE = "not_applicable"
NOT_TESTED = "not_tested"
#: An Early Access review: the data exists and is published for Insiders, but was withheld
#: from *this* session. **A membership does lift this** — RTINGS says so itself: "the writer
#: publishes it for Early Access so that Insiders who support us can see the data without any
#: text" (rtings.com/monitor/learn/how-we-test), and these products live under
#: `/early-access/...` URLs. An earlier draft called it "a blur no membership lifts", which
#: was inferred from anonymous data alone and is backwards.
REVIEW_UNPUBLISHED = "review_unpublished"
COVERAGE_UNKNOWN = "coverage_unknown"
UNKNOWN_ROW_STATUS = "unknown_row_status"

#: The full ``status`` enum an ``outputSchema`` must declare.
STATUS_VALUES = (
    TESTED_VISIBLE,
    TESTED_GATED,
    NOT_APPLICABLE,
    NOT_TESTED,
    REVIEW_UNPUBLISHED,
    COVERAGE_UNKNOWN,
    UNKNOWN_ROW_STATUS,
)

#: The observed API ``status`` domain. Treated as open: an unrecognised value is a drift
#: warning, never silently mapped to ``tested_visible``.
API_STATUS_TESTED = "tested"
API_STATUS_NA = "na"
API_STATUS_UNTESTED = "untested"

def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


_TAG_RE = re.compile(r"<[^>]+>")
#: RTINGS' spelling of an unbounded reading, as a whole value ("Inf") …
_INFINITE_RE = re.compile(r"^([+-]?)(?:inf|infinity|∞)$", re.IGNORECASE)
#: A clock-style display ("01:45", "1:02:03"), whose machine value is seconds.
_CLOCK_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})(?::(\d{2}))?\b")
#: … and at the head of a display string ("Inf : 1").
_INFINITE_PREFIX_RE = re.compile(r"^([+-]?)(?:inf|infinity|∞)(?![a-z])", re.IGNORECASE)

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_WS_RE = re.compile(r"\s+")


def strip_html(value: Any) -> str | None:
    """Flatten an HTML ``rendered_value`` to text. Never used to *recover* a gated value."""
    if value is None:
        return None
    text = html_module.unescape(_TAG_RE.sub(" ", str(value)))
    text = _WS_RE.sub(" ", text).strip()
    return text or None


@dataclass(slots=True)
class NormalizedValue:
    """One ``(product, test)`` answer, with the reason it has the value it has."""

    original_id: str
    name: str
    kind: str
    status: str
    value: Any = None
    raw_value: Any = None
    unit: str | None = None
    #: The unit RTINGS *displays* when it differs from ``unit`` (the unit of ``value``).
    display_unit: str | None = None
    precision: int | None = None
    score: float | None = None
    #: ``null``, not ``false``, whenever there is no value to gate. ``{value:null,
    #: gated:false}`` is indistinguishable from a *visible* value that happens to be null,
    #: which destroys the distinction in the pair an agent is most likely to read.
    gated: bool | None = None
    insider_only: bool = False
    product_id: str | None = None
    #: Where a ``not_tested`` answer's coverage came from, so its age is visible.
    as_of: float | None = None
    #: ``"value"`` (a clean machine value from the table path) or ``"rendered"`` (parsed
    #: from the review path's display string, so display-rounded).
    value_source: str | None = None
    #: ``"curve"`` when the answer is a series rather than a scalar (a ``graph`` test).
    value_kind: str | None = None
    display: str | None = None
    warning: str | None = None
    hierarchy: list[str] | None = None

    def to_json(self) -> dict[str, Any]:
        value = self.value
        infinite = isinstance(value, float) and math.isinf(value)
        out: dict[str, Any] = {
            "original_id": self.original_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            # JSON has no infinity. The wire says `null` + `is_infinite` rather than a
            # token some parsers reject and others read as null without comment.
            "value": None if infinite else value,
            "gated": self.gated,
            "insider_only": self.insider_only,
        }
        if infinite:
            out["is_infinite"] = True
            if value < 0:
                out["infinity_sign"] = -1
            out["warning"] = (
                self.warning
                or "RTINGS reports this reading as infinite (see `display`); it ranks "
                "above every finite value"
            )
        if self.as_of is not None:
            # ISO like every other timestamp in the envelope; an epoch float here made two
            # time formats sit side by side in one object.
            out["as_of"] = _iso(self.as_of)
        for key in (
            "raw_value",
            "unit",
            "display_unit",
            "precision",
            "score",
            "product_id",
            "value_source",
            "value_kind",
            "display",
            "warning",
            "hierarchy",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


def coerce_value(definition: TestDef, raw: Any) -> tuple[Any, str | None]:
    """Coerce by the **declared kind**, never by value shape.

    ``word`` tests hold ``"Yes"``/``"No"`` and numeric-looking strings alike, so a ``word``
    value is never coerced. A value that will not coerce keeps ``raw_value``, sets
    ``value`` to ``None``, and warns — never a guess.
    """
    if raw is None:
        return None, None
    if definition.kind == "number":
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw), None
        text = str(raw).strip()
        if not text:
            return None, None
        infinite = _INFINITE_RE.match(text)
        if infinite:
            # RTINGS reports an OLED's contrast as `value: "Inf"`, `rendered_value: "Inf :
            # 1"`. Python's float() accepts "Inf" quietly and the JSON layer then emitted
            # null — so the two best contrast readings on the bench looked like empty rows.
            # Carried as a real infinity so sorting and filtering rank it where it belongs;
            # `to_json` says so explicitly rather than serialising a non-standard token.
            return (-math.inf if infinite.group(1) == "-" else math.inf), None
        try:
            return float(text.replace(",", "")), None
        except ValueError:
            return None, (
                f"{definition.name!r} declared kind=number but value {text!r} is not numeric"
            )
    if definition.kind == "word":
        return str(raw), None
    return raw, None


def parse_rendered_number(definition: TestDef, rendered: Any) -> tuple[float | None, str | None]:
    """Recover a number from a review row's ``rendered_value``.

    The review path carries **no ``value`` key at all** — only ``rendered_value`` (a
    formatted string like ``"1950 cd/m²"``) and ``score``. This is the one place the
    "coerce from a raw value" rule cannot apply, so the number is read back out of RTINGS'
    own display string. It is therefore **display-rounded**: the table path is where a
    clean value exists, and every value recovered here is labelled ``value_source:
    "rendered"`` so precision loss is visible rather than implied.
    """
    text = strip_html(rendered)
    if not text:
        return None, None
    infinite = _INFINITE_PREFIX_RE.match(text)
    if infinite:
        # "Inf : 1" parsed as 1.0 — the worst possible contrast — for the two OLEDs a
        # dark-room shopper was comparing. The number regex must never see the unit text
        # of an infinite reading.
        return (-math.inf if infinite.group(1) == "-" else math.inf), None
    clock = _CLOCK_RE.match(text)
    if clock:
        # "01:45" parsed as 1.0 — the digits before the colon — where the table path
        # serves 105 (seconds). Same number on both paths now.
        parts = [int(clock.group(1)), int(clock.group(2))]
        if clock.group(3) is not None:
            parts.append(int(clock.group(3)))
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds, None
    match = _NUMBER_RE.search(text)
    if not match:
        return None, (
            f"{definition.name!r} declared kind=number but rendered value {text!r} "
            "carries no number"
        )
    try:
        return float(match.group(0).replace(",", "")), None
    except ValueError:  # pragma: no cover - the regex guarantees a parseable token
        return None, f"could not parse a number from {text!r}"


def _base(
    definition: TestDef, product_id: str | None, schema: SiloSchema | None
) -> NormalizedValue:
    numeric = definition.kind == "number"
    return NormalizedValue(
        original_id=definition.original_id,
        name=definition.name,
        kind=definition.kind,
        status=UNKNOWN_ROW_STATUS,
        # Unit and precision are meaningful only on a `number` test, and they are never
        # inferred — they ship in the definition or they are absent.
        # `unit` is the unit of `value`. RTINGS stores a converted test in its INPUT unit
        # ("centimeters") and displays another ("inches"): Height Adjustment shipped
        # `value: 10.7` labelled `inches` beside `display: 4.2" (10.7 cm)`. The display
        # unit is reported separately whenever it differs.
        unit=definition.value_unit if numeric else None,
        display_unit=(
            definition.number_display_unit
            if numeric and definition.number_display_unit != definition.value_unit
            else None
        ),
        precision=definition.value_precision if numeric else None,
        insider_only=definition.insider_only,
        product_id=product_id,
        hierarchy=schema.ancestry(definition.original_id) if schema else None,
    )


def normalize_absent(
    definition: TestDef,
    *,
    product_id: str | None,
    as_of: float | None,
    schema: SiloSchema | None = None,
    covered: bool = True,
) -> NormalizedValue:
    """Step 0/1: an absent row.

    Inside covered, fresh, non-stale scope that is a real answer — ``not_tested``, stamped
    with the slice's ``fetched_at``. Outside it, it is **not an answer at all**:
    ``coverage_unknown``, an honest "I don't know" distinct from both ``not_tested``
    ("RTINGS did not measure this") and ``tested_gated`` ("measured, you can't see it").
    """
    row = _base(definition, product_id, schema)
    if covered:
        row.status = NOT_TESTED
        row.as_of = as_of
    else:
        row.status = COVERAGE_UNKNOWN
        row.warning = (
            "this product is outside the coverage of the cached slice; refetch to answer"
        )
    return row


def normalize_table_row(
    raw: dict[str, Any],
    definition: TestDef,
    *,
    unpublished_product_ids: set[str] | None = None,
    schema: SiloSchema | None = None,
    as_of: float | None = None,
) -> NormalizedValue:
    """Normalize one ``table_tool__test_results`` row (carries a clean ``value``)."""
    product_id = _as_str(raw.get("product_id"))
    row = _base(definition, product_id, schema)
    status = raw.get("status")
    unblurred = bool(raw.get("unblurred"))
    row.as_of = as_of

    # Step 2 — an explicit `untested`. The table path emits BOTH encodings of not-tested
    # (an absent row *and* this), so mapping it to unknown_row_status would raise a false
    # drift alarm on an ordinary state and fail to report not_tested.
    if status == API_STATUS_UNTESTED:
        row.status = NOT_TESTED
        return row

    # Step 3 — not applicable, REGARDLESS of `unblurred` (true on 47% of them).
    if status == API_STATUS_NA:
        row.status = NOT_APPLICABLE
        return row

    if status != API_STATUS_TESTED:
        row.status = UNKNOWN_ROW_STATUS
        row.warning = f"unrecognised row status {status!r}"
        return row

    # Step 4b — measured, withheld. The reason differs, and both are checked before the
    # paywall is blamed:
    #   * an Early Access review is withheld from *this* session (a membership lifts it);
    #   * anything else gated is the per-silo paywall.
    # `unblurred` is checked FIRST so a member's real Early Access values are never
    # discarded: if the data came through, it is data, whatever the catalog flag says.
    if not unblurred:
        is_early_access = bool(
            unpublished_product_ids and product_id and product_id in unpublished_product_ids
        )
        row.status = REVIEW_UNPUBLISHED if is_early_access else TESTED_GATED
        row.gated = None if is_early_access else True
        return row

    # Step 4c — visible. The value may legitimately be null (2 measured rows), and when it
    # is, `gated` must be null rather than false.
    row.status = TESTED_VISIBLE
    if definition.has_graph:
        # Same reasoning as the review path: a `graph` test never has a scalar, so a null
        # value here is not "measured, and the answer is nothing" — the answer is a curve.
        # `rt_ratings` accepts any leaf id as a projection and `rt_schema` lists graph tests
        # beside real ones, so this is reachable without the caller doing anything odd.
        row.value_kind = "curve"
        row.warning = "this test is a measurement curve — fetch it with rt_graph"
        row.display = strip_html(raw.get("rendered_value"))
        row.gated = None
        return row
    raw_value = raw.get("value")
    row.raw_value = raw_value
    value, warning = coerce_value(definition, raw_value)
    if warning:
        row.warning = warning
        value = None
    row.value = value
    row.value_source = "value" if value is not None else None
    row.display = strip_html(raw.get("rendered_value"))
    # An unscored test still ships `score: 0.0`; passing it through reads as "0 out of 10"
    # for something RTINGS never scored (mattress "Mattress Type" = Foam, score 0.0).
    row.score = _as_float(raw.get("score")) if definition.has_score else None
    row.gated = None if value is None else False
    return row


def normalize_review_row(
    raw: dict[str, Any],
    definition: TestDef,
    *,
    product_id: str | None,
    unpublished: bool = False,
    schema: SiloSchema | None = None,
    as_of: float | None = None,
) -> NormalizedValue:
    """Normalize one ``product_vue_page__page_body`` row.

    Same seven states, different shape: this row has **no ``value`` key**, only
    ``rendered_value`` (HTML) plus ``score``. Never write one accessor that assumes both.
    """
    row = _base(definition, product_id, schema)
    status = raw.get("status")
    unblurred = bool(raw.get("unblurred"))
    row.as_of = as_of

    if status == API_STATUS_UNTESTED:
        row.status = NOT_TESTED
        return row
    if status == API_STATUS_NA:
        row.status = NOT_APPLICABLE
        return row
    if status != API_STATUS_TESTED:
        row.status = UNKNOWN_ROW_STATUS
        row.warning = f"unrecognised row status {status!r}"
        return row
    if not unblurred:
        # As on the table path: unblurred data wins over the catalog's `published` flag.
        row.status = REVIEW_UNPUBLISHED if unpublished else TESTED_GATED
        row.gated = None if unpublished else True
        return row

    row.status = TESTED_VISIBLE
    rendered = raw.get("rendered_value")
    row.display = strip_html(rendered)
    row.raw_value = row.display
    if definition.has_graph:
        # A `graph` test never has a scalar, so reporting `value: null` on a visible row
        # reads as "measured, and the answer is nothing". The answer is a curve.
        row.value_kind = "curve"
        row.warning = "this test is a measurement curve — fetch it with rt_graph"
        row.gated = None
        return row
    if definition.kind == "number":
        value, warning = parse_rendered_number(definition, rendered)
        row.value = value
        row.value_source = "rendered" if value is not None else None
        # A rendered number is read out of the DISPLAY string, so it is in the display
        # unit: "2.1 lbs (1.0 kg)" yields 2.1, pounds — not the kilograms the machine
        # value is stored in. The table path is where `value_unit` applies.
        row.unit = definition.number_display_unit
        row.precision = definition.number_display_precision
        row.display_unit = None
        if row.unit and row.unit.lower() in CLOCK_UNITS:
            # The parsed number is seconds; the clock string is how RTINGS shows it.
            row.unit, row.display_unit = "seconds", row.unit
        if warning:
            row.warning = warning
    elif definition.kind == "word":
        row.value = row.display
        row.value_source = "rendered" if row.display is not None else None
    else:
        row.value = row.display
        row.value_source = "rendered" if row.display is not None else None
    row.score = _as_float(raw.get("score")) if definition.has_score else None
    row.gated = None if row.value is None else False
    return row


@dataclass(slots=True)
class NormalizedRating:
    """One usage rating. ``table_tool__ratings`` rows carry **no ``status`` field**
    (verified 2026-09-03), so only presence and ``unblurred`` are available here — there is
    no ``na``/``untested`` to branch on first."""

    original_id: str
    name: str
    product_id: str | None
    status: str
    score: float | None = None
    gated: bool | None = None
    suitable: bool | None = None
    is_unscored: bool = False
    as_of: float | None = None

    def to_json(self) -> dict[str, Any]:
        out = {
            "original_id": self.original_id,
            "name": self.name,
            "product_id": self.product_id,
            "status": self.status,
            "score": self.score,
            "gated": self.gated,
        }
        for key in ("suitable", "is_unscored"):
            value = getattr(self, key)
            if value is not None and value is not False:
                out[key] = value
        if self.as_of is not None:
            out["as_of"] = _iso(self.as_of)
        return out


def normalize_rating_row(
    raw: dict[str, Any],
    *,
    name: str,
    is_unscored: bool = False,
    unpublished_product_ids: set[str] | None = None,
    as_of: float | None = None,
) -> NormalizedRating:
    product_id = _as_str(raw.get("product_id"))
    original_id = _as_str(raw.get("original_id")) or ""
    rating = NormalizedRating(
        original_id=original_id,
        name=name,
        product_id=product_id,
        status=TESTED_GATED,
        suitable=raw.get("suitable") if isinstance(raw.get("suitable"), bool) else None,
        is_unscored=is_unscored,
        as_of=as_of,
    )
    if not bool(raw.get("unblurred")):
        early_access = bool(
            unpublished_product_ids and product_id and product_id in unpublished_product_ids
        )
        rating.status = REVIEW_UNPUBLISHED if early_access else TESTED_GATED
        rating.gated = None if early_access else True
        return rating
    score = _as_float(raw.get("score"))
    rating.status = TESTED_VISIBLE
    rating.score = score
    rating.gated = None if score is None else False
    return rating


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
