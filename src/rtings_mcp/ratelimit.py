"""Rate limiting we own: a token bucket, a concurrency semaphore, and a cross-process
per-host cooldown (SPEC §9).

Why not wafer's limiter: ``wafer/_ratelimit.py`` is a fixed-interval per-hostname sleeper
with no bucket, no lock and no concurrency control, and it waits *before* an attempt but
records *after* the response. It cannot express a burst. Both sessions therefore run
``rate_limit=0.0`` and the shaping happens here.

Why we own ``Retry-After``: under ``max_rotations=0`` wafer *returns* a 429 without
sleeping — it only honours ``Retry-After`` on the rotation path. Ignoring the server's own
"wait this long" and firing again one token later is the single behaviour that turns a soft
limit into a block.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

#: Floor for any cooldown, even when the server asked for less.
COOLDOWN_FLOOR_S = 30.0
#: Ceiling. It bounds ``Retry-After`` itself, not only the doubling — a hostile or
#: mistaken ``Retry-After: 3600`` must not freeze the server for an hour. wafer clamps to
#: the request deadline; we have no such backstop.
COOLDOWN_CAP_S = 600.0


class TokenBucket:
    """Capacity ``burst``, refilling one token per ``interval`` seconds, with jitter.

    The burst buys interactive latency, not throughput: a cold multi-request tool call
    stops paying the full serial interval, while the sustained rate stays the refill.
    """

    __slots__ = ("_capacity", "_interval", "_jitter", "_lock", "_tokens", "_updated")

    def __init__(self, *, capacity: int, interval: float, jitter: float = 0.25) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if interval < 0:
            raise ValueError("interval must be >= 0")
        self._capacity = float(capacity)
        self._interval = float(interval)
        self._jitter = float(jitter)
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        if self._interval <= 0:
            self._tokens = self._capacity
        else:
            elapsed = now - self._updated
            if elapsed > 0:
                self._tokens = min(self._capacity, self._tokens + elapsed / self._interval)
        self._updated = now

    async def acquire(self) -> float:
        """Take one token, sleeping until one exists. Returns the seconds waited."""
        waited = 0.0
        while True:
            async with self._lock:
                self._refill(time.monotonic())
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    jitter = random.uniform(0.0, self._jitter) if self._jitter > 0 else 0.0
                    break
                # interval == 0 cannot reach here: _refill fills the bucket outright.
                wait = max((1.0 - self._tokens) * self._interval, 0.001)
            # Sleep outside the lock so concurrent waiters are not serialized behind it.
            await asyncio.sleep(wait)
            waited += wait
        if jitter > 0:
            await asyncio.sleep(jitter)
            waited += jitter
        return waited

    @property
    def tokens(self) -> float:
        """Current token count, refilled to now. Diagnostics only."""
        self._refill(time.monotonic())
        return self._tokens


@dataclass(slots=True, frozen=True)
class CooldownState:
    """A host's current cooldown, as read from disk."""

    until: float  # wall-clock epoch seconds
    level: int  # consecutive-event counter driving the doubling
    reason: str

    @property
    def remaining(self) -> float:
        return max(0.0, self.until - time.time())

    @property
    def active(self) -> bool:
        return self.remaining > 0


class HostCooldown:
    """A per-host cooldown persisted under the cache dir so a second MCP process respects it.

    Read and written **lock-free via atomic replace** (SPEC §8): it is one small file, and a
    lost update costs one extra request, never correctness. Taking a lock here would let a
    process hold the cooldown lock while waiting on a key lock — and another do the reverse
    — which is a textbook deadlock.

    The doubling counter is persisted too. Without it a second process restarts the ladder
    at the 30 s floor and hammers a host that has already escalated.
    """

    __slots__ = ("_dir",)

    def __init__(self, cooldown_dir: Path) -> None:
        self._dir = cooldown_dir

    def _path(self, host: str) -> Path:
        safe = "".join(c if (c.isalnum() or c in "-._") else "_" for c in host)[:100]
        return self._dir / f"{safe}.json"

    def read(self, host: str) -> CooldownState | None:
        path = self._path(host)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        try:
            state = CooldownState(
                until=float(raw["until"]),
                level=int(raw.get("level", 0)),
                reason=str(raw.get("reason", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        # An expired record keeps its level for a short grace window so consecutive events
        # keep escalating rather than restarting the ladder at the floor each time.
        if state.until + COOLDOWN_CAP_S < time.time():
            return None
        return state

    def remaining(self, host: str) -> float:
        state = self.read(host)
        return state.remaining if state else 0.0

    def record_backoff(
        self, host: str, *, retry_after: float | None, reason: str
    ) -> CooldownState:
        """Install (or escalate) a cooldown after a 429 / Retry-After 503 / challenge."""
        prior = self.read(host)
        level = (prior.level + 1) if prior else 0
        base = max(retry_after or 0.0, COOLDOWN_FLOOR_S)
        delay = min(base * (2**level), COOLDOWN_CAP_S)
        state = CooldownState(until=time.time() + delay, level=level, reason=reason)
        self._write(host, state)
        return state

    def clear(self, host: str) -> None:
        """Called on the next successful 200. Removes the record and the ladder with it."""
        path = self._path(host)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _write(self, host: str, state: CooldownState) -> None:
        path = self._path(host)
        payload = json.dumps(
            {"until": state.until, "level": state.level, "reason": state.reason}
        ).encode("utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-cooldown-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError:
            # A cooldown we cannot persist still applies in-process via the caller's own
            # error handling; losing the file costs politeness, never correctness.
            pass
