"""Enforcement observations carry their provenance — `data_completeness` is a claim about
what ANONYMOUS gets, so only a signed-out fetch may establish `full`."""

from __future__ import annotations

import time

import pytest

from rtings_mcp.observations import (
    OBSERVATION_FRESH_S,
    PROVENANCE_ANONYMOUS,
    PROVENANCE_LOGGED_IN,
    PROVENANCE_UNKNOWN,
    ObservationStore,
    ScoresAvailable,
)


def scores(*, insider=(), usage=()):
    out = ScoresAvailable(has_insider=True, has_usages=True)
    for unblurred in insider:
        out.insider_tests.add(unblurred=unblurred)
    for unblurred in usage:
        out.usage_ratings.add(unblurred=unblurred)
    return out


FULL = scores(insider=[True] * 20, usage=[True] * 5)
GATED = scores(insider=[False] * 20, usage=[False] * 5)


@pytest.fixture
def store(cache):
    return ObservationStore(cache)


def test_a_logged_in_observation_never_reports_full(store):
    """A member's fetch of tv is 588/588 unblurred. Reported as `full`, this machine tells
    every agent a gated silo is answerable anonymously — and with a stored credential no
    signed-out fetch ever comes along to correct it."""
    store.record("tv", "227", FULL, provenance=PROVENANCE_LOGGED_IN)
    observation = store.read("tv", "227")
    assert observation.provenance == PROVENANCE_LOGGED_IN
    assert observation.completeness == "unknown"
    assert observation.insider_ratio == 1.0, "the counts are kept; only the claim is withheld"


def test_a_logged_in_observation_may_still_report_gated(store):
    """Nothing about a membership ADDS blur, so a fully-blurred reading is true of the silo
    whoever fetched it (a lapsed session on tv, say)."""
    store.record("tv", "227", GATED, provenance=PROVENANCE_LOGGED_IN)
    assert store.read("tv", "227").completeness == "gated"


def test_an_anonymous_observation_reports_as_before(store):
    store.record("mattress", "300", FULL, provenance=PROVENANCE_ANONYMOUS)
    assert store.read("mattress", "300").completeness == "full"
    store.record("tv", "227", GATED, provenance=PROVENANCE_ANONYMOUS)
    assert store.read("tv", "227").completeness == "gated"


def test_a_logged_in_observation_never_replaces_an_anonymous_one(store):
    """Provenance outranks width. The signed-out reading is the ground truth; a wider
    signed-in one would erase it and nothing would ever put it back."""
    store.record("tv", "227", scores(insider=[False] * 5), provenance=PROVENANCE_ANONYMOUS)
    store.record("tv", "227", FULL, provenance=PROVENANCE_LOGGED_IN)
    observation = store.read("tv", "227")
    assert observation.provenance == PROVENANCE_ANONYMOUS
    assert observation.insider_total == 5
    assert observation.completeness == "gated"


def test_an_anonymous_observation_replaces_a_logged_in_one_at_any_width(store):
    """A one-row signed-out reading says more about anonymous than a 500-row signed-in one."""
    store.record("tv", "227", FULL, provenance=PROVENANCE_LOGGED_IN)
    store.record("tv", "227", scores(insider=[False]), provenance=PROVENANCE_ANONYMOUS)
    observation = store.read("tv", "227")
    assert observation.provenance == PROVENANCE_ANONYMOUS
    assert observation.insider_total == 1
    assert observation.completeness == "gated"


def test_width_still_decides_between_two_observations_of_the_same_provenance(store):
    store.record("tv", "227", FULL, provenance=PROVENANCE_LOGGED_IN)
    store.record("tv", "227", scores(insider=[False]), provenance=PROVENANCE_LOGGED_IN)
    assert store.read("tv", "227").insider_total == 20, "a narrow read must not erase a wide one"


def test_a_legacy_untagged_entry_is_trusted_for_nothing_and_replaced(store, cache):
    """An entry written before provenance existed may already be a member's reading on a
    machine that had a credential. It reports `unknown`, cannot serve as proof, and yields
    to the next tagged observation however narrow."""
    from rtings_mcp.cache import ANONYMOUS, Envelope

    legacy = {
        "227": {
            "insider_total": 500,
            "insider_unblurred": 500,
            "public_total": 0,
            "public_unblurred": 0,
            "usage_total": 0,
            "usage_unblurred": 0,
            "observed_at": time.time(),
        }
    }
    cache.put(
        Envelope(fetched_at=time.time(), source_url="", cache_tier=ANONYMOUS, payload=legacy),
        "observed",
        "tv.json",
    )
    observation = store.read("tv", "227")
    assert observation.provenance == PROVENANCE_UNKNOWN
    assert observation.completeness == "unknown"
    assert store.anonymous_serves("tv", "227", surface="tests") is False

    store.record("tv", "227", scores(insider=[False]), provenance=PROVENANCE_ANONYMOUS)
    assert store.read("tv", "227").insider_total == 1


def test_anonymous_serves_needs_a_fresh_full_signed_out_reading(store, cache, monkeypatch):
    """The proof the write guard keys on. Only a signed-out, fully-unblurred, fresh reading
    of the surface in question counts — a stale one is no proof, because enforcement flips
    silently and a signed-in machine never refreshes this reading on its own."""
    assert store.anonymous_serves("mattress", "300", surface="tests") is False  # nothing yet

    store.record("mattress", "300", FULL, provenance=PROVENANCE_LOGGED_IN)
    assert store.anonymous_serves("mattress", "300", surface="tests") is False

    store.record("mattress", "300", FULL, provenance=PROVENANCE_ANONYMOUS)
    assert store.anonymous_serves("mattress", "300", surface="tests") is True
    assert store.anonymous_serves("mattress", "300", surface="reviews") is True
    assert store.anonymous_serves("mattress", "300", surface="ratings") is True
    assert store.anonymous_serves("mattress", "300", surface="verdicts") is True
    assert store.anonymous_serves("mattress", "301", surface="tests") is False  # other bench

    later = time.time() + OBSERVATION_FRESH_S + 60
    monkeypatch.setattr("rtings_mcp.observations.time.time", lambda: later)
    assert store.anonymous_serves("mattress", "300", surface="tests") is False


def test_anonymous_serves_reads_the_surface_that_was_asked_about(store):
    """Insider tests open but usage scores withheld (or unobserved) must not prove the
    usage surface, and vice versa."""
    store.record(
        "x", "1", scores(insider=[True] * 3, usage=[False] * 2), provenance=PROVENANCE_ANONYMOUS
    )
    assert store.anonymous_serves("x", "1", surface="tests") is True
    assert store.anonymous_serves("x", "1", surface="ratings") is False
    store.record("y", "1", scores(insider=[True, False]), provenance=PROVENANCE_ANONYMOUS)
    assert store.anonymous_serves("y", "1", surface="tests") is False, "partial is no proof"
    store.record("z", "1", scores(usage=[True]), provenance=PROVENANCE_ANONYMOUS)
    assert store.anonymous_serves("z", "1", surface="ratings") is True
    assert store.anonymous_serves("z", "1", surface="tests") is False, "no insider rows seen"


def test_read_without_a_bench_prefers_the_entry_that_says_something(store):
    """A signed-in `unknown` on the newest bench must not hide a signed-out `full` on
    another — the silo IS measured as open."""
    store.record("m", "1", FULL, provenance=PROVENANCE_ANONYMOUS)
    store.record("m", "2", FULL, provenance=PROVENANCE_LOGGED_IN)  # newer, says nothing
    chosen = store.read("m")
    assert chosen.bench_id == "1"
    assert chosen.completeness == "full"


def test_provenance_is_required_and_validated(store):
    """Defaulting it is how the defect happened: getting it wrong is silent."""
    with pytest.raises(TypeError):
        store.record("tv", "227", FULL)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        store.record("tv", "227", FULL, provenance="member")
