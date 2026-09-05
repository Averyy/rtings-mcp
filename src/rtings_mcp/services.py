"""The seven tool bodies (SPEC §7).

Designed around questions, not endpoints: silo and bench are parameters, never tool names.
Each function returns a finished envelope dict; ``server.py`` only wires them to MCP.
"""

from __future__ import annotations

import functools
import re
import time
from typing import Any

from . import api, errors
from .auth import derive_data_tier
from .cache import validate_id
from .config import TTL_RECS, TTL_SILOS
from .context import Context
from .envelope import (
    Envelope,
    ScoresAvailable,
    gated_notice,
    iso,
    observe_rating_rows,
    observe_test_rows,
)
from .errors import RtingsError
from .http import request_scope, requests_made
from .normalize import (
    COVERAGE_UNKNOWN,
    NOT_TESTED,
    REVIEW_UNPUBLISHED,
    TESTED_GATED,
    TESTED_VISIBLE,
    normalize_absent,
    normalize_rating_row,
    normalize_review_row,
    normalize_table_row,
    strip_html,
)
from .observations import ObservationStore
from .schema import SiloSchema, TestDef


def collects_warnings(fn):
    """Open a warning scope around a tool body.

    Warnings belong to the response, so collecting them cannot depend on the caller
    remembering to set that up. The scope is re-entrant, so the server's outer scope — which
    also covers the error envelope — still wins.
    """

    @functools.wraps(fn)
    async def wrapper(ctx: Context, *args: Any, **kwargs: Any) -> Any:
        with ctx.repo.warning_scope(), request_scope():
            return await fn(ctx, *args, **kwargs)

    return wrapper


#: 25 products x 11 usages is ~15K tokens of mostly identical rows on a gated silo, and
#: ~26K on an open one. 10 is a page an agent can actually read; `offset` gets the rest, and
#: `usages=[]` skips the usage surface entirely when only measurements are wanted.
#:
#: Per-row honesty is NOT traded for size: every served row still says why it has its value.
#: What changed is how many rows are served by default.
DEFAULT_RATINGS_LIMIT = 10
MAX_RATINGS_LIMIT = 200
MAX_PROJECTION_TESTS = 40

#: Filter keys answered from the catalog row itself, so they need no test or usage fetched.
#: Kept beside :func:`_apply_filters`, which must recognise exactly this set.
_CATALOG_FILTER_KEYS = frozenset(
    {
        "brand",
        "brand_name",
        "name",
        "name_contains",
        "published",
        "size",
        "tested_variant",
        "variant",
    }
)

_COMPARATOR_RE = re.compile(r"^\s*(>=|<=|!=|>|<|=)?\s*(.+?)\s*$")


# ---------------------------------------------------------------------------------
# rt_silos
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_silos(ctx: Context, *, refresh: bool = False) -> dict[str, Any]:
    """The routing tool. It reports **observed** enforcement, never ``has_paywall``.

    ``has_paywall`` is ``true`` on all 28 silos and therefore carries no information. What
    an agent needs is whether *this* silo's numbers are answerable right now — because 16
    of 28 answer numeric questions anonymously and 12 do not, and the agent cannot tell
    from the outside. Without this, "rank air purifiers by CADR" and "rank TVs by peak
    brightness" are the same-shaped call with wildly different usefulness.
    """
    repo = ctx.repo
    probe = await ctx.auth.ensure_session()
    rows, envelope = await repo.silos(refresh=refresh)
    store = ObservationStore(ctx.cache)

    out = []
    for silo in rows:
        url_part = str(silo.get("url_part"))
        bench = silo.get("test_bench") if isinstance(silo.get("test_bench"), dict) else {}
        # The current bench is the one an agent will actually be routed to, so its
        # observation is the one that answers "are this silo's numbers answerable now".
        current_bench_id = _str_or_none(bench.get("id"))
        observation = None
        if current_bench_id is not None:
            observation = store.read(url_part, current_bench_id)
        if observation is None or observation.insider_total == 0:
            observation = store.read(url_part) or observation
        entry: dict[str, Any] = {
            "silo": url_part,
            "name": silo.get("name"),
            "silo_group": silo.get("silo_group"),
            "review_count": silo.get("review_count"),
            "reviews_in_progress_count": silo.get("reviews_in_progress_count"),
            "first_published_at": silo.get("first_published_at"),
            # Reported because RTINGS ships it, and immediately qualified: it is `true` on
            # all 28, so it never distinguishes anything.
            "has_paywall": silo.get("has_paywall"),
            "current_bench": {"id": _str_or_none(bench.get("id")), "name": bench.get("name")},
            "data_completeness": observation.completeness if observation else "unknown",
            "tool_pages": [
                p.get("url") for p in (silo.get("tool_pages") or []) if isinstance(p, dict)
            ],
        }
        if observation is not None:
            entry["observed"] = {
                "bench_id": observation.bench_id,
                "insider_rows_seen": observation.insider_total,
                "insider_unblurred_ratio": observation.insider_ratio,
                "observed_at": iso(observation.observed_at),
                "fresh": observation.is_fresh,
            }
        out.append(entry)

    return Envelope(
        data={
            "silos": out,
            "notice": (
                "data_completeness is derived from what actually came back unblurred on "
                "this machine's last fetch of each silo; 'unknown' means nothing has been "
                "fetched yet. has_paywall is true for all 28 and carries no information."
            ),
        },
        session=probe.session if probe else "unknown",
        fetched_at=iso(envelope.fetched_at),
        from_cache=requests_made() == 0,
        stale=envelope.is_stale(TTL_SILOS),
        source_url=envelope.source_url,
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list(repo.warnings),
    ).to_json()


# ---------------------------------------------------------------------------------
# rt_schema
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_schema(
    ctx: Context,
    silo: str,
    *,
    bench: str | None = None,
    group: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Test/usage definitions, **bounded**.

    The TV schema is 402 tests in 357 KB, so this never returns the raw payload: with no
    ``group`` it returns the group/category tree with counts, and with one it returns that
    group's leaf tests.
    """
    repo = ctx.repo
    probe = await ctx.auth.ensure_session()
    await repo.resolve_silo(silo)
    schema = await repo.schema(silo, refresh=refresh)
    benches, info = await repo.resolve_benches(silo, [bench] if bench else None)
    bench_id = benches[0] if bench else info.current_id(schema)
    if bench_id is None:
        raise RtingsError(errors.INVALID_BENCH, f"{silo} has no bench with a published schema")
    if schema.bench(bench_id) is None:
        raise RtingsError(
            errors.INVALID_BENCH,
            f"bench {bench_id} has no published schema for {silo}",
            details={"benches_with_schema": schema.bench_order},
        )

    tests = schema.tests_for_bench(bench_id)
    usages = schema.usages_for_bench(bench_id)

    if group is None:
        tree = _schema_tree(schema, tests)
        data: dict[str, Any] = {
            "silo": silo,
            "bench": _bench_json(schema, bench_id),
            "test_count": len(tests),
            "leaf_value_test_count": sum(1 for t in tests if t.is_leaf_value),
            "graph_test_count": sum(1 for t in tests if t.has_graph),
            "insider_only_test_count": sum(1 for t in tests if t.insider_only),
            "groups": tree,
            "usages": [
                {
                    "original_id": u.original_id,
                    "name": u.name,
                    "is_sub_usage": u.is_sub_usage,
                    "is_unscored": u.is_unscored,
                    "parent_usage_name": u.parent_usage_name,
                }
                for u in usages
            ],
            "notice": (
                "insider_only marks a test gate-able, never gated: 16 of 28 silos serve "
                "those values anonymously. Call rt_ratings to see what is actually served."
            ),
        }
    else:
        group_id = validate_id(group, what="group original_id")
        definition = schema.test(group_id)
        if definition is None:
            raise RtingsError(errors.UNKNOWN_TEST, f"no test/group with original_id {group_id}")
        members = [t for t in tests if group_id in _ancestor_ids(schema, t)]
        data = {
            "silo": silo,
            "bench": _bench_json(schema, bench_id),
            "group": {
                "original_id": definition.original_id,
                "name": definition.name,
                "kind": definition.kind,
            },
            "tests": [_test_json(schema, t) for t in members if t.is_leaf_value or t.has_graph],
            "structure_rows": [
                {"original_id": t.original_id, "name": t.name, "kind": t.kind}
                for t in members
                if t.is_structure
            ],
        }

    schema_fetched_at, schema_stale = repo.schema_meta(silo)
    return Envelope(
        data=data,
        session=probe.session if probe else "unknown",
        test_benches=[_bench_json(schema, bench_id)],
        # The schema's own age, not "now": a 29-day-old cached copy reported as freshly
        # fetched is the quiet dishonesty the envelope exists to prevent.
        fetched_at=iso(schema_fetched_at or time.time()),
        from_cache=requests_made() == 0,
        stale=schema_stale,
        source_url=f"https://www.rtings.com/{silo}/tools/table",
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list(repo.warnings) + _bench_hint(info, bench_id),
    ).to_json()


def _schema_tree(schema: SiloSchema, tests: list[TestDef]) -> list[dict[str, Any]]:
    by_parent: dict[str | None, list[TestDef]] = {}
    for test in tests:
        by_parent.setdefault(schema.parent_of(test), []).append(test)

    def build(parent: str | None, depth: int) -> list[dict[str, Any]]:
        out = []
        for test in sorted(by_parent.get(parent, ()), key=lambda t: (t.order, t.name)):
            if not test.is_structure:
                continue
            children = build(test.original_id, depth + 1)
            leaves = [t for t in by_parent.get(test.original_id, ()) if t.is_leaf_value]
            out.append(
                {
                    "original_id": test.original_id,
                    "name": test.name,
                    "kind": test.kind,
                    "leaf_test_count": len(leaves),
                    "insider_only_leaf_count": sum(1 for t in leaves if t.insider_only),
                    "children": children,
                }
            )
        return out

    return build(None, 0)


def _ancestor_ids(schema: SiloSchema, test: TestDef) -> set[str]:
    """Every group AND category above a test, so `group=` can name either level."""
    out: set[str] = set()
    current: TestDef | None = test
    while current is not None:
        parent_id = schema.parent_of(current)
        if not parent_id or parent_id in out:
            break
        out.add(parent_id)
        current = schema.test(parent_id)
    return out


def _test_json(schema: SiloSchema, test: TestDef) -> dict[str, Any]:
    out: dict[str, Any] = {
        "original_id": test.original_id,
        "name": test.name,
        "kind": test.kind,
        "has_score": test.has_score,
        "insider_only": test.insider_only,
        "hierarchy": schema.ancestry(test.original_id),
    }
    if test.kind == "number":
        out["unit"] = test.number_display_unit
        out["precision"] = test.number_display_precision
    if test.words:
        out["words"] = list(test.words)
    return out


def _bench_json(schema: SiloSchema, bench_id: str) -> dict[str, Any]:
    bench = schema.bench(bench_id)
    return {"id": bench_id, "display_name": bench.display_name if bench else None}


def _bench_hint(info: Any, bench_id: str) -> list[str]:
    if bench_id not in set(info.recent_ids):
        return [
            f"bench {bench_id} is outside the recent-bench set {info.recent_ids} that RTINGS "
            "renders together; results are not comparable across that boundary"
        ]
    return []


# ---------------------------------------------------------------------------------
# rt_ratings
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_ratings(
    ctx: Context,
    silo: str,
    *,
    bench: list[str] | None = None,
    tests: list[str] | None = None,
    usages: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    limit: int = DEFAULT_RATINGS_LIMIT,
    offset: int = 0,
    refresh: bool = False,
) -> dict[str, Any]:
    """The catalog plus 0-10 usage scores, with an optional scalar-test projection."""
    repo = ctx.repo
    probe = await ctx.auth.ensure_session()
    await repo.resolve_silo(silo)
    benches, info = await repo.resolve_benches(silo, bench)
    if not benches:
        raise RtingsError(errors.INVALID_BENCH, f"{silo} exposes no benches")
    schema = await repo.schema(silo)

    usage_ids = _resolve_usages(schema, benches, usages)
    test_ids = _resolve_tests(schema, benches, tests)

    # A filter or sort is applied to the rows actually served, so a field nobody projected
    # is absent from every row and the predicate quietly does nothing. Fetch what was
    # referenced (see :func:`_fields_to_fetch`) — bounded by the same projection cap, and
    # only for fields that are really on these benches.
    want_tests, want_usages = _fields_to_fetch(schema, filters, sort)
    bench_tests: set[str] = set()
    bench_usages: set[str] = set()
    for bench_id in benches:
        bench_tests.update(t.original_id for t in schema.tests_for_bench(bench_id))
        bench_usages.update(u.original_id for u in schema.usages_for_bench(bench_id))
    for extra in sorted(want_tests & bench_tests):
        definition = schema.test(extra)
        if definition is not None and definition.is_structure:
            continue
        if extra not in test_ids and len(test_ids) < MAX_PROJECTION_TESTS:
            test_ids.append(extra)
    for extra in sorted(want_usages & bench_usages):
        if extra not in usage_ids:
            usage_ids.append(extra)

    generations = await repo.catalog(silo, benches, refresh=refresh)
    if usage_ids:
        await repo.ensure_rating_slices(silo, benches, usage_ids, refresh=refresh)
    if test_ids:
        await repo.ensure_test_slices(silo, benches, test_ids, refresh=refresh)

    demand_tests = ctx.auth.demand_tier("tests", probe)
    demand_ratings = ctx.auth.demand_tier("ratings", probe)

    scores = ScoresAvailable(
        has_public=any(not t.insider_only for t in schema.tests.values()),
        has_insider=any(t.insider_only for t in schema.tests.values()),
        has_usages=bool(schema.usages),
    )
    all_raw_rows: list[dict[str, Any]] = []
    insider_ids = {t.original_id for t in schema.tests.values() if t.insider_only}

    freshest = 0.0
    stale = False
    current_bench_id = info.current_id(schema)
    products_by_bench: dict[str, list[dict[str, Any]]] = {}
    per_bench_scores: dict[str, ScoresAvailable] = {}

    for bench_id in benches:
        generation = generations.get(bench_id)
        if generation is None:
            continue
        # scores_available is per (silo, bench): the same silo gates differently across
        # benches, so one accumulator folded over the whole set would mislabel every
        # legacy-bench product.
        bench_scores = ScoresAvailable(
            has_public=scores.has_public,
            has_insider=scores.has_insider,
            has_usages=scores.has_usages,
        )
        per_bench_scores[bench_id] = bench_scores
        unpublished = set(generation.unpublished_ids)
        rating_rows: dict[str, dict[str, Any]] = {}
        rating_meta: dict[str, Any] = {}
        for usage_id in usage_ids:
            merged = repo.read_slice("ratings", bench_id, usage_id, demand=demand_ratings)
            meta = repo.slice_meta("ratings", bench_id, usage_id, demand=demand_ratings)
            if meta is not None:
                freshest = max(freshest, meta.fetched_at)
                stale = stale or repo.slice_is_stale(meta, current_bench_id=current_bench_id)
                for target in (scores, bench_scores):
                    observe_rating_rows(
                        target,
                        (meta.payload or {}).get("rows", []),
                        unpublished_product_ids=unpublished,
                    )
            for product_id, entry in (merged or {}).items():
                rating_rows.setdefault(product_id, {})[usage_id] = entry
            rating_meta[usage_id] = meta

        test_rows: dict[str, dict[str, Any]] = {}
        test_meta: dict[str, Any] = {}
        for test_id in test_ids:
            merged = repo.read_slice("tests", bench_id, test_id, demand=demand_tests)
            meta = repo.slice_meta("tests", bench_id, test_id, demand=demand_tests)
            test_meta[test_id] = meta
            if meta is not None:
                freshest = max(freshest, meta.fetched_at)
                stale = stale or repo.slice_is_stale(meta, current_bench_id=current_bench_id)
                raw = (meta.payload or {}).get("rows", [])
                all_raw_rows.extend(raw)
                for target in (scores, bench_scores):
                    observe_test_rows(
                        target,
                        raw,
                        insider_ids=insider_ids,
                        unpublished_product_ids=unpublished,
                    )
            for product_id, entry in (merged or {}).items():
                test_rows.setdefault(product_id, {})[test_id] = entry

        rows_out: list[dict[str, Any]] = []
        for product in generation.products:
            product_id = str(product.get("id"))
            entry = _product_json(product, bench_id, schema)
            entry["usage_scores"] = [
                _usage_json(
                    schema,
                    usage_id,
                    rating_rows.get(product_id, {}),
                    rating_meta.get(usage_id),
                    unpublished,
                    product_id,
                )
                for usage_id in usage_ids
            ]
            if test_ids:
                entry["tests"] = [
                    _test_value_json(
                        schema,
                        test_id,
                        test_rows.get(product_id, {}),
                        test_meta.get(test_id),
                        unpublished,
                        product_id,
                    )
                    for test_id in test_ids
                ]
            rows_out.append(entry)
        products_by_bench[bench_id] = rows_out

    if usage_ids or test_ids:
        store = ObservationStore(ctx.cache)
        for bench_id, bench_scores in per_bench_scores.items():
            store.record(silo, bench_id, bench_scores)

    # Filtering and sorting operate on the SERVED rows, never on the silo: a silo-level rule
    # would either block legitimate sorting on the open 16 or permit silent null-sorting on
    # the gated 12.
    flat = [row for rows in products_by_bench.values() for row in rows]
    warnings = list(repo.warnings)
    filtered, filter_warnings = _apply_filters(flat, filters, schema)
    warnings.extend(filter_warnings)
    ordered, sorted_by, sort_warnings = _apply_sort(filtered, sort, schema)
    warnings.extend(sort_warnings)

    limit = max(1, min(int(limit), MAX_RATINGS_LIMIT))
    offset = max(0, int(offset))

    # The window is applied **per group**, not across the flattened list. A group is one
    # comparable bench population, so a global sort-then-slice can starve a widened bench of
    # every row it matched — and it would be ranking across the very boundary
    # `rank_scope: within_bench` exists to keep.
    # A bench whose catalog could not be fetched has no generation. Reporting it as an
    # ordinary empty group would be indistinguishable from "nothing matched your filter",
    # which hides missing data behind a plausible answer.
    unavailable = [b for b in benches if b not in generations]
    groups = _build_groups(
        benches, info, products_by_bench, ordered, schema, limit, offset, unavailable
    )
    if test_ids:
        catalogued = {
            product_id
            for generation in generations.values()
            for product_id in generation.product_ids
        }
        orphan_group = _uncatalogued_group(repo, schema, test_ids, catalogued, limit)
        if orphan_group is not None:
            groups.append(orphan_group)
    window = [row for group in groups for row in group["products"]]
    statuses = [
        value.get("status")
        for row in window
        for value in (row.get("tests") or []) + (row.get("usage_scores") or [])
        if value.get("status")
    ]

    data: dict[str, Any] = {
        "silo": silo,
        "total_matched": len(ordered),
        "returned": len(window),
        "offset": offset,
        "limit_is_per_group": len(groups) > 1,
        "groups": groups,
    }
    notice = gated_notice(statuses)
    if notice:
        data["notice"] = notice

    return Envelope(
        data=data,
        session=probe.session if probe else "unknown",
        data_tier=derive_data_tier(all_raw_rows, insider_ids),
        scores_available=scores.to_json(),
        # Benches the site renders together but for which RTINGS publishes no schema carry
        # no display name and no tests; listing them as an empty population is noise.
        test_benches=[
            _bench_json(schema, b) for b in benches if schema.bench(b) is not None
        ]
        or [_bench_json(schema, b) for b in benches],
        sorted_by=sorted_by,
        fetched_at=iso(freshest or time.time()),
        # Did THIS call touch the network — not "is the data a couple of seconds old".
        from_cache=requests_made() == 0,
        stale=stale,
        source_url=f"https://www.rtings.com/{silo}/tools/table",
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=warnings,
    ).to_json()


def _resolve_usages(
    schema: SiloSchema, benches: list[str], requested: list[str] | None
) -> list[str]:
    """``None`` means "the headline usages"; ``[]`` means "none at all".

    Defaulting to *every* usage doubles the response on silos with sub-usages (mattress: 20
    usages, 9 of them sub-usages) for a caller who never asked. Sub-usages stay one explicit
    ``usages=[...]`` away.
    """
    available: list[str] = []
    top_level: list[str] = []
    seen: set[str] = set()
    for bench_id in benches:
        for usage in schema.usages_for_bench(bench_id):
            if usage.original_id not in seen:
                seen.add(usage.original_id)
                available.append(usage.original_id)
                if not usage.is_sub_usage:
                    top_level.append(usage.original_id)
    if requested is None:
        return top_level or available
    out = []
    for raw in requested:
        usage_id = _name_or_id(schema, raw, kind="usage")
        if usage_id not in seen:
            raise RtingsError(
                errors.UNKNOWN_TEST,
                f"usage {usage_id} is not on the requested bench(es)",
                details={"available": available},
            )
        out.append(usage_id)
    return out


def _name_or_id(schema: SiloSchema, raw: Any, *, kind: str) -> str:
    """Accept a name wherever an ``original_id`` is accepted.

    ``filters`` and ``sort`` have always taken either, so rejecting a name in ``tests=``
    made the documented remedy for an unapplied filter — "request it too" — fail with
    ``unknown_test`` on the very string the filter had just accepted.
    """
    text = str(raw).strip()
    if text.isdigit():
        return validate_id(text, what=f"{kind} original_id")
    resolved = _field_lookup(schema, text)
    if resolved is None or resolved[0] != kind:
        raise RtingsError(errors.UNKNOWN_TEST, f"no {kind} named {text!r}")
    return resolved[1]


def _fields_to_fetch(
    schema: SiloSchema, filters: dict[str, Any] | None, sort: str | None
) -> tuple[set[str], set[str]]:
    """The tests and usages that ``filters``/``sort`` reference, as ``(tests, usages)``.

    A filter is applied against the rows actually served, so a field nobody *projected* was
    absent from every row and the filter quietly did nothing — on **mattress**, an open silo,
    ``filters={"Thickness": ">1"}`` returned all 69 products with a warning saying no value
    was populated, when the truth was that the test had never been fetched. That is the
    project's core failure mode wearing a different hat: "0 applied" reading as "no data
    exists" when the data was one request away. So a referenced field is fetched, not
    excused.
    """
    tests: set[str] = set()
    usages: set[str] = set()
    keys = [*(filters or {})]
    if sort:
        # `sort` carries its direction as a leading +/-; the field lookup must not see it.
        keys.append(str(sort).strip().lstrip("+-"))
    for key in keys:
        if str(key).lower() in _CATALOG_FILTER_KEYS:
            continue
        resolved = _field_lookup(schema, key)
        if resolved is None:
            continue
        kind, field_id = resolved
        (tests if kind == "test" else usages).add(field_id)
    return tests, usages


def _resolve_tests(
    schema: SiloSchema, benches: list[str], requested: list[str] | None
) -> list[str]:
    if not requested:
        return []
    membership: set[str] = set()
    for bench_id in benches:
        membership.update(t.original_id for t in schema.tests_for_bench(bench_id))
    out: list[str] = []
    for raw in requested:
        test_id = _name_or_id(schema, raw, kind="test")
        definition = schema.test(test_id)
        if definition is None:
            raise RtingsError(errors.UNKNOWN_TEST, f"no test with original_id {test_id}")
        if definition.is_structure:
            raise RtingsError(
                errors.UNKNOWN_TEST,
                f"test {test_id} ({definition.name!r}) is a {definition.kind} row — "
                "structure, not a result",
            )
        if test_id not in membership:
            raise RtingsError(
                errors.UNKNOWN_TEST,
                f"test {test_id} is not on the requested bench(es)",
            )
        out.append(test_id)
    if len(out) > MAX_PROJECTION_TESTS:
        raise RtingsError(
            errors.UNKNOWN_TEST,
            f"at most {MAX_PROJECTION_TESTS} tests may be projected in one call",
        )
    return out


def _variant_info(product: dict[str, Any]) -> tuple[str | None, list[str]]:
    """The size (or other variation) RTINGS actually tested, and the ones it lists.

    RTINGS reviews one SKU of a family and says which: ``reviewed_sku_id`` points into
    ``variant_skus[]``, whose entries carry a ``variation`` like ``'65"'``. There is no
    "Size" test on TVs, so without this "which 65-inch TV is brightest?" cannot be asked at
    all — the field was in the catalog row and simply dropped.
    """
    skus = product.get("variant_skus")
    if not isinstance(skus, list):
        return None, []
    reviewed = str(product.get("reviewed_sku_id") or "")
    variations: list[str] = []
    tested: str | None = None
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        variation = sku.get("variation")
        if not variation:
            continue
        text = str(variation).strip()
        variations.append(text)
        if reviewed and str(sku.get("id")) == reviewed:
            tested = text
    return tested, variations


def _product_json(
    product: dict[str, Any], bench_id: str, schema: SiloSchema
) -> dict[str, Any]:
    page = product.get("page") if isinstance(product.get("page"), dict) else {}
    tested_variant, variants = _variant_info(product)
    return {
        "product_id": str(product.get("id")),
        "name": product.get("fullname"),
        "brand": product.get("brand_name"),
        "url": page.get("url"),
        "released_at": product.get("approximate_released_at"),
        #: The variant RTINGS tested. Results describe THIS one; other sizes in the family
        #: often differ (panel type, brightness), which is why RTINGS names it.
        "tested_variant": tested_variant,
        "variants": variants,
        # `published:false` is an Early Access review: RTINGS has the data and publishes
        # it to Insiders. Different from the paywall, and a membership lifts it.
        "published": product.get("published"),
        "test_bench": _bench_json(schema, bench_id),
        "image": product.get("image"),
    }


def _usage_json(
    schema: SiloSchema,
    usage_id: str,
    per_product: dict[str, Any],
    meta: Any,
    unpublished: set[str],
    product_id: str,
) -> dict[str, Any]:
    definition = schema.usage(usage_id)
    name = definition.name if definition else usage_id
    entry = per_product.get(usage_id)
    if entry is None:
        # STEP 0 before the absent-row test, exactly as on the test path. Without it a
        # product that postdates the cached ratings slice gets a hard `not_tested` for every
        # usage score — "RTINGS did not test this" about a product it may well have rated.
        covered = bool(meta and product_id in set(meta.product_ids or ()))
        return {
            "original_id": usage_id,
            "name": name,
            "product_id": product_id,
            "status": NOT_TESTED if covered else COVERAGE_UNKNOWN,
            "score": None,
            "gated": None,
            # ISO like every other timestamp the envelope carries; the normalizers do this
            # for themselves, and this branch builds its dict by hand.
            "as_of": iso(meta.fetched_at) if (covered and meta) else None,
            "warning": (
                None
                if covered
                else "this product is outside the coverage of the cached ratings slice"
            ),
        }
    return normalize_rating_row(
        entry.row,
        name=name,
        is_unscored=bool(definition and definition.is_unscored),
        # The ROW's own fetch-time snapshot, never the freshly-refetched catalog: the
        # catalog runs on a 3-day clock of its own, so a review published on day 4 would
        # make a day-1 blurred row read as `tested_gated` — "buy a membership" for a review
        # RTINGS had simply not finished (SPEC §7 4a). `rt_product` already does this.
        unpublished_product_ids=_row_unpublished(entry, unpublished),
        as_of=entry.envelope.fetched_at,
    ).to_json()


def _row_unpublished(entry: Any, fallback: set[str]) -> set[str]:
    """Early-Access ids as of the slice that produced this row."""
    ids = getattr(entry.envelope, "unpublished_product_ids", None)
    return set(ids) if ids is not None else fallback


def _test_value_json(
    schema: SiloSchema,
    test_id: str,
    per_product: dict[str, Any],
    meta: Any,
    unpublished: set[str],
    product_id: str,
) -> dict[str, Any]:
    definition = schema.test(test_id)
    if definition is None:  # pragma: no cover - _resolve_tests already validated
        raise RtingsError(errors.UNKNOWN_TEST, f"no test with original_id {test_id}")
    entry = per_product.get(test_id)
    if entry is None:
        # STEP 0 before the absent-row test: a product outside the slice's catalog
        # generation is coverage_unknown, never a false not_tested.
        covered = bool(meta and product_id in set(meta.product_ids or ()))
        value = normalize_absent(
            definition,
            product_id=product_id,
            as_of=meta.fetched_at if meta else None,
            schema=schema,
            covered=covered,
        )
        return value.to_json()
    normalized = normalize_table_row(
        entry.row,
        definition,
        # As above: the row's own fetch-time Early-Access snapshot, not today's catalog.
        unpublished_product_ids=_row_unpublished(entry, unpublished),
        schema=schema,
        as_of=entry.envelope.fetched_at,
    )
    out = normalized.to_json()
    if entry.superseded_at is not None:
        out["superseded_at"] = iso(entry.superseded_at)
    return out


def _uncatalogued_group(
    repo: Any,
    schema: SiloSchema,
    test_ids: list[str],
    catalogued: set[str],
    limit: int,
) -> dict[str, Any] | None:
    """Products with results but no catalog row, reported rather than dropped.

    They carry no name, brand, release date or bench — the catalog is where those live — so
    they are a separate group that says so, not silently mixed in with products that have
    them. Without this a third of the mattress silo is unreachable and reads as untested.
    """
    rows = repo.uncatalogued_rows("tests", test_ids)
    products = []
    for product_id, by_test in sorted(rows.items()):
        if product_id in catalogued:
            continue
        values = []
        for test_id in test_ids:
            definition = schema.test(test_id)
            row = by_test.get(test_id)
            if definition is None or row is None:
                continue
            values.append(
                normalize_table_row(row, definition, schema=schema).to_json()
            )
        if values:
            products.append(
                {
                    "product_id": product_id,
                    "name": None,
                    "test_bench": None,
                    "tests": values,
                    "usage_scores": [],
                }
            )
    if not products:
        return None
    return {
        "test_benches": [],
        "is_recent_set": False,
        "coverage": "uncatalogued",
        "matched": len(products),
        "products": products[:limit],
        "notice": (
            f"{len(products)} product(s) returned measurements but appear in no RTINGS "
            "catalog listing, so their name, brand and bench are unknown here. Their values "
            "are real. Use rt_search or rt_product with the product_id to identify them."
        ),
    }


def _build_groups(
    benches: list[str],
    info: Any,
    products_by_bench: dict[str, list[dict[str, Any]]],
    ordered: list[dict[str, Any]],
    schema: SiloSchema,
    limit: int,
    offset: int,
    unavailable: list[str] | None = None,
) -> list[dict[str, Any]]:
    """One response shape, always.

    The default is a single group spanning the recent set; a caller widening beyond it gets
    **additional groups**, never a different shape and never a flattened cross-bench
    ranking. ``ordered`` carries the filter and sort already applied; the window is taken
    **within each group** so widening cannot starve a bench of the rows it matched.
    """
    recent = set(info.recent_ids)
    missing = set(unavailable or ())
    in_recent = [b for b in benches if b in recent]
    outside = [b for b in benches if b not in recent]
    rank = {id(row): index for index, row in enumerate(ordered)}

    def window_for(bench_ids: list[str]) -> tuple[list[dict[str, Any]], int]:
        members = [
            row
            for bench_id in bench_ids
            for row in products_by_bench.get(bench_id, [])
            if id(row) in rank
        ]
        members.sort(key=lambda row: rank[id(row)])
        return members[offset : offset + limit], len(members)

    def group_for(bench_ids: list[str], *, is_recent: bool) -> dict[str, Any]:
        products, matched = window_for(bench_ids)
        absent = [b for b in bench_ids if b in missing]
        group: dict[str, Any] = {
            "test_benches": [_bench_json(schema, b) for b in bench_ids],
            "is_recent_set": is_recent,
            "matched": matched,
            "products": products,
        }
        if absent:
            group["coverage"] = "unavailable"
            group["unavailable_benches"] = absent
            group["notice"] = (
                "The catalog for "
                + ", ".join(absent)
                + " could not be fetched, so this group is incomplete. An empty or short "
                "list here does not mean nothing matched."
            )
        return group

    groups: list[dict[str, Any]] = []
    if in_recent:
        groups.append(group_for(in_recent, is_recent=True))
    for bench_id in outside:
        groups.append(group_for([bench_id], is_recent=False))
    return groups


# -- filtering and sorting ---------------------------------------------------------


def _field_lookup(schema: SiloSchema, key: str) -> tuple[str, str] | None:
    """Resolve a filter/sort key to ``(kind, id)`` where kind is ``test``/``usage``."""
    text = str(key).strip()
    if text.isdigit():
        if schema.test(text) is not None:
            return "test", text
        if schema.usage(text) is not None:
            return "usage", text
        return None
    lowered = text.lower()
    for test in schema.tests.values():
        if test.name.lower() == lowered:
            return "test", test.original_id
    for usage in schema.usages.values():
        if usage.name.lower() == lowered:
            return "usage", usage.original_id
    return None


def _values_for(row: dict[str, Any], kind: str, field_id: str) -> dict[str, Any] | None:
    key = "tests" if kind == "test" else "usage_scores"
    for entry in row.get(key) or []:
        if entry.get("original_id") == field_id:
            return entry
    return None


def _comparable(entry: dict[str, Any] | None, kind: str) -> Any:
    """The number a filter or sort may legitimately compare, or ``None``.

    **A usage rating has only a score; a test has a value.** Falling back to a test's 0-10
    ``score`` when its ``value`` is null silently compares the wrong quantity — a filter of
    ``peak_brightness < 10`` would match a TV whose value is unknown because its *score* is
    8.5. That is worse than the empty-result failure the guard exists for: it returns
    confidently wrong rows with no signal at all. A test row with no value is simply not
    comparable.
    """
    if entry is None or entry.get("status") != TESTED_VISIBLE:
        return None
    if kind == "usage":
        return entry.get("score")
    return entry.get("value")


def _apply_filters(
    rows: list[dict[str, Any]], filters: dict[str, Any] | None, schema: SiloSchema
) -> tuple[list[dict[str, Any]], list[str]]:
    """A filter on a gated scalar is the quiet version of the core failure.

    Anonymously every gated value is ``null``, so ``filters={peak_brightness: ">1000"}``
    matches **zero products** — and "0 results" reads as *no TV is that bright*, not *you
    cannot see brightness*. So a field that resolves to ``tested_gated`` for the population
    is **not applied**, and the envelope says which field and why.
    """
    if not filters:
        return rows, []
    warnings: list[str] = []
    out = rows
    for key, expression in filters.items():
        lowered = str(key).lower()
        if lowered in {"brand", "brand_name"}:
            wanted = str(expression).lower()
            out = [r for r in out if str(r.get("brand") or "").lower() == wanted]
            continue
        if lowered in {"name_contains", "name"}:
            wanted = str(expression).lower()
            out = [r for r in out if wanted in str(r.get("name") or "").lower()]
            continue
        if lowered == "published":
            wanted_bool = str(expression).lower() in {"1", "true", "yes"}
            out = [r for r in out if bool(r.get("published")) is wanted_bool]
            continue
        if lowered in {"variant", "size", "tested_variant"}:
            wanted_variant = _normalize_variant(expression)
            out = [
                r
                for r in out
                if _normalize_variant(r.get("tested_variant")) == wanted_variant
            ]
            continue

        resolved = _field_lookup(schema, key)
        if resolved is None:
            warnings.append(f"filter_unavailable: no test or usage named {key!r}")
            continue
        kind, field_id = resolved
        populated = 0
        gated = 0
        present = 0
        for row in out:
            entry = _values_for(row, kind, field_id)
            if entry is None:
                continue
            present += 1
            if entry.get("status") == TESTED_GATED:
                gated += 1
            elif _comparable(entry, kind) is not None:
                populated += 1
        if populated == 0:
            # Three different reasons, and they must not be collapsed: "gated" means buy a
            # membership, "absent" means the field is not on this bench, and "empty" means
            # RTINGS measured nothing. Reporting the middle one as "no value is populated"
            # was the bug that made an unfetched field look like missing data.
            if gated:
                reason = "every value is gated for this session"
            elif present == 0:
                reason = (
                    "it is not on the bench(es) queried, so no row carries it — "
                    "call rt_schema for a field these benches define"
                )
            else:
                reason = "RTINGS published no value for it on these rows"
            warnings.append(
                f"filter_unavailable: {key!r} was NOT applied because {reason}. "
                "An empty result here would mean 'you cannot see it', not 'no product "
                "matches'."
            )
            continue
        comparator, operand = _parse_expression(expression)
        out = [
            r
            for r in out
            if _matches(_values_for(r, kind, field_id), comparator, operand, kind)
        ]
    return out, warnings


def _normalize_variant(value: Any) -> str:
    """Compare variants loosely: ``65``, ``65"``, ``65 inch`` and ``65-inch`` are one thing."""
    text = str(value or "").lower()
    text = text.replace("inches", "").replace("inch", "").replace('"', "")
    return "".join(ch for ch in text if ch.isalnum())


def _parse_expression(expression: Any) -> tuple[str, Any]:
    if isinstance(expression, (int, float)) and not isinstance(expression, bool):
        return "=", float(expression)
    match = _COMPARATOR_RE.match(str(expression))
    if not match:
        return "=", str(expression)
    comparator = match.group(1) or "="
    operand_text = match.group(2)
    try:
        return comparator, float(operand_text)
    except ValueError:
        return comparator, operand_text


def _matches(entry: dict[str, Any] | None, comparator: str, operand: Any, kind: str) -> bool:
    value = _comparable(entry, kind)
    if value is None:
        return False
    if isinstance(operand, float):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return {
            "=": number == operand,
            "!=": number != operand,
            ">": number > operand,
            ">=": number >= operand,
            "<": number < operand,
            "<=": number <= operand,
        }[comparator]
    text = str(value).lower()
    wanted = str(operand).lower()
    if comparator == "!=":
        return text != wanted
    return text == wanted or wanted in text


def _apply_sort(
    rows: list[dict[str, Any]], sort: str | None, schema: SiloSchema
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """The default sort is a **public catalog field**, never a gated score."""
    warnings: list[str] = []
    field_name = (sort or "").strip()
    direction = "desc"
    if field_name.startswith("-"):
        field_name, direction = field_name[1:], "desc"
    elif field_name.startswith("+"):
        field_name, direction = field_name[1:], "asc"

    if not field_name or field_name in {"released_at", "approximate_released_at"}:
        ordered = sorted(rows, key=lambda r: str(r.get("released_at") or ""), reverse=True)
        return ordered, {"field": "released_at", "gated": False, "direction": "desc"}, warnings
    if field_name == "name":
        ordered = sorted(rows, key=lambda r: str(r.get("name") or "").lower())
        return ordered, {"field": "name", "gated": False, "direction": "asc"}, warnings

    resolved = _field_lookup(schema, field_name)
    if resolved is None:
        warnings.append(
            f"filter_unavailable: no test or usage named {field_name!r}; sorted by "
            "released_at instead"
        )
        ordered = sorted(rows, key=lambda r: str(r.get("released_at") or ""), reverse=True)
        return (
            ordered,
            {
                "field": "released_at",
                "gated": False,
                "direction": "desc",
                "requested": field_name,
                "fallback_reason": "no test or usage has that name",
            },
            warnings,
        )

    kind, field_id = resolved
    populated = [r for r in rows if _comparable(_values_for(r, kind, field_id), kind) is not None]
    if not populated:
        # Same three-way split as the filter path: a field absent from every row is a
        # different problem from one that is gated, and telling the caller the wrong one
        # sends them to buy a membership they do not need.
        absent = all(_values_for(r, kind, field_id) is None for r in rows)
        why = (
            "is not on the bench(es) queried, so no row carries it"
            if absent
            else "is gated or unpopulated for these rows"
        )
        warnings.append(
            f"filter_unavailable: {field_name!r} {why}, so the ordering would have been "
            "arbitrary; sorted by released_at instead"
        )
        ordered = sorted(rows, key=lambda r: str(r.get("released_at") or ""), reverse=True)
        # `gated` describes the field actually USED. Reporting
        # `{field: "released_at", gated: true}` reads as "release date is gated", which is
        # nonsense; the requested field is named separately.
        return (
            ordered,
            {
                "field": "released_at",
                "gated": False,
                "direction": "desc",
                "requested": field_name,
                "fallback_reason": (
                    "the requested field is not on the bench(es) queried"
                    if absent
                    else "the requested field is gated or unpopulated for these rows"
                ),
            },
            warnings,
        )

    def key(row: dict[str, Any]) -> tuple[int, float]:
        candidate = _comparable(_values_for(row, kind, field_id), kind)
        number = 0.0
        present = candidate is not None
        if present:
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                present = False
        # `reverse` flips the WHOLE tuple, so a fixed "missing = 1" put rows with no value
        # at the TOP of every descending sort — "the brightest TVs" led by TVs whose
        # brightness is gated or untested. Pre-flip the presence flag so a row the server
        # cannot compare sorts last in both directions.
        rank = (1 if present else 0) if direction == "desc" else (0 if present else 1)
        return (rank, number)

    ordered = sorted(rows, key=key, reverse=direction == "desc")
    definition = schema.test(field_id) if kind == "test" else schema.usage(field_id)
    return (
        ordered,
        {
            "field": field_name,
            "name": definition.name if definition else None,
            "gated": False,
            "direction": direction,
        },
        warnings,
    )


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------------
# rt_product
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_product(
    ctx: Context,
    product: str,
    *,
    silo: str | None = None,
    group: str | None = None,
    include_prose: bool = False,
    include_media: bool = False,
    include_verdicts: bool = False,
    consume_preview: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    """One review: leaf test results by hierarchy, prose and media opt-in.

    Bounded like the others — a current-bench review is 402 rows / 437 KB — and **metered**:
    on a free session this is the endpoint RTINGS counts, so the budget is enforced in the
    repository before the POST ever happens.

    This path has **no machine ``value``**. A review row carries ``rendered_value`` (a
    formatted string) plus ``score`` and nothing else, so numbers here are recovered from
    RTINGS' own display string and are display-rounded. Every one is labelled
    ``value_source: "rendered"``. When a clean number matters, use ``rt_ratings``.
    """
    repo = ctx.repo
    await ctx.auth.ensure_session()
    ref = await repo.resolve_product(product, silo)
    if not ref.url_path:
        raise RtingsError(
            errors.UNKNOWN_PRODUCT,
            f"resolved product {ref.product_id} but found no review URL for it",
        )
    await repo.resolve_silo(ref.silo)
    try:
        envelope, stale = await repo.review(
            ref.silo,
            ref.product_id,
            ref.url_path,
            consume_preview=consume_preview,
            refresh=refresh,
        )
    except RtingsError as exc:
        # The verdicts are believed unmetered — they are the public compare tool, not the
        # review page. Letting a spent preview budget also withhold them would deny the one
        # surface documented as the substantive answer on a gated category, for a cost it
        # does not incur. So a budget refusal degrades to a verdicts-only response instead
        # of failing outright.
        if not (include_verdicts and exc.code == errors.PREVIEW_EXHAUSTED):
            raise
        return await _verdicts_only(ctx, ref, exc, refresh=refresh)
    # Read the probe AFTER the fetch, not before: `repo.review` re-probes when it spent a
    # preview, and `previews_remaining` in the envelope must reflect the spend it just made.
    probe = ctx.auth.cached_probe()
    schema = await repo.schema(ref.silo)

    page = envelope.payload if isinstance(envelope.payload, dict) else {}
    product_obj = page.get("product") if isinstance(page.get("product"), dict) else {}
    review = product_obj.get("review") if isinstance(product_obj.get("review"), dict) else {}
    rows = [r for r in (review.get("test_results") or []) if isinstance(r, dict)]
    bench = review.get("test_bench") if isinstance(review.get("test_bench"), dict) else {}
    bench_id = _str_or_none(bench.get("id")) or envelope.bench_id

    # The expected-test set is scoped to the product's OWN bench, never the silo: a v0.9 TV
    # returns 54 rows, not 402, and joining against the full silo schema would invent
    # hundreds of false not_tested rows.
    unpublished = set(envelope.unpublished_product_ids or ())
    is_unpublished = ref.product_id in unpublished

    insider_ids = {t.original_id for t in schema.tests.values() if t.insider_only}
    scores = ScoresAvailable(
        has_public=any(not t.insider_only for t in schema.tests.values()),
        has_insider=any(t.insider_only for t in schema.tests.values()),
        has_usages=bool(schema.usages),
    )
    raw_for_tier: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    commentary: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    group_filter = validate_id(group, what="group original_id") if group else None

    for row in rows:
        stub = row.get("test") if isinstance(row.get("test"), dict) else {}
        original_id = _str_or_none(stub.get("original_id"))
        if original_id is None:
            continue
        seen_ids.add(original_id)
        definition = schema.test(original_id)
        if definition is None:
            # The row's own stub carries name/kind/insider_only, so a test missing from the
            # schema is still reportable — but unit and precision are NEVER inferred, so
            # they stay absent rather than guessed.
            definition = TestDef(
                original_id=original_id,
                name=str(stub.get("name") or ""),
                kind=str(stub.get("kind") or ""),
                has_score=bool(stub.get("has_score")),
                insider_only=bool(stub.get("insider_only")),
                parent_original_id=None,
                order=0,
                published=True,
                number_display_unit=None,
                number_display_precision=None,
                number_prefix=None,
                words=(),
            )
        raw_for_tier.append(
            {
                "original_id": original_id,
                "unblurred": row.get("unblurred"),
                "status": row.get("status"),
                "product_id": ref.product_id,
            }
        )
        # `group` and `category` rows are structure, not results: they carry no value and
        # must never be given a status. A group marked not_tested is a category header
        # reported as a missing measurement.
        #
        # They are still where RTINGS hangs its prose (measured: of 53 rows carrying a
        # `linked_description` on the X90L, the commentary sits on section rows, not leaves),
        # so with include_prose it is collected separately rather than discarded — as
        # commentary, never as a result with a status.
        if definition.is_structure:
            if include_prose and row.get("linked_description"):
                commentary.append(
                    {
                        "original_id": original_id,
                        "name": definition.name,
                        "kind": definition.kind,
                        "hierarchy": schema.ancestry(original_id),
                        "text": row.get("linked_description"),
                    }
                )
            continue
        if not (definition.is_leaf_value or definition.has_graph) and not include_media:
            continue
        if group_filter and group_filter not in _ancestor_ids(schema, definition):
            continue

        normalized = normalize_review_row(
            row,
            definition,
            product_id=ref.product_id,
            unpublished=is_unpublished,
            schema=schema,
            as_of=envelope.fetched_at,
        )
        entry = _strip_response_constants(normalized.to_json())
        if include_prose and row.get("linked_description"):
            entry["description"] = row.get("linked_description")
        if include_media:
            media = {
                k: row.get(k)
                for k in ("asset_url", "asset_thumb_url", "asset_url_3d", "graph_data_url")
                if row.get(k)
            }
            if media:
                entry["media"] = media
        elif row.get("graph_data_url"):
            entry["has_graph"] = True
        values.append(entry)

    observe_test_rows(
        scores,
        raw_for_tier,
        insider_ids=insider_ids,
        unpublished_product_ids=unpublished,
    )

    missing = []
    if bench_id and schema.bench(bench_id) is not None:
        # A review body normally carries EVERY test on the product's own bench (54/54 and
        # 402/402 measured), so this list is empty in practice. It stops being empty exactly
        # when the schema and the cached review disagree — and the schema is fetched on its
        # own clock, so a schema newer than the review may list a test the review predates.
        # Calling that `not_tested` asserts "RTINGS did not measure this" from two documents
        # of different ages, which is the false-absence the safety property forbids. SPEC §8
        # promised a `bench_mismatch` guard here; this is it.
        # Gated on the review being STALE, not merely older: on a cold call the schema is
        # legitimately fetched moments after the review, and that is not drift. Only a
        # past-TTL review read against a newer schema is genuinely unable to answer.
        schema_fetched_at, _ = repo.schema_meta(ref.silo)
        covered = not (
            stale and schema_fetched_at is not None and schema_fetched_at > envelope.fetched_at
        )
        for definition in schema.leaf_tests_for_bench(bench_id):
            if definition.original_id in seen_ids:
                continue
            if group_filter and group_filter not in _ancestor_ids(schema, definition):
                continue
            missing.append(
                _strip_response_constants(
                    normalize_absent(
                        definition,
                        product_id=ref.product_id,
                        as_of=envelope.fetched_at,
                        schema=schema,
                        covered=covered,
                    ).to_json()
                )
            )
        if missing and not covered:
            repo.warn(
                f"bench_mismatch: {len(missing)} test(s) on bench {bench_id} are in the "
                "schema but absent from this cached review, and the schema is the newer of "
                "the two — reported as coverage_unknown rather than not_tested. "
                "refresh=true resolves it"
            )
    values.extend(missing)

    data: dict[str, Any] = {
        "product": {
            "product_id": ref.product_id,
            "name": product_obj.get("fullname") or ref.name,
            "url": ref.url_path,
            "silo": ref.silo,
            "test_bench": {"id": bench_id, "display_name": bench.get("display_name")},
            "published": not is_unpublished,
            "variants": product_obj.get("variants_rendered_list"),
            "tested_variant": _tested_variant_from_review(product_obj),
        },
        "value_source_notice": (
            "Numbers on this path are parsed from RTINGS' own display strings and are "
            "therefore display-rounded; rt_ratings returns the unrounded machine values."
        ),
        "results": values,
        "result_count": len(values),
    }
    if include_prose:
        data["summary"] = {
            "compared": review.get("compared_summary_linked"),
            "differences": review.get("differences_summary_linked"),
        }
        data["commentary"] = commentary
    notice = gated_notice([v.get("status") for v in values if v.get("status")])
    if notice:
        data["notice"] = notice
    if is_unpublished:
        # `published:false` is EARLY ACCESS, and a membership is exactly what lifts it —
        # RTINGS: "the writer publishes it for Early Access so that Insiders who support us
        # can see the data without any text." The old wording here ("blurred for everyone —
        # a membership does not lift it") was the pre-correction reading, and it contradicted
        # this response's own rows: a member's Early Access values arrive `tested_visible`,
        # so the notice claimed everything was hidden while the data sat beside it.
        visible = sum(1 for v in values if v.get("status") == TESTED_VISIBLE)
        data["notice"] = (
            "This review is Early Access (published:false): RTINGS has not finished the "
            "written review, and publishes the data to Insiders in the meantime. Withheld "
            "values here are `review_unpublished`, NOT `tested_gated` — the distinction "
            "matters because an Insider membership does lift this."
            + (
                f" {visible} value(s) came through visible on this session."
                if visible
                else ""
            )
        )

    if include_verdicts:
        # One extra request, and the only way to get RTINGS' written verdicts. Deliberately
        # not folded into `include_prose`: that flag costs nothing today (the per-test prose
        # rides on the review body already fetched), and silently doubling its request count
        # would be a surprise.
        try:
            verdict_env, verdict_stale = await repo.side_by_side(
                ref.silo, ref.product_id, refresh=refresh
            )
            shaped = _verdicts_from(
                verdict_env.payload or {}, schema, unpublished=is_unpublished
            )
            data.update(shaped)
            # These scores are usage ratings by another name, so they answer the question
            # `scores_available.usage_ratings` asks — which would otherwise report `unknown`
            # for a call that just returned 18 of them.
            for verdict in shaped["verdicts"]:
                # Early Access rows are excluded for the same reason unpublished products are
                # excluded everywhere else: they are withheld for a different reason and
                # would make an open silo look gated.
                if verdict["status"] in {TESTED_VISIBLE, TESTED_GATED}:
                    scores.usage_ratings.add(unblurred=verdict["score"] is not None)
            if verdict_stale:
                repo.warn("verdicts served from a past-TTL cached copy")
        except RtingsError as exc:
            # The measurements are already in hand; losing the commentary must not lose them.
            repo.warn(f"verdicts unavailable ({exc.code}): {exc.message}")

    if bench_id:
        # Recorded after the verdict block, so a call that observed the usage surface counts.
        ObservationStore(ctx.cache).record(ref.silo, bench_id, scores)

    warnings = list(repo.warnings)
    if stale:
        warnings.append("served a past-TTL cached review rather than spending a preview")

    return Envelope(
        data=data,
        session=probe.session if probe else "unknown",
        data_tier=derive_data_tier(raw_for_tier, insider_ids),
        # Computed last: the verdict block above may have observed the usage surface.
        scores_available=scores.to_json(),
        test_benches=[{"id": bench_id, "display_name": bench.get("display_name")}],
        fetched_at=iso(envelope.fetched_at),
        from_cache=requests_made() == 0,
        stale=stale,
        source_url=envelope.source_url,
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=warnings,
    ).to_json()


def _strip_response_constants(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop the two fields that are identical on every row of an ``rt_product`` response.

    ``product_id`` is the product the whole call is about — it is already in
    ``data.product`` — and ``as_of`` is the single fetch time of the one review, already in
    the envelope's ``fetched_at``. Repeating them 243 times cost ~13 KB of a 69 KB response
    and told the reader nothing it did not already have.

    They stay per-row in ``rt_ratings``, where they genuinely vary: that response spans many
    products and many slices with different ages.
    """
    entry.pop("product_id", None)
    entry.pop("as_of", None)
    return entry


def _tested_variant_from_review(product_obj: dict[str, Any]) -> str | None:
    """The reviewed SKU's variation, from the review body's own sku list."""
    reviewed = str(product_obj.get("reviewed_sku_id") or "")
    if not reviewed:
        return None
    for key in ("variant_skus", "skus"):
        for sku in product_obj.get(key) or []:
            if isinstance(sku, dict) and str(sku.get("id")) == reviewed:
                variation = sku.get("variation")
                return str(variation).strip() if variation else None
    return None


def _verdicts_from(
    review: dict[str, Any], schema: SiloSchema, *, unpublished: bool = False
) -> dict[str, Any]:
    """RTINGS' words about a product: per-usage verdicts, pros/cons, and the score recipe.

    **This is the most valuable thing available on a category that withholds numbers.** The
    measurements come back gated there, but the verdict prose does not — so "is this good for
    gaming?" has a real answer even when "how bright is it?" does not.

    ``user_has_access`` is this path's blur signal — it has no per-row ``unblurred`` key.
    Measured anonymously: ``false`` on TV (gated), ``true`` on mattress (open), so it tracks
    the silo's enforcement rather than the caller's membership. A score is reported when it
    is there and marked ``tested_gated`` when the flag says the data was withheld, exactly as
    on every other surface.
    """
    has_access = bool(review.get("user_has_access"))
    definitions = {
        str(s.get("id")): s for s in review.get("score_sets") or [] if isinstance(s, dict)
    }
    # The payload's own bench listing maps a component's internal `test_id` to a name.
    # Without it a score built from a raw test reports `component: null`, and the internal
    # id is not joinable against the schema (which is keyed by `original_id`, and id !=
    # original_id).
    bench_tests = {
        str(t.get("id")): t
        for t in ((review.get("test_bench") or {}).get("tests") or [])
        if isinstance(t, dict)
    }

    verdicts: list[dict[str, Any]] = []
    for entry in review.get("product_score_sets") or []:
        if not isinstance(entry, dict):
            continue
        definition = definitions.get(str(entry.get("score_set_id"))) or {}
        original_id = _str_or_none(entry.get("score_set__original_id"))
        usage = schema.usage(original_id) if original_id else None
        score = entry.get("score")
        verdicts.append(
            {
                "original_id": original_id,
                "name": (usage.name if usage else definition.get("name")),
                "kind": definition.get("kind"),
                # Three states, and the third is deliberately NOT `not_tested`.
                #
                # This payload has no per-row flag — one review-wide `user_has_access` is the
                # whole signal. When access is granted and a score is simply absent, calling
                # it `not_tested` asserts "RTINGS did not measure this", which that signal
                # cannot support and which is the single claim this project must never make
                # on thin evidence. The measured precedent is the other way: a visible row
                # can be genuinely empty (SPEC §5), so it is `tested_visible` with a null
                # score and `gated: null`.
                "status": _verdict_status(score, has_access, unpublished),
                "score": score,
                # `gated` follows the same three-way split as the status, and Early Access
                # is `null`: `true` would blame the category paywall for a review RTINGS
                # simply has not finished publishing.
                "gated": _verdict_gated(score, has_access, unpublished),
                "suitable": entry.get("suitable"),
                "verdict": strip_html(entry.get("linked_description")),
            }
        )

    highlights: list[dict[str, Any]] = []
    for summary in review.get("summaries") or []:
        if not isinstance(summary, dict):
            continue
        text = strip_html(summary.get("blurb"))
        if not text:
            continue
        priority = summary.get("priority")
        highlights.append(
            {
                # RTINGS marks a con with a negative priority and a pro with a positive one.
                "sentiment": "con" if isinstance(priority, int) and priority < 0 else "pro",
                "text": text,
                "title": summary.get("title"),
            }
        )

    scoring: list[dict[str, Any]] = []
    for definition in review.get("score_sets") or []:
        if not isinstance(definition, dict) or not definition.get("items"):
            continue
        components = []
        for item in definition["items"]:
            if not isinstance(item, dict):
                continue
            referenced = definitions.get(str(item.get("score_set_id")))
            test = bench_tests.get(str(item.get("test_id")))
            components.append(
                {
                    "weight_pct": item.get("weight"),
                    # A component is either a sub-score or a raw test; both resolve to a
                    # name, and the test's `original_id` is what joins to rt_schema.
                    "component": (
                        referenced.get("name")
                        if referenced
                        else (test.get("name") if test else None)
                    ),
                    "original_id": _str_or_none(
                        referenced.get("original_id") if referenced else
                        (test.get("original_id") if test else None)
                    ),
                    "component_kind": "usage" if referenced else ("test" if test else None),
                }
            )
        scoring.append(
            {
                "original_id": _str_or_none(definition.get("original_id")),
                "name": definition.get("name"),
                "components": components,
            }
        )

    return {
        "verdicts": verdicts,
        "highlights": highlights,
        "scoring": scoring,
        # **Its own key, never `notice`.** `data["notice"]` already carries the reason the
        # measurements are null — including "this review is Early Access", which must never
        # be replaced by paywall framing. `dict.update` would have overwritten it.
        "verdicts_notice": (
            "RTINGS' own words. On a category that withholds measurements these verdicts and "
            "highlights are served anyway, so they are the substantive answer where the "
            "numbers are null."
            if not has_access
            else "RTINGS' own words, alongside the measurements."
        ),
    }


async def _verdicts_only(
    ctx: Context, ref: Any, cause: RtingsError, *, refresh: bool
) -> dict[str, Any]:
    """Serve RTINGS' words when the measurements could not be bought.

    Reached only when a free session is out of metered previews *and* the caller asked for
    verdicts. The response is honest about what is missing: `error` stays null because there
    is real content, and a warning names the budget as the reason the measurements are absent.
    """
    repo = ctx.repo
    probe = ctx.auth.cached_probe()
    schema = await repo.schema(ref.silo)
    verdict_env, verdict_stale = await repo.side_by_side(
        ref.silo, ref.product_id, refresh=refresh
    )
    data: dict[str, Any] = {
        "product": {
            "product_id": ref.product_id,
            "name": ref.name,
            "url": ref.url_path,
            "silo": ref.silo,
        },
        "results": [],
        "result_count": 0,
        "notice": (
            "The measurements were not fetched: " + cause.message + " RTINGS' verdicts below "
            "are served regardless and are not affected."
        ),
    }
    # `ref.published` is False for an Early Access product. Omitting it defaulted to
    # `unpublished=False`, so a withheld verdict score on an enforcing silo came back
    # `tested_gated` — "buy a membership" — for a review RTINGS had not finished. It is
    # `None` only when the product was resolved by search, where the flag is genuinely
    # unknown; treating that as "published" is the existing behaviour everywhere else.
    data.update(
        _verdicts_from(
            verdict_env.payload or {}, schema, unpublished=ref.published is False
        )
    )
    repo.warn(f"measurements unavailable ({cause.code}); serving verdicts only")
    return Envelope(
        data=data,
        session=probe.session if probe else "unknown",
        fetched_at=iso(verdict_env.fetched_at),
        from_cache=requests_made() == 0,
        stale=verdict_stale,
        source_url=verdict_env.source_url,
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list(repo.warnings),
    ).to_json()


def _verdict_gated(score: Any, has_access: bool, unpublished: bool) -> bool | None:
    """`false` for a served score, `true` only for the category paywall, `null` otherwise.

    Never `false` beside a null score — that pair is indistinguishable from a visible value
    that happens to be empty, which is the one thing SPEC §7 forbids.
    """
    if score is not None:
        return False
    if unpublished:
        return None
    return True if not has_access else None


def _verdict_status(score: Any, has_access: bool, unpublished: bool) -> str:
    """The state of one usage verdict, from a review-wide access flag and nothing else."""
    if score is not None:
        return TESTED_VISIBLE
    if unpublished:
        # Early Access: withheld from this session, and a membership lifts it. Reporting
        # `tested_gated` here would blame the category paywall for the wrong thing.
        return REVIEW_UNPUBLISHED
    if not has_access:
        return TESTED_GATED
    return TESTED_VISIBLE


# ---------------------------------------------------------------------------------
# rt_graph
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_graph(
    ctx: Context,
    product: str,
    test: str,
    *,
    silo: str | None = None,
    full: bool = False,
    max_points: int | None = None,
) -> dict[str, Any]:
    """One test's measurement curve, resampled by **selecting** shipped points.

    Never interpolated, averaged or smoothed: an interpolated point is a number RTINGS
    never measured. And no headline scalar, including axis bounds — on a peak-luminance or
    EOTF curve a ``y_range.max`` *is* the gated scalar in all but name, so what is returned
    is labelled ``axis_bounds_of_served_points``.
    """
    if not ctx.config.enable_graph:
        raise RtingsError(
            errors.NO_GRAPH, "rt_graph is disabled by configuration (RTINGS_ENABLE_GRAPH=false)"
        )
    repo = ctx.repo
    probe = await ctx.auth.ensure_session()
    ref = await repo.resolve_product(product, silo)
    await repo.resolve_silo(ref.silo)
    schema = await repo.schema(ref.silo)
    test_id = validate_id(test, what="test original_id")
    definition = schema.test(test_id)

    if definition is None:
        raise RtingsError(errors.UNKNOWN_TEST, f"no test with original_id {test_id}")
    if not definition.has_graph:
        # Structural, and distinct from "this product has no curve for it".
        raise RtingsError(
            errors.NO_GRAPH,
            f"{definition.name!r} is kind={definition.kind!r}; only kind=graph tests have a "
            "curve",
        )

    envelope = await repo.graph(ref.silo, ref.product_id, test_id)
    if envelope.outcome != "ok" or not isinstance(envelope.payload, dict):
        raise RtingsError(
            errors.GRAPH_NOT_AVAILABLE,
            f"RTINGS publishes no curve for product {ref.product_id} on {definition.name!r}; "
            "curve coverage is per-(product, bench), not schema-wide",
        )

    header = envelope.payload.get("header") or []
    points = envelope.payload.get("data") or []
    target = max_points or ctx.config.graph_max_points
    served, decimated = (points, False) if full else _decimate(points, target)

    data = {
        "product": {"product_id": ref.product_id, "name": ref.name, "silo": ref.silo},
        "test": {
            "original_id": definition.original_id,
            "name": definition.name,
            "kind": definition.kind,
        },
        "header": header,
        "n_points": len(served),
        "n_points_shipped": len(points),
        "resampled": decimated,
        "resampling": (
            "uniform decimation — every returned point is a point RTINGS shipped; nothing "
            "is interpolated, averaged or smoothed"
        ),
        "axis_bounds_of_served_points": _axis_bounds(served),
        "points": served,
    }
    return Envelope(
        data=data,
        session=probe.session if probe else "unknown",
        fetched_at=iso(envelope.fetched_at),
        from_cache=requests_made() == 0,
        source_url=envelope.source_url,
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list(repo.warnings),
    ).to_json()


def _decimate(points: list[Any], target: int) -> tuple[list[Any], bool]:
    """Select every Nth shipped point, always keeping the first and last.

    Selection, not interpolation — that distinction is the whole point of the rule.
    """
    if target < 2 or len(points) <= target:
        return points, False
    stride = len(points) / float(target - 1)
    picked: list[Any] = []
    seen: set[int] = set()
    for step in range(target - 1):
        index = int(step * stride)
        if index not in seen and index < len(points):
            seen.add(index)
            picked.append(points[index])
    last = len(points) - 1
    if last not in seen:
        picked.append(points[last])
    return picked, True


def _axis_bounds(points: list[Any]) -> dict[str, Any] | None:
    """Bounds of the points we served — deliberately NOT a measurement of the product."""
    xs = [p[0] for p in points if isinstance(p, list) and p and isinstance(p[0], (int, float))]
    if not xs:
        return None
    return {"x_min": min(xs), "x_max": max(xs)}


# ---------------------------------------------------------------------------------
# rt_search
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_search(ctx: Context, query: str, *, count: int = 10) -> dict[str, Any]:
    """Model name/number to candidates across all silos, via RTINGS' own live index."""
    text = str(query or "").strip()
    if not text:
        raise RtingsError(errors.UNKNOWN_PRODUCT, "search query is empty")
    probe = await ctx.auth.ensure_session()
    result = await api.search(ctx.transport, text, count=max(1, min(int(count), 50)))
    hits = []
    for hit in result.get("results") or []:
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("url") or "")
        hits.append(
            {
                "kind": hit.get("kind"),
                "title": hit.get("title"),
                "url": url,
                "product_id": _str_or_none(hit.get("product_id")),
                "silo": url.strip("/").split("/", 1)[0].lower() if url else None,
                "thumbnail": hit.get("thumbnail"),
                "excerpt": hit.get("highlighted_content"),
            }
        )
    return Envelope(
        data={
            "query": text,
            "total_count": result.get("total_count"),
            "results": hits,
            "searched": "rtings live index",
            "notice": (
                "An empty result means RTINGS' live search index has no match — it is not "
                "evidence that a product was not tested."
            ),
        },
        session=probe.session if probe else "unknown",
        fetched_at=iso(time.time()),
        source_url="https://www.rtings.com/",
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list(ctx.repo.warnings),
    ).to_json()


# ---------------------------------------------------------------------------------
# rt_recommendations
# ---------------------------------------------------------------------------------


@collects_warnings
async def rt_recommendations(
    ctx: Context, silo: str, *, list: str | None = None, refresh: bool = False
) -> dict[str, Any]:
    """The silo's best-of lists, or one ranked list with its reasoning.

    The one page-extraction path in the project. A silo carries dozens of lists and the URL
    slug is not derivable from the silo name, so with no ``list`` this returns the
    **discovered** index rather than guessing a slug.
    """
    repo = ctx.repo
    probe = await ctx.auth.ensure_session()
    await repo.resolve_silo(silo)

    if not list:
        envelope = await repo.recommendation_lists(silo, refresh=refresh)
        return Envelope(
            data={
                "silo": silo,
                "lists": (envelope.payload or {}).get("lists", []),
                "notice": "Pass one of these `list` values to get that ranking.",
            },
            session=probe.session if probe else "unknown",
            fetched_at=iso(envelope.fetched_at),
            from_cache=requests_made() == 0,
            source_url=envelope.source_url,
            previews_remaining=probe.previews_remaining if probe else None,
            warnings=list_warnings(repo),
        ).to_json()

    index = await repo.recommendation_lists(silo)
    known = {entry.get("list") for entry in (index.payload or {}).get("lists", [])}
    if list not in known:
        raise RtingsError(
            errors.UNKNOWN_PRODUCT,
            f"{list!r} is not one of {silo}'s discovered best-of lists",
            details={"available": sorted(k for k in known if k)},
        )

    envelope = await repo.recommendation(silo, list, refresh=refresh)
    payload = envelope.payload or {}
    picks = []
    for rank, pick in enumerate(payload.get("product_recommendations") or [], start=1):
        if not isinstance(pick, dict):
            continue
        product = pick.get("product") if isinstance(pick.get("product"), dict) else {}
        page = product.get("page") if isinstance(product.get("page"), dict) else {}
        picks.append(
            {
                "rank": rank,
                "title": pick.get("title"),
                "subtitle": pick.get("subtitle"),
                "reasoning": pick.get("description"),
                "product_id": _str_or_none(pick.get("product_id") or product.get("id")),
                "name": product.get("fullname"),
                "url": page.get("url"),
                "overall_score": product.get("preferred_scoreset_score"),
                "variants": product.get("variants_rendered_list"),
                "featured_results": _featured_results(pick.get("featured_test_results")),
                "usage_scores": _featured_ratings(pick.get("ratings")),
            }
        )

    # The featured rows carry `insider_only` on their own stub, so the safe-direction read
    # still works here even though they cannot be joined to the schema by `original_id`.
    featured_rows = [
        {
            "original_id": "featured",
            "unblurred": row.get("unblurred"),
            "status": row.get("status"),
        }
        for pick in (payload.get("product_recommendations") or [])
        if isinstance(pick, dict)
        for row in (pick.get("featured_test_results") or [])
        if isinstance(row, dict) and (row.get("test") or {}).get("insider_only")
    ]
    return Envelope(
        data={
            "silo": silo,
            "list": list,
            "title": payload.get("title"),
            "url": payload.get("url"),
            "updated_at": payload.get("updated_at"),
            "introduction": payload.get("introduction"),
            "picks": picks,
            "notice": (
                "Ranking and reasoning are RTINGS' editorial picks, served anonymously. The "
                "featured numbers beside each pick follow the same blur rules as everywhere "
                "else."
            ),
        },
        session=probe.session if probe else "unknown",
        data_tier=derive_data_tier(featured_rows, {"featured"}),
        fetched_at=iso(envelope.fetched_at),
        from_cache=requests_made() == 0,
        stale=envelope.is_stale(TTL_RECS),
        source_url=envelope.source_url,
        previews_remaining=probe.previews_remaining if probe else None,
        warnings=list_warnings(repo),
    ).to_json()


def list_warnings(repo: Any) -> list[str]:
    return list(repo.warnings)


def _featured_results(rows: Any) -> list[dict[str, Any]]:
    """Recommendation rows carry an inline ``test`` stub with **no ``original_id``**
    (verified 2026-09-03), so they cannot be joined to the schema. They are reported with
    the same seven-state honesty using the stub's own name/kind, and no value is coerced."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stub = row.get("test") if isinstance(row.get("test"), dict) else {}
        status = row.get("status")
        unblurred = bool(row.get("unblurred"))
        if status == "na":
            state = "not_applicable"
        elif status == "untested":
            state = NOT_TESTED
        elif status != "tested":
            state = "unknown_row_status"
        elif not unblurred:
            state = TESTED_GATED
        else:
            state = TESTED_VISIBLE
        entry: dict[str, Any] = {
            "name": stub.get("name") or stub.get("featured_name"),
            "kind": stub.get("kind"),
            "insider_only": bool(stub.get("insider_only")),
            "status": state,
            "gated": True if state == TESTED_GATED else None,
            "display": _strip(row.get("rendered_value")) if state == TESTED_VISIBLE else None,
            "score": row.get("score") if state == TESTED_VISIBLE else None,
        }
        if entry["display"] is not None:
            entry["gated"] = False
        out.append(entry)
    return out


def _featured_ratings(rows: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        unblurred = bool(row.get("unblurred"))
        score = row.get("score") if unblurred else None
        out.append(
            {
                "original_id": _str_or_none(usage.get("original_id")),
                "name": usage.get("name"),
                "status": TESTED_VISIBLE if unblurred else TESTED_GATED,
                "score": score,
                "gated": (None if score is None else False) if unblurred else True,
            }
        )
    return out


def _strip(value: Any) -> str | None:
    return strip_html(value)
