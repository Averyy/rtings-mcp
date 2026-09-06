"""``column_options`` parsing — test/usage definitions and bench membership (RECON §6, §11.5).

The one trap worth stating at the top: **``test_benches[].tests[]`` is a REFERENCE list.**
Each entry is ``{"original_id": "..."}`` and nothing else. Definitions live in
``silo.test_bench.tests[]`` (the current bench) and ``silo.legacy_tests[]``. Bench-to-test
*membership* comes from ``test_benches[]``; test *definitions* come from the other two.
Joining the wrong one yields an empty schema **silently**.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from . import errors
from .errors import RtingsError

#: Kinds that carry a measurable leaf value. ``group``/``category`` are **structure, not
#: results** — they never get a ``status`` or a value; a ``group`` marked ``not_tested`` is a
#: category header reported as a missing measurement.
LEAF_VALUE_KINDS = frozenset({"number", "word"})
STRUCTURE_KINDS = frozenset({"group", "category"})
MEDIA_KINDS = frozenset({"picture", "video", "dropdown_images", "audio", "3d_model", "download"})
GRAPH_KIND = "graph"


#: RTINGS' clock-style units: the display is "mm:ss", the stored value is seconds.
CLOCK_UNITS = frozenset({"mm:ss", "hh:mm:ss", "h:mm:ss", "m:ss"})


@dataclass(slots=True, frozen=True)
class TestDef:
    original_id: str
    name: str
    kind: str
    has_score: bool
    insider_only: bool
    parent_original_id: str | None
    order: int
    published: bool
    number_display_unit: str | None
    number_display_precision: int | None
    number_prefix: str | None
    words: tuple[str, ...]
    #: The unit of the machine `value`, when RTINGS converts for display (measured
    #: 2026-09-06 on monitor: input "centimeters", display "inches"; input "kilograms",
    #: display "pounds"). `None` means the value is already in the display unit.
    number_input_unit: str | None = None
    number_input_precision: int | None = None
    #: The category a top-level ``group`` belongs to, recovered from **list position**.
    #:
    #: Measured 2026-09-04: every one of TV's 69 ``category``/``group`` rows carries
    #: ``parent_original_id: null``, so the API states no category->group link at all. The
    #: list order does: a ``category`` row is followed by the groups beneath it, each
    #: followed by its own leaves. Ignoring that yields a flat scramble of ~70 groups plus 12
    #: categories reporting ``leaves=0, children=0`` — which is what ``rt_schema`` used to
    #: return, and it makes the discovery tool nearly useless for finding a test.
    #:
    #: Kept in a separate field so the API's own (null) ``parent_original_id`` is never
    #: overwritten: this is derived, and it is labelled as derived.
    derived_category_id: str | None = None

    @property
    def is_leaf_value(self) -> bool:
        return self.kind in LEAF_VALUE_KINDS

    @property
    def value_unit(self) -> str | None:
        """The unit of the machine ``value``: the input unit when RTINGS converts.

        A clock unit ("mm:ss") describes the DISPLAY; the machine value behind "01:45" is
        105, seconds, so that is what the value is labelled.
        """
        unit = self.number_input_unit or self.number_display_unit
        return "seconds" if unit and unit.lower() in CLOCK_UNITS else unit

    @property
    def value_precision(self) -> int | None:
        if self.number_input_unit and self.number_input_precision is not None:
            return self.number_input_precision
        return self.number_display_precision

    @property
    def is_structure(self) -> bool:
        return self.kind in STRUCTURE_KINDS

    @property
    def has_graph(self) -> bool:
        return self.kind == GRAPH_KIND


@dataclass(slots=True, frozen=True)
class UsageDef:
    original_id: str
    name: str
    kind: str
    order: int
    published: bool
    is_sub_usage: bool
    is_unscored: bool
    parent_usage_name: str | None


@dataclass(slots=True, frozen=True)
class BenchDef:
    id: str
    display_name: str | None
    major: bool
    test_ids: tuple[str, ...]
    usage_ids: tuple[str, ...]


@dataclass(slots=True)
class SiloSchema:
    """One silo's full schema, joined and indexed by ``original_id``.

    ``original_id`` is the stable key; ``name`` is **not** — six different TV tests are all
    named "Peak 100% Window" across sub-groups. And ``id`` != ``original_id``: the per-row
    ``test:{id}`` stub is a primary key, not the schema key.
    """

    silo: str
    silo_id: str | None
    tested_products_count: int | None
    tests: dict[str, TestDef]
    usages: dict[str, UsageDef]
    benches: dict[str, BenchDef]
    current_bench_id: str | None
    bench_order: list[str] = field(default_factory=list)

    def test(self, original_id: str) -> TestDef | None:
        return self.tests.get(str(original_id))

    def usage(self, original_id: str) -> UsageDef | None:
        return self.usages.get(str(original_id))

    def bench(self, bench_id: str) -> BenchDef | None:
        return self.benches.get(str(bench_id))

    def tests_for_bench(self, bench_id: str) -> list[TestDef]:
        bench = self.bench(bench_id)
        if bench is None:
            return []
        return [self.tests[t] for t in bench.test_ids if t in self.tests]

    def usages_for_bench(self, bench_id: str) -> list[UsageDef]:
        bench = self.bench(bench_id)
        if bench is None:
            return []
        return [self.usages[u] for u in bench.usage_ids if u in self.usages]

    def leaf_tests_for_bench(self, bench_id: str) -> list[TestDef]:
        return [t for t in self.tests_for_bench(bench_id) if t.is_leaf_value]

    def parent_of(self, test: TestDef) -> str | None:
        """The stated parent, or the positional category when the API states none."""
        return test.parent_original_id or test.derived_category_id

    def ancestry(self, original_id: str) -> list[str]:
        """Group/category names from the outermost inward, for hierarchical grouping.

        With the positional category recovered this is two levels for a leaf under a
        grouped category (``["Brightness", "HDR Brightness"]``), which is what the site
        itself shows.
        """
        chain: list[str] = []
        seen: set[str] = set()
        current = self.test(original_id)
        while current is not None:
            parent_id = self.parent_of(current)
            if not parent_id or parent_id in seen:
                break  # defensive: a cycle in the hierarchy must not hang a tool call
            seen.add(parent_id)
            parent = self.test(parent_id)
            if parent is None:
                break
            chain.append(parent.name)
            current = parent
        chain.reverse()
        return chain


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_test(raw: dict[str, Any]) -> TestDef | None:
    original_id = _as_opt_str(raw.get("original_id"))
    if original_id is None:
        return None
    # The API ships `words` as `[{"value": "Yes"}, ...]`; the cacheable form flattens it to
    # `["Yes", ...]`. Both are accepted so a round trip through the cache does not silently
    # drop a `word` test's value domain.
    words_raw = raw.get("words")
    words: tuple[str, ...] = ()
    if isinstance(words_raw, list):
        collected: list[str] = []
        for entry in words_raw:
            if isinstance(entry, dict):
                if entry.get("value"):
                    collected.append(str(entry["value"]))
            elif entry is not None and str(entry):
                collected.append(str(entry))
        words = tuple(collected)
    return TestDef(
        original_id=original_id,
        # One keyboard-switch test ships as "Keystroke Data Used For Smoothness\t".
        name=str(raw.get("name") or "").strip(),
        kind=str(raw.get("kind") or ""),
        has_score=_as_bool(raw.get("has_score")),
        # Absent means "not gate-able", which is the safe reading: the flag marks a test
        # gate-*able*, and only an observed `unblurred` says whether it is gated here.
        insider_only=_as_bool(raw.get("insider_only")),
        parent_original_id=_as_opt_str(raw.get("parent_original_id")),
        order=_as_int(raw.get("order")),
        published=_as_bool(raw.get("published"), default=True),
        number_display_unit=_as_opt_str(raw.get("number_display_unit"))
        or _as_opt_str(raw.get("number_custom_unit")),
        number_display_precision=_as_opt_int(raw.get("number_display_precision")),
        number_prefix=_as_opt_str(raw.get("number_prefix")),
        words=words,
        derived_category_id=_as_opt_str(raw.get("derived_category_id")),
        number_input_unit=_as_opt_str(raw.get("number_input_unit")),
        number_input_precision=_as_opt_int(raw.get("number_input_precision")),
    )


def _parse_usage(raw: dict[str, Any]) -> UsageDef | None:
    original_id = _as_opt_str(raw.get("original_id"))
    if original_id is None:
        return None
    return UsageDef(
        original_id=original_id,
        name=str(raw.get("name") or ""),
        kind=str(raw.get("kind") or "usage"),
        order=_as_int(raw.get("order")),
        published=_as_bool(raw.get("published"), default=True),
        is_sub_usage=_as_bool(raw.get("is_sub_usage")),
        is_unscored=_as_bool(raw.get("is_unscored")),
        parent_usage_name=_as_opt_str(raw.get("parent_usage_name")),
    )


def _reference_ids(entries: Any) -> tuple[str, ...]:
    if not isinstance(entries, list):
        return ()
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            value = _as_opt_str(entry.get("original_id"))
            if value:
                out.append(value)
        elif entry is not None:
            out.append(str(entry))
    return tuple(out)


def parse_column_options(silo: str, payload: dict[str, Any]) -> SiloSchema:
    """Build a :class:`SiloSchema` from a ``data.silo`` body."""
    if not isinstance(payload, dict):
        raise RtingsError(errors.PAYLOAD_MISSING, "column_options: silo is not an object")

    current = payload.get("test_bench")
    current = current if isinstance(current, dict) else {}

    tests: dict[str, TestDef] = {}
    usages: dict[str, UsageDef] = {}

    # Definitions: current bench first, then legacy. Later writes do not clobber earlier
    # ones, so the current bench's definition wins where an original_id appears in both.
    #
    # Each source list is walked IN ORDER, because that order is the only statement of which
    # category a group belongs to (see TestDef.derived_category_id).
    for source in (current.get("tests"), payload.get("legacy_tests")):
        if not isinstance(source, list):
            continue
        last_category: str | None = None
        for raw in source:
            if not isinstance(raw, dict):
                continue
            parsed = _parse_test(raw)
            if parsed is None:
                continue
            if parsed.kind == "category":
                last_category = parsed.original_id
            elif (
                parsed.kind == "group"
                and parsed.parent_original_id is None
                and last_category is not None
            ):
                parsed = replace(parsed, derived_category_id=last_category)
            if parsed.original_id not in tests:
                tests[parsed.original_id] = parsed

    for source in (current.get("usages"), payload.get("legacy_usages")):
        if not isinstance(source, list):
            continue
        for raw in source:
            if not isinstance(raw, dict):
                continue
            parsed_usage = _parse_usage(raw)
            if parsed_usage and parsed_usage.original_id not in usages:
                usages[parsed_usage.original_id] = parsed_usage

    if not tests:
        raise RtingsError(
            errors.PAYLOAD_MISSING,
            f"column_options for {silo!r} yielded no test definitions",
        )

    # Membership: from test_benches[], which is a reference list only.
    benches: dict[str, BenchDef] = {}
    order: list[str] = []
    raw_benches = payload.get("test_benches")
    if isinstance(raw_benches, list):
        for raw in raw_benches:
            if not isinstance(raw, dict):
                continue
            bench_id = _as_opt_str(raw.get("id"))
            if bench_id is None:
                continue
            benches[bench_id] = BenchDef(
                id=bench_id,
                display_name=_as_opt_str(raw.get("display_name")),
                major=_as_bool(raw.get("major")),
                test_ids=_reference_ids(raw.get("tests")),
                usage_ids=_reference_ids(raw.get("usages")),
            )
            order.append(bench_id)

    return SiloSchema(
        silo=silo,
        silo_id=_as_opt_str(payload.get("id")),
        tested_products_count=_as_opt_int(payload.get("tested_products_count")),
        tests=tests,
        usages=usages,
        benches=benches,
        current_bench_id=order[0] if order else None,
        bench_order=order,
    )


#: Version 2 (2026-09-06) added `number_input_unit`/`number_input_precision`: without them
#: a converted test's value is labelled with the DISPLAY unit (10.7 "inches" for 10.7 cm).
CACHEABLE_VERSION = 2


def cacheable_is_current(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("cacheable_version") == CACHEABLE_VERSION


def schema_to_cacheable(schema: SiloSchema) -> dict[str, Any]:
    """A compact serialization of the parsed schema. Storing the parse (not the 357 KB raw
    body) keeps ``rt_schema`` cheap; the raw body is not needed again once parsed."""
    return {
        # Bumped when the cacheable form gains a field the normalizer depends on: a cached
        # copy without it must be refetched, not served for the rest of its 30-day TTL.
        "cacheable_version": CACHEABLE_VERSION,
        "silo": schema.silo,
        "silo_id": schema.silo_id,
        "tested_products_count": schema.tested_products_count,
        "current_bench_id": schema.current_bench_id,
        "bench_order": schema.bench_order,
        "tests": [
            {
                "original_id": t.original_id,
                "name": t.name,
                "kind": t.kind,
                "has_score": t.has_score,
                "insider_only": t.insider_only,
                "parent_original_id": t.parent_original_id,
                "order": t.order,
                "published": t.published,
                "number_display_unit": t.number_display_unit,
                "number_display_precision": t.number_display_precision,
                "number_prefix": t.number_prefix,
                "number_input_unit": t.number_input_unit,
                "number_input_precision": t.number_input_precision,
                "words": list(t.words),
                "derived_category_id": t.derived_category_id,
            }
            for t in schema.tests.values()
        ],
        "usages": [
            {
                "original_id": u.original_id,
                "name": u.name,
                "kind": u.kind,
                "order": u.order,
                "published": u.published,
                "is_sub_usage": u.is_sub_usage,
                "is_unscored": u.is_unscored,
                "parent_usage_name": u.parent_usage_name,
            }
            for u in schema.usages.values()
        ],
        "benches": [
            {
                "id": b.id,
                "display_name": b.display_name,
                "major": b.major,
                "tests": list(b.test_ids),
                "usages": list(b.usage_ids),
            }
            for b in schema.benches.values()
        ],
    }


def schema_from_cacheable(raw: dict[str, Any]) -> SiloSchema:
    tests = {}
    for item in raw.get("tests", []):
        parsed = _parse_test(item)
        if parsed:
            tests[parsed.original_id] = parsed
    usages = {}
    for item in raw.get("usages", []):
        parsed_usage = _parse_usage(item)
        if parsed_usage:
            usages[parsed_usage.original_id] = parsed_usage
    benches = {}
    order: list[str] = []
    for item in raw.get("benches", []):
        bench_id = _as_opt_str(item.get("id"))
        if bench_id is None:
            continue
        benches[bench_id] = BenchDef(
            id=bench_id,
            display_name=_as_opt_str(item.get("display_name")),
            major=_as_bool(item.get("major")),
            test_ids=tuple(str(t) for t in item.get("tests", [])),
            usage_ids=tuple(str(u) for u in item.get("usages", [])),
        )
        order.append(bench_id)
    return SiloSchema(
        silo=str(raw.get("silo") or ""),
        silo_id=_as_opt_str(raw.get("silo_id")),
        tested_products_count=_as_opt_int(raw.get("tested_products_count")),
        tests=tests,
        usages=usages,
        benches=benches,
        current_bench_id=_as_opt_str(raw.get("current_bench_id")),
        bench_order=list(raw.get("bench_order") or order),
    )
