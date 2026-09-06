"""`auth --browser` and `rt_sign_in`: the optional ``[browser]`` extra.

Open an installed Chromium-family browser on RTINGS' own sign-in page in a fresh, throwaway
context that does not advertise the automation, then wait for the session to go logged-in and
read the cookie. This module never reads, fills or submits any form field, never sees a
password, and is never on the data path. The human types; the server watches one boolean.

**This is what makes `_rtings_session` capturable at all.** The cookie is ``HttpOnly``, so
`document.cookie` cannot see it and a console fallback does not exist — which is why the paste
path is "Copy as cURL" and nothing lighter. Playwright's `context.cookies()` reads the jar
itself, HttpOnly included, so the browser path needs no gesture from the user beyond signing in.

**The ready signal is NOT "a cookie appeared"** — the trap that would make this flow capture an
anonymous session and call it a member one. RTINGS mints `_rtings_session` for anonymous
requests too (`RECON.md` §5) and re-issues it on *every* response (§12.15), so both "the cookie
exists" and "the cookie changed" are true within a second of opening the page, logged in or not.
The only honest signal is the one the server-side probe already uses: `GLOBALS.session
.current_user` going non-null. It is read from the page, and the captured cookie is still
validated against RTINGS afterwards before anything is stored.

The teardown is ported from `consumer-reports-mcp`'s `browser_auth` unchanged in substance,
including the reasons: the window must close on EVERY exit path, and closing it must never hang
the process. `context.close()`/`browser.close()` wait for a reply from Playwright's driver and
carry no timeout of their own; when the loop is shutting down, `asyncio.run` cancels every task
in one sweep, the driver's pipe reader included, so that reply can never be read and an
unbounded close deadlocks the interpreter with the window still open. Hence the bounded teardown
plus a pid kill of last resort, and an `atexit` reaper for a loop torn down mid-capture.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

from .config import SESSION_COOKIE_NAME

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.rtings.com/login"
TOKEN_COOKIE = SESSION_COOKIE_NAME
TOKEN_DOMAIN = "rtings.com"  # the cookie's domain must be this or a subdomain of it

#: Whether the page's own session says a user is signed in. This is the browser-side twin of
#: `htmlprobe.classify_session`, which reads the same field out of server-rendered HTML: `null`
#: is anonymous, an object is logged in. Wrapped in a try so a page mid-navigation (or one that
#: has not defined GLOBALS yet) is "not yet", never an error.
READY_SCRIPT = """
() => {
  try {
    const s = window.GLOBALS && window.GLOBALS.session;
    return !!(s && s.current_user);
  } catch (e) {
    return false;
  }
}
"""

INSTALL_COMMAND = 'uv sync --extra browser  (or: pip install "rtings-mcp[browser]")'
INSTALL_HINT = (
    "the browser extra is not installed. Install it with\n"
    '    uv sync --extra browser        (or: pip install "rtings-mcp[browser]")\n'
    "or use the paste path instead: `rtings-mcp auth` and follow the prompt."
)

# The launch ladder. Playwright `channel`s resolve the browser from its known install locations
# (not PATH), so they work under a Desktop-launched process with a minimal PATH; the executable
# names are the Linux/PATH fallback. Nothing is ever downloaded.
CHANNELS = ("chrome", "msedge")
EXECUTABLE_NAMES = ("google-chrome", "chromium", "chromium-browser", "brave-browser")

# The window must not advertise the automation. A Chromium under Playwright enables the
# `AutomationControlled` blink feature, so every page reads `navigator.webdriver === true`, and
# RTINGS' login page loads reCAPTCHA. Whether it keys on that bit here is unmeasured; the
# equivalent was measured on Consumer Reports' form, where `webdriver: true` produced a visible
# puzzle and a rejected sign-in for credentials that work in a plain window. The setting costs
# nothing and removes the question, so it is on. These are Playwright's own options for a
# human-driven window.
LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled",)
IGNORE_DEFAULT_ARGS = ("--enable-automation",)
# Playwright's default context emulates a 1280x720 `screen` equal to the viewport with no menu
# bar, a shape no real desktop has. `no_viewport` lets the page see the window's real size.
# Still no `storage_state` and no profile: the context is throwaway, so the window starts signed
# out and nothing of the user's own browsing is touched.
CONTEXT_OPTIONS: Mapping[str, Any] = MappingProxyType({"no_viewport": True})

CLOSE_TIMEOUT_S = 5.0
DRIVER_STOP_TIMEOUT_S = 3.0
ABANDON_GRACE_S = 0.2
PROFILE_MARKER = "--user-data-dir="
PLAYWRIGHT_MARKER = "playwright"


class BrowserExtraMissing(RuntimeError):
    pass


class BrowserNotFound(RuntimeError):
    pass


class CaptureTimeout(RuntimeError):
    pass


class WindowClosed(CaptureTimeout):
    """The user closed the window before signing in: a `CaptureTimeout` to every existing
    caller, distinguishable for the ones that want to say so."""


def extra_installed() -> bool:
    """Whether Playwright is importable, without importing it (no driver process, no cost)."""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def _import_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserExtraMissing(INSTALL_HINT) from exc
    return async_playwright


async def load_playwright() -> Any:
    """`async_playwright`, imported on a worker thread so the loop keeps serving tool calls."""
    return await asyncio.to_thread(_import_playwright)


def launch_plan(which: Callable[[str], str | None] | None = None) -> Iterator[tuple[str, dict]]:
    """(label, launch kwargs) in the order to try. `_launch` adds `headless=False` (headed is
    not a preference: the user has to type), `args` and `ignore_default_args`, so a rung must
    NOT yield those three keys."""
    which = which or shutil.which
    for channel in CHANNELS:
        yield channel, {"channel": channel}
    for name in EXECUTABLE_NAMES:
        path = which(name)
        if path:
            yield name, {"executable_path": path}


async def _launch(pw: Any) -> tuple[Any, str]:
    failures: list[str] = []
    for label, kwargs in launch_plan():
        try:
            browser = await pw.chromium.launch(
                headless=False,
                args=list(LAUNCH_ARGS),
                ignore_default_args=list(IGNORE_DEFAULT_ARGS),
                **kwargs,
            )
        except Exception as exc:  # not installed, or not launchable: try the next rung
            failures.append(f"{label}: {type(exc).__name__}")
            continue
        return browser, label
    raise BrowserNotFound(
        "no installed Chrome, Edge or Chromium-family browser could be launched (nothing is "
        "downloaded). Install Chrome, or use the paste path: `rtings-mcp auth`. "
        f"Tried: {', '.join(failures) or 'nothing'}."
    )


# --------------------------------------------------------------------------- kill of last resort


async def browser_pid(browser: Any) -> int | None:
    """The browser process id, from a browser-level CDP session. None when unavailable, which
    turns the kill of last resort off and leaves only the graceful path."""
    try:
        cdp = await browser.new_browser_cdp_session()
        try:
            info = await cdp.send("SystemInfo.getProcessInfo")
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()
        for proc in info.get("processInfo", []):
            if proc.get("type") == "browser":
                return int(proc["id"])
    except Exception as exc:
        log.debug("browser pid unavailable (%s)", type(exc).__name__)
    return None


def _command_line_argv(pid: int) -> list[str]:
    if sys.platform == "win32":
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine",
        ]
    return ["ps", "-o", "command=", "-p", str(pid)]


def _command_line(pid: int) -> str | None:
    """The process's command line, None when it cannot be known, and unknown means no kill."""
    try:
        out = subprocess.run(_command_line_argv(pid), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout


def kill_browser(
    pid: int,
    *,
    command_line: Callable[[int], str | None] = _command_line,
    kill: Callable[[int, int], None] = os.kill,
) -> bool:
    """SIGTERM the browser process, but only while `pid` is still a Playwright-profiled browser
    (the guard against a recycled pid). Returns whether a signal was sent."""
    cmd = command_line(pid) or ""
    if PROFILE_MARKER not in cmd or PLAYWRIGHT_MARKER not in cmd.lower():
        return False
    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except OSError as exc:
        log.debug("could not terminate browser pid %d (%s)", pid, type(exc).__name__)
        return False
    log.warning("sign-in: the browser window did not close on its own; terminated pid %d", pid)
    return True


_pending_browsers: set[int] = set()  # launched, not yet confirmed closed
_reaper_armed = False


def _arm_reaper(pid: int) -> None:
    global _reaper_armed
    _pending_browsers.add(pid)
    if not _reaper_armed:
        atexit.register(reap_pending_browsers)
        _reaper_armed = True


def _disarm_reaper(pid: int) -> None:
    _pending_browsers.discard(pid)


def pending_browsers() -> frozenset[int]:
    return frozenset(_pending_browsers)


def reap_pending_browsers() -> None:
    """`atexit`: a loop torn down under a capture never reaches the capture's `finally`, so
    every browser still pending is terminated here. Idempotent."""
    for pid in list(_pending_browsers):
        _pending_browsers.discard(pid)
        kill_browser(pid)


def _retrieve(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # an abandoned step's error is not news


async def _await_bounded(aw: Awaitable[Any], deadline: float) -> bool:
    """Run `aw` as its own task and wait until `deadline`; False on timeout or any exception,
    never raises either. Only cancellation of the awaiting task itself propagates.

    NOT `wait_for`: on timeout `wait_for` cancels the awaitable and then WAITS for it, and a
    Playwright call absorbs that first cancellation, so with the driver's reader gone it hangs
    exactly as before. A step that overruns is abandoned, not awaited.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        if asyncio.iscoroutine(aw):
            aw.close()
        return False
    task = asyncio.ensure_future(aw)
    task.add_done_callback(_retrieve)
    done, _ = await asyncio.wait({task}, timeout=remaining)
    if task in done:
        if task.cancelled() or task.exception() is not None:
            log.debug("browser teardown step failed: %s", task.exception() or "cancelled")
            return False
        return True
    log.debug("browser teardown step overran its budget; abandoning it")
    task.cancel()
    task.cancel()
    await asyncio.wait({task}, timeout=ABANDON_GRACE_S)
    return False


async def _close_browser(context: Any, browser: Any, pid: int | None, deadline: float) -> None:
    """Close the window: context, then browser, within the shared budget. What the graceful path
    does not confirm closed is terminated by pid, from a `finally` so a second cancellation
    mid-teardown cannot skip it."""
    closed = False
    try:
        closed = context is None or await _await_bounded(context.close(), deadline)
        closed = await _await_bounded(browser.close(), deadline) and closed
    finally:
        if pid is not None:
            if not closed:
                kill_browser(pid)
            _disarm_reaper(pid)


def session_cookie(cookies: list[dict]) -> str | None:
    """The `_rtings_session` value from a jar dump, or None.

    `context.cookies()` is every cookie in the window, so the name alone is not enough: the
    domain has to be rtings.com or below it.
    """
    for cookie in cookies:
        if cookie.get("name") != TOKEN_COOKIE:
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain == TOKEN_DOMAIN or domain.endswith("." + TOKEN_DOMAIN):
            value = str(cookie.get("value") or "")
            if value:
                return value
    return None


async def capture_session(
    timeout_s: int = 300,
    *,
    playwright_factory: Any | None = None,
    poll_s: float = 1.0,
    on_launch: Callable[[str], None] | None = None,
) -> str:
    """Return the `_rtings_session` cookie once the page reports a signed-in user, or raise.

    `on_launch(label)` is called as soon as a window is up, so a caller that must answer within
    seconds (`rt_sign_in`) can say which browser opened without waiting for the human.
    """
    if playwright_factory is None:
        playwright_factory = await load_playwright()
    driver = playwright_factory()
    pw = await driver.__aenter__()
    try:
        browser, label = await _launch(pw)
        pid = await browser_pid(browser)
        if pid is not None:
            _arm_reaper(pid)
        context = None
        try:
            if on_launch is not None:
                on_launch(label)
            # fresh and throwaway: never a real profile, never a storage_state, so the window
            # starts signed out and the user's own browser session is untouched
            context = await browser.new_context(**CONTEXT_OPTIONS)
            page = await context.new_page()
            await page.goto(LOGIN_URL)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                try:
                    signed_in = bool(await page.evaluate(READY_SCRIPT))
                except Exception as exc:
                    # A page mid-navigation raises here, and login navigates. Distinguish that
                    # from a window the user closed, which is the end of the flow.
                    if page.is_closed() or not context.pages:
                        raise WindowClosed(
                            "the browser window was closed before the sign-in completed — "
                            "nothing was captured"
                        ) from exc
                    signed_in = False
                if signed_in:
                    try:
                        value = session_cookie(await context.cookies())
                    except Exception as exc:
                        raise WindowClosed(
                            "the browser window was closed before the session cookie could be "
                            "read — nothing was captured"
                        ) from exc
                    if value:
                        return value
                    # signed in, but the jar has not settled yet: keep polling
                await asyncio.sleep(poll_s)
            raise CaptureTimeout(
                f"no signed-in session appeared within {timeout_s} s — nothing was captured"
            )
        finally:
            await _close_browser(context, browser, pid, time.monotonic() + CLOSE_TIMEOUT_S)
    finally:
        await _await_bounded(
            driver.__aexit__(None, None, None), time.monotonic() + DRIVER_STOP_TIMEOUT_S
        )
