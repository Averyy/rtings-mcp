"""The shaping we own: the token bucket and the cross-process cooldown."""

from __future__ import annotations

import asyncio
import time

import pytest

from rtings_mcp.ratelimit import (
    COOLDOWN_CAP_S,
    COOLDOWN_FLOOR_S,
    HostCooldown,
    TokenBucket,
)


async def test_burst_is_free_then_the_refill_rate_applies():
    """The burst buys interactive latency, not throughput."""
    bucket = TokenBucket(capacity=5, interval=0.05, jitter=0.0)
    started = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    assert time.monotonic() - started < 0.03, "the initial burst should not sleep"

    started = time.monotonic()
    await bucket.acquire()
    assert time.monotonic() - started >= 0.04, "the 6th call waits for a refill"


async def test_tokens_refill_over_time():
    bucket = TokenBucket(capacity=3, interval=0.02, jitter=0.0)
    for _ in range(3):
        await bucket.acquire()
    assert bucket.tokens < 1.0
    await asyncio.sleep(0.07)
    assert bucket.tokens >= 2.0


async def test_zero_interval_never_blocks():
    bucket = TokenBucket(capacity=1, interval=0.0, jitter=0.0)
    started = time.monotonic()
    for _ in range(20):
        await bucket.acquire()
    assert time.monotonic() - started < 0.2


async def test_concurrent_waiters_are_all_served():
    bucket = TokenBucket(capacity=2, interval=0.01, jitter=0.0)
    results = await asyncio.wait_for(
        asyncio.gather(*(bucket.acquire() for _ in range(8))), timeout=5
    )
    assert len(results) == 8


@pytest.mark.parametrize("bad", [(0, 1.0), (-1, 1.0)])
def test_invalid_capacity_raises(bad):
    with pytest.raises(ValueError):
        TokenBucket(capacity=bad[0], interval=bad[1])


# -- cooldown ---------------------------------------------------------------------


def test_floor_applies_even_when_the_server_asks_for_less(tmp_path):
    cooldown = HostCooldown(tmp_path / "cd")
    state = cooldown.record_backoff("www.rtings.com", retry_after=1.0, reason="http_429")
    assert state.remaining >= COOLDOWN_FLOOR_S - 1


def test_cap_bounds_retry_after_itself_not_only_the_doubling(tmp_path):
    """A hostile or mistaken `Retry-After: 3600` must not freeze the server for an hour."""
    cooldown = HostCooldown(tmp_path / "cd")
    state = cooldown.record_backoff("www.rtings.com", retry_after=3600.0, reason="http_429")
    assert state.remaining <= COOLDOWN_CAP_S + 1


def test_consecutive_events_double_and_persist_the_counter(tmp_path):
    """Without persisting the counter a second process restarts the ladder at the floor."""
    first = HostCooldown(tmp_path / "cd")
    first.record_backoff("h", retry_after=None, reason="a")
    second = HostCooldown(tmp_path / "cd")  # a different process
    state = second.record_backoff("h", retry_after=None, reason="b")
    assert state.level == 1
    assert state.remaining > COOLDOWN_FLOOR_S


def test_cleared_on_the_next_success(tmp_path):
    cooldown = HostCooldown(tmp_path / "cd")
    cooldown.record_backoff("h", retry_after=None, reason="a")
    assert cooldown.remaining("h") > 0
    cooldown.clear("h")
    assert cooldown.remaining("h") == 0
    assert cooldown.read("h") is None


def test_cross_process_visibility(tmp_path):
    HostCooldown(tmp_path / "cd").record_backoff("h", retry_after=None, reason="a")
    assert HostCooldown(tmp_path / "cd").remaining("h") > 0


def test_unreadable_record_is_no_cooldown(tmp_path):
    cooldown = HostCooldown(tmp_path / "cd")
    cooldown.record_backoff("h", retry_after=None, reason="a")
    (tmp_path / "cd" / "h.json").write_text("{ broken", encoding="utf-8")
    assert cooldown.remaining("h") == 0


def test_hostnames_are_sanitized_into_a_filename(tmp_path):
    cooldown = HostCooldown(tmp_path / "cd")
    cooldown.record_backoff("../../evil", retry_after=None, reason="a")
    written = list((tmp_path / "cd").glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == tmp_path / "cd"
