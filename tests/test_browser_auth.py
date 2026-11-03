"""The sign-in window's teardown: budgets, the kill of last resort, and abandoning a hung step.

No browser is launched. The context, browser and kill are fakes; what runs is the shipped
`_close_browser` / `_await_bounded`. Measured behaviour these encode (2026-11-02, macOS,
installed Chrome, Playwright 1.62.0-1.64.1): Chrome's main process usually exits ~0.4 s after
`browser.close()`, but about one exit in three or four takes 1.4-5.1 s, on every version and
whatever the page was doing. A 5.0 s budget turned that ordinary tail into a SIGKILL.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rtings_mcp import auth_tools, browser_auth

pytestmark = pytest.mark.asyncio

#: The slowest Chrome exit measured after `browser.close()` (2026-11-02, 40-odd closes).
SLOWEST_MEASURED_EXIT_S = 5.08


class FakeContext:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.closed = False

    async def close(self):
        await asyncio.sleep(self.delay)
        self.closed = True


class FakeBrowser(FakeContext):
    pass


@pytest.fixture
def kills(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(browser_auth, "kill_browser", lambda pid, **kw: killed.append(pid))
    return killed


async def test_the_close_budget_clears_chromes_slowest_measured_exit():
    """With room to spare, and still inside what shutdown's cancel waits for: a budget past
    CANCEL_WAIT_S would have `cancel()` give up while the window is still closing."""
    assert browser_auth.CLOSE_TIMEOUT_S >= 1.5 * SLOWEST_MEASURED_EXIT_S, (
        "a slow but healthy Chrome exit must not be SIGKILLed; re-measure before lowering this"
    )
    whole_teardown = (
        browser_auth.CLOSE_TIMEOUT_S
        + 2 * browser_auth.ABANDON_GRACE_S
        + browser_auth.DRIVER_STOP_TIMEOUT_S
    )
    assert whole_teardown < auth_tools.CANCEL_WAIT_S


async def test_a_close_that_finishes_inside_the_budget_is_never_killed(kills):
    context, browser = FakeContext(), FakeBrowser(delay=0.3)  # a "slow" exit, scaled down
    await browser_auth._close_browser(context, browser, 4242, time.monotonic() + 1.0)
    assert context.closed and browser.closed
    assert kills == []


async def test_a_close_that_overruns_the_budget_is_killed_by_pid(kills):
    context, browser = FakeContext(), FakeBrowser(delay=5.0)
    started = time.monotonic()
    await browser_auth._close_browser(context, browser, 4242, time.monotonic() + 0.3)
    assert kills == [4242]
    assert time.monotonic() - started < 1.5, "an overrun is abandoned, never awaited"


async def test_a_slow_context_close_spends_the_budget_the_browser_close_shares(kills):
    """One budget for both steps: a context close that eats it leaves the browser close none,
    and the kill fires rather than the teardown running twice as long as promised."""
    context, browser = FakeContext(delay=5.0), FakeBrowser()
    await browser_auth._close_browser(context, browser, 4242, time.monotonic() + 0.3)
    assert kills == [4242]


async def test_no_pid_means_no_kill_even_on_an_overrun(kills):
    """`browser_pid` returns None when CDP cannot say; killing a guessed pid is never safe."""
    await browser_auth._close_browser(
        FakeContext(), FakeBrowser(delay=5.0), None, time.monotonic() + 0.2
    )
    assert kills == []


async def test_a_step_that_swallows_its_first_cancellation_is_still_abandoned():
    """Why `_await_bounded` is not `wait_for`: a Playwright call absorbs the first cancel, and
    `wait_for` then waits on it for ever."""

    async def stubborn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(10)  # absorbs one cancel, as a Playwright call can

    started = time.monotonic()
    assert await browser_auth._await_bounded(stubborn(), time.monotonic() + 0.2) is False
    assert time.monotonic() - started < 1.0
