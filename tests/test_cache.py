"""Cache correctness: tiering, the per-row merge, pruning, eviction, path safety."""

from __future__ import annotations

import json
import time

import pytest

from rtings_mcp.cache import (
    ANONYMOUS,
    FREE,
    MEMBER,
    Cache,
    Envelope,
    slug_to_key,
    validate_id,
    validate_silo,
    validate_slug,
)
from rtings_mcp.config import load_config
from rtings_mcp.errors import RtingsError


def env(tier=ANONYMOUS, fetched_at=None, *, rows=None, unblurred=False, **over):
    payload = {"rows": rows if rows is not None else []}
    e = Envelope(
        fetched_at=fetched_at if fetched_at is not None else time.time(),
        source_url="https://www.rtings.com/tv/tools/table",
        cache_tier=tier,
        request={},
        payload=payload,
        notes={"has_unblurred_insider": unblurred},
    )
    for key, value in over.items():
        setattr(e, key, value)
    return e


# -- path validation --------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../etc", "1/2", "abc", "", "12345678901", "-1"])
def test_ids_are_validated_before_a_path_is_built(bad):
    with pytest.raises(RtingsError):
        validate_id(bad, what="bench_id")


@pytest.mark.parametrize("bad", ["../tv", "TV/", "tv/../..", ""])
def test_silo_validation_rejects_traversal(bad):
    with pytest.raises(RtingsError):
        validate_silo(bad)


def test_silo_is_lowercased_so_case_insensitive_filesystems_are_a_non_issue():
    assert validate_silo("TV") == "tv"


def test_multi_segment_slugs_are_allowed_but_flattened():
    """`/tv/reviews/best/by-size/65-inch` is a real list path; a single-segment pattern
    would reject most of them, and a raw `/` would create a directory."""
    assert validate_slug("by-size/65-inch") == "by-size/65-inch"
    assert slug_to_key("by-size/65-inch") == "by-size__65-inch"
    assert "/" not in slug_to_key("by-usage/video-gaming")


def test_slug_traversal_is_refused():
    for bad in ["../../etc", "a/../b", "/absolute", "a" * 200]:
        with pytest.raises(RtingsError):
            validate_slug(bad)


def test_resolve_refuses_to_escape_the_cache_root(cache):
    with pytest.raises(RtingsError):
        cache.resolve("..", "outside.json")


# -- round trip -------------------------------------------------------------------


def test_write_read_round_trip_and_mtime_is_fetched_at(cache):
    envelope = env(fetched_at=1_700_000_000.0)
    path = cache.put(envelope, "silos.json")
    assert abs(path.stat().st_mtime - 1_700_000_000.0) < 1
    back = cache.get("silos.json")
    assert back is not None and back.fetched_at == 1_700_000_000.0


def test_truncated_file_is_a_miss_and_is_unlinked(cache):
    path = cache.put(env(), "silos.json")
    path.write_text("{ not json", encoding="utf-8")
    assert cache.get("silos.json") is None
    assert not path.exists()


def test_stale_means_past_ttl_and_nothing_else():
    fresh = env(fetched_at=time.time())
    old = env(fetched_at=time.time() - 100)
    assert not fresh.is_stale(50)
    assert old.is_stale(50)


def test_gzip_round_trip(cache):
    cache.put_variant(env(rows=[{"product_id": "1"}]), "reviews", key="39008", compress=True)
    got = cache.read_variants("reviews", key="39008")
    assert got and got[0].payload["rows"][0]["product_id"] == "1"


# -- versioned files and tiering --------------------------------------------------


def test_fetched_at_is_in_the_filename_so_a_refetch_appends(cache):
    """One slot per tier makes a same-tier refetch a whole-file overwrite, which would let
    an all-blurred response clobber a good one."""
    cache.put_variant(env(fetched_at=100.0), "tests", "227", key="11")
    cache.put_variant(env(fetched_at=200.0), "tests", "227", key="11")
    variants = cache.list_variants("tests", "227", key="11")
    assert len(variants) == 2
    assert [v.fetched_at for v in variants] == [200.0, 100.0]  # newest first


def test_read_variants_honours_min_tier(cache):
    cache.put_variant(env(ANONYMOUS, 100.0), "tests", "227", key="11")
    cache.put_variant(env(MEMBER, 200.0), "tests", "227", key="11")
    assert len(cache.read_variants("tests", "227", key="11")) == 2
    assert len(cache.read_variants("tests", "227", key="11", min_tier=MEMBER)) == 1


def test_prune_keeps_newest_per_tier_plus_newest_unblurred(cache):
    cache.put_variant(env(ANONYMOUS, 100.0, unblurred=True), "tests", "227", key="11")
    cache.put_variant(env(ANONYMOUS, 200.0), "tests", "227", key="11")
    cache.put_variant(env(ANONYMOUS, 300.0), "tests", "227", key="11")
    cache.put_variant(env(MEMBER, 150.0), "tests", "227", key="11")
    cache.prune_variants("tests", "227", key="11")
    kept = {(v.tier, v.fetched_at) for v in cache.list_variants("tests", "227", key="11")}
    assert (ANONYMOUS, 300.0) in kept  # newest anonymous
    assert (MEMBER, 150.0) in kept  # newest member
    assert (ANONYMOUS, 100.0) in kept  # the only unblurred file survives age-pruning
    assert (ANONYMOUS, 200.0) not in kept


# -- eviction ---------------------------------------------------------------------


def test_newest_unblurred_is_exempt_from_eviction_at_ANY_tier(tmp_path):
    """16 of 28 silos serve insider values unblurred *anonymously*, so almost every
    unblurred file on disk is anonymous-tier. Gating the exemption on member/free would
    protect nothing and let eviction silently downgrade a scored row."""
    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CACHE_MAX_MB": "1"}
    )
    cache = Cache(config)
    big = [{"product_id": str(i), "pad": "x" * 400} for i in range(400)]
    keep = cache.put_variant(
        env(ANONYMOUS, 100.0, rows=big, unblurred=True), "tests", "227", key="11"
    )
    for i in range(12):
        cache.put_variant(
            env(ANONYMOUS, 200.0 + i, rows=big), "tests", "999", key=str(1000 + i)
        )
    # `flush_lru` now enforces the size limit itself — it is the hook every write batch
    # ends with, and `enforce_size_limit` was previously called from nowhere in the serving
    # path, so `RTINGS_CACHE_MAX_MB` had no effect at all.
    written = len(list((cache.root / "tests" / "999").glob("*.json")))
    cache.flush_lru()
    remaining = len(list((cache.root / "tests" / "999").glob("*.json")))
    assert remaining < written, "the size limit must actually evict"
    assert cache.enforce_size_limit() == 0, "a second pass has nothing left to free"
    assert keep.exists(), "the only unblurred copy of a scored row was evicted"


def test_preview_bought_reviews_are_exempt(tmp_path):
    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CACHE_MAX_MB": "1"}
    )
    cache = Cache(config)
    big = [{"product_id": str(i), "pad": "y" * 400} for i in range(400)]
    bought = cache.put_variant(env(FREE, 100.0, rows=big), "reviews", key="39008")
    for i in range(12):
        cache.put_variant(env(ANONYMOUS, 200.0 + i, rows=big), "tests", "227", key=str(i))
    cache.flush_lru()
    cache.enforce_size_limit()
    assert bought.exists(), "a review bought with a metered preview was evicted"


def test_eviction_is_a_noop_under_the_limit(cache):
    cache.put_variant(env(), "tests", "227", key="11")
    assert cache.enforce_size_limit() == 0


# -- generations ------------------------------------------------------------------


def test_generations_are_immutable_and_newest_wins(cache):
    cache.put_generation(env(fetched_at=100.0, payload=[{"id": "1"}]), "catalog", "tv", key="227")
    cache.put_generation(
        env(fetched_at=200.0, payload=[{"id": "1"}, {"id": "2"}]), "catalog", "tv", key="227"
    )
    assert len(cache.list_generations("catalog", "tv", key="227")) == 2
    newest = cache.newest_generation("catalog", "tv", key="227")
    assert len(newest.payload) == 2


def test_prune_generations_keeps_the_newest(cache):
    for i in range(6):
        cache.put_generation(env(fetched_at=100.0 + i), "catalog", "tv", key="227")
    cache.prune_generations("catalog", "tv", key="227", keep=2)
    kept = cache.list_generations("catalog", "tv", key="227")
    assert len(kept) == 2 and kept[0].fetched_at == 105.0


# -- format version ---------------------------------------------------------------


def test_format_version_mismatch_clears_payloads_but_not_the_tree(tmp_path):
    config = load_config({"RTINGS_CACHE_DIR": str(tmp_path / "c")})
    cache = Cache(config)
    cache.put(env(), "silos.json")
    (cache.root / "meta.json").write_text(json.dumps({"format_version": 0}), encoding="utf-8")
    rebuilt = Cache(config)
    assert rebuilt.get("silos.json") is None
