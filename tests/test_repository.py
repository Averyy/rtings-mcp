"""The per-row tier merge and the coverage rules — read-side only, no network."""

from __future__ import annotations

import pytest

from rtings_mcp.auth import AuthManager
from rtings_mcp.cache import ANONYMOUS, MEMBER, Cache, Envelope
from rtings_mcp.config import load_config
from rtings_mcp.http import Transport
from rtings_mcp.repository import Repository


class ExplodingTransport(Transport):
    """Proves a read never touches the network."""

    async def api_post(self, *args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("a cache read hit the network")

    async def api_get_html(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("a cache read hit the network")


@pytest.fixture
def repo(tmp_path):
    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CONFIG_DIR": str(tmp_path / "cfg")}
    )
    cache = Cache(config)
    transport = ExplodingTransport(config)
    auth = AuthManager(config=config, cache=cache, transport=transport)
    return Repository(config, cache, transport, auth)


def slice_env(tier, fetched_at, rows, *, product_ids=None, unpublished=None):
    return Envelope(
        fetched_at=fetched_at,
        source_url="https://www.rtings.com/tv/tools/table",
        cache_tier=tier,
        request={},
        payload={"rows": rows},
        catalog_generation="227:1",
        product_ids=product_ids if product_ids is not None else [r["product_id"] for r in rows],
        unpublished_product_ids=unpublished or [],
        bench_id="227",
        silo="tv",
    )


def row(product_id, unblurred, value=None):
    return {
        "original_id": "11",
        "product_id": product_id,
        "status": "tested",
        "unblurred": unblurred,
        "value": value,
    }


# -- the per-row merge ------------------------------------------------------------


def test_an_unblurred_row_wins_over_a_newer_blurred_one(repo):
    """Never-downgrade lives on the row's own `unblurred` bit, not on a file label."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [row("1", True, "1200")]), "tests", "227", key="11"
    )
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 200.0, [row("1", False)]), "tests", "227", key="11"
    )
    merged = repo.read_slice("tests", "227", "11", demand=ANONYMOUS)
    assert merged["1"].row["value"] == "1200"
    assert merged["1"].superseded_at == 200.0


def test_a_retained_lower_tier_unblurred_row_is_not_dropped_by_the_demand_tier(repo):
    """`demand` is the hit rule's job. Filtering the merge by it would discard the only
    unblurred value for a product the higher-tier response did not cover."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [row("1", True, "1200"), row("2", False)]),
        "tests",
        "227",
        key="11",
    )
    repo.cache.put_variant(
        slice_env(MEMBER, 200.0, [row("2", True, "900")]), "tests", "227", key="11"
    )
    merged = repo.read_slice("tests", "227", "11", demand=MEMBER)
    assert merged["1"].row["value"] == "1200"  # from the older anonymous file
    assert merged["2"].row["value"] == "900"


def test_superseded_at_is_per_row_not_per_file(repo):
    """Stamping it from the freshest file overall would claim a fresher observation of a
    row that the fresher file never covered."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [row("1", True, "1200")]), "tests", "227", key="11"
    )
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 200.0, [row("2", True, "900")]), "tests", "227", key="11"
    )
    merged = repo.read_slice("tests", "227", "11", demand=ANONYMOUS)
    assert merged["1"].superseded_at is None  # no newer file covers product 1
    assert merged["2"].superseded_at is None


def test_a_written_but_empty_slice_is_an_answer_not_a_miss(repo):
    """Zero rows is real `not_tested` for every product on that bench."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [], product_ids=["1", "2"]), "tests", "227", key="11"
    )
    merged = repo.read_slice("tests", "227", "11", demand=ANONYMOUS)
    assert merged == {}
    assert merged is not None


def test_a_never_fetched_slice_is_a_miss(repo):
    assert repo.read_slice("tests", "227", "999", demand=ANONYMOUS) is None


# -- coverage ---------------------------------------------------------------------


def test_the_slice_carries_the_generation_membership_not_a_pointer(repo):
    """A bare generation id dangles once the 3-day catalog TTL overwrites it."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [row("1", True, "1200")], product_ids=["1", "2"]),
        "tests",
        "227",
        key="11",
    )
    meta = repo.slice_meta("tests", "227", "11", demand=ANONYMOUS)
    assert set(meta.product_ids) == {"1", "2"}
    assert "3" not in set(meta.product_ids)  # a newer product is NOT covered


def test_slice_meta_prefers_the_demanded_tier(repo):
    repo.cache.put_variant(slice_env(ANONYMOUS, 100.0, []), "tests", "227", key="11")
    repo.cache.put_variant(slice_env(MEMBER, 50.0, []), "tests", "227", key="11")
    assert repo.slice_meta("tests", "227", "11", demand=MEMBER).cache_tier == MEMBER
    assert repo.slice_meta("tests", "227", "11", demand=ANONYMOUS).cache_tier == ANONYMOUS


def test_unpublished_ids_ride_on_the_slice_not_the_live_catalog(repo):
    """Read from the current catalog instead, a review published on day 4 would make a
    day-1 blurred slice read as 'buy a membership'."""
    repo.cache.put_variant(
        slice_env(ANONYMOUS, 100.0, [row("1", False)], unpublished=["1"]),
        "tests",
        "227",
        key="11",
    )
    meta = repo.slice_meta("tests", "227", "11", demand=ANONYMOUS)
    assert meta.unpublished_product_ids == ["1"]


# -- TTL ---------------------------------------------------------------------------


def test_current_bench_gets_the_shorter_ttl(repo):
    """New products land on the current bench, so its slices expire sooner — the named
    mitigation for the coverage hole that has no reliable signal."""
    eight_days = 8 * 86_400
    old = slice_env(ANONYMOUS, __import__("time").time() - eight_days, [])
    assert not repo._slice_is_fresh(old, current_bench_id="227", bench_id="227")
    assert repo._slice_is_fresh(old, current_bench_id="227", bench_id="2")


def test_unknown_current_bench_does_not_silently_extend_the_ttl(repo):
    """With no current bench known, every bench is treated as legacy — so this asserts the
    caller passes one rather than relying on a memo that may be cold."""
    eight_days = 8 * 86_400
    old = slice_env(ANONYMOUS, __import__("time").time() - eight_days, [])
    assert repo._slice_is_fresh(old, current_bench_id=None, bench_id="227")


# -- warnings are per call, not per process ----------------------------------------


def test_warnings_do_not_leak_between_tool_calls(repo):
    """The repository is built once and shared, so a plain instance list would attach a
    catalog warning from one call to an unrelated call an hour later — and grow forever."""
    with repo.warning_scope():
        repo.warn("first call")
        assert repo.warnings == ["first call"]
    with repo.warning_scope():
        assert repo.warnings == []
        repo.warn("second call")
        assert repo.warnings == ["second call"]
    assert repo.warnings == []


def test_warnings_are_deduplicated_within_a_call(repo):
    with repo.warning_scope():
        repo.warn("same")
        repo.warn("same")
        assert repo.warnings == ["same"]


def test_warning_outside_a_scope_is_logged_not_dropped(repo, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="rtings_mcp.repository"):
        repo.warn("no scope open")
    assert "no scope open" in caplog.text
    assert repo.warnings == []


async def test_concurrent_calls_do_not_see_each_others_warnings(repo):
    """Tool bodies interleave even at RTINGS_CONCURRENCY=1 — that only bounds in-flight
    requests, not coroutines."""
    import asyncio

    async def call(name):
        with repo.warning_scope():
            repo.warn(name)
            await asyncio.sleep(0)
            return repo.warnings

    a, b = await asyncio.gather(call("a"), call("b"))
    assert a == ["a"]
    assert b == ["b"]


def test_the_release_hint_has_no_duplicates_and_is_lowercase():
    from rtings_mcp.config import KNOWN_SILOS, SILO_HINT_SET

    assert len(KNOWN_SILOS) == len(SILO_HINT_SET), "duplicate silo in the hint"
    assert all(s == s.lower().strip() for s in KNOWN_SILOS)
