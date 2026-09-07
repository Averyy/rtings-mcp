"""`rt_sign_in` and `rt_auth_status`: the in-conversation sign-in.

Ported in shape from `consumer-reports-mcp`'s `auth_tools`, for the same reason it exists
there: **Claude Desktop has no terminal**, so `rtings-mcp auth` — paste a "Copy as cURL" blob,
press Ctrl-D — is reachable from Claude Code and from nowhere else. These two tools put the same
capability in the conversation, and the CLI stays as the fallback for a machine with no browser.

Why a background task and a status poll rather than one blocking call: Claude Desktop kills a
local tool call at 60 s and progress notifications do not extend it, while a human sign-in takes
as long as it takes. So `rt_sign_in` starts the flow and answers within ~2 s, and
`rt_auth_status(wait_s=…)` long-polls under the cap.

Nothing here returns, logs or formats a cookie value. The captured value travels
`capture_session()` → `validate_cookie()` → `store_credential()` and nowhere else.

Why the guard VERIFIES rather than refuses: `session` is `unknown` until a probe has run, and a
fresh Desktop process may never have run one. A guard that refuses on `unknown` would refuse a
dead cookie forever, and the user would recover only by discovering `force` — which makes the
guard advisory and defeats the renewal it exists for. So an unproven stored cookie is checked
against RTINGS first, and a window opens only when RTINGS declines it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .auth import load_credential, store_credential
from .browser_auth import (
    INSTALL_COMMAND,
    BrowserExtraMissing,
    BrowserNotFound,
    CaptureTimeout,
    WindowClosed,
    capture_session,
    extra_installed,
)
from .config import SESSION_COOKIE_NAME, Config

if TYPE_CHECKING:
    from .context import Context

log = logging.getLogger(__name__)

SIGN_IN_TIMEOUT_S = 300  # how long the window stays open
LAUNCH_WAIT_S = 1.5  # rt_sign_in waits this long for the browser to report before answering
STATUS_WAIT_MAX_S = 45  # the long-poll cap: under Claude Desktop's 60 s tool-call limit
CANCEL_WAIT_S = 10

#: The probe verdicts that mean "RTINGS looked at this cookie and it is signed in". `free` is
#: a real logged-in state here, unlike Consumer Reports: a free account is what the metered
#: preview budget exists for, so capturing one and calling it a failure would be wrong.
SIGNED_IN = frozenset({"member", "free"})
#: RTINGS looked and said no. `anonymous` belongs here: the probe page renders
#: `current_user: null` for a cookie it does not accept, which is a verdict, not a blank.
DECLINED = frozenset({"anonymous", "expired"})

CaptureFn = Callable[..., Awaitable[str]]
ValidateFn = Callable[[Config, str], Awaitable[str]]


# --------------------------------------------------------------------------- validation


async def _close_transport(transport: Any) -> None:
    for attr in ("_api_session", "_cdn_session"):
        session = getattr(transport, attr, None)
        if session is None:
            continue
        for method in ("aclose", "close"):
            closer = getattr(session, method, None)
            if closer is None:
                continue
            with contextlib.suppress(Exception):
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            break


async def validate_cookie(config: Config, cookie_value: str) -> str:
    """One real HTML probe with `cookie_value` held in MEMORY. Nothing is written.

    Returns the probe's `session` — `member`, `free`, `anonymous`, `expired`, `unknown` — or
    `could_not_check:<reason>` when the probe could not reach RTINGS, which is no verdict on
    the cookie at all.

    The candidate rides in on a throwaway `Config` whose `session_cookie_env` is the value, so
    `load_credential` picks it up without the stored file being touched. The cache dir is a
    temporary one: an `AuthManager` persists its probe under the cache root, and writing an
    `anonymous` probe there would clobber the real context's cached one.
    """
    from .context import Context

    scratch = Path(tempfile.mkdtemp(prefix="rtings-signin-"))
    probe_config = replace(
        config,
        cache_dir=scratch,
        session_cookie_env=cookie_value,
        session_override=None,  # an override would assert the answer we are asking for
        telemetry=False,
        warnings=[],
    )
    ctx = Context.build(probe_config)
    try:
        probe = await ctx.auth.session_probe(force=True)
    except Exception as exc:  # a crash in the probe is not a verdict on the cookie
        log.warning("sign-in: the cookie check crashed with %s", type(exc).__name__)
        return f"could_not_check:{type(exc).__name__}"
    finally:
        await _close_transport(ctx.transport)
        shutil.rmtree(scratch, ignore_errors=True)
    if probe.session == "unknown":
        return f"could_not_check:{probe.note or 'probe_unavailable'}"
    return probe.session


# --------------------------------------------------------------------------- the flow


class SignInFlow:
    """One sign-in at a time per process: a background task plus a phase the status tool reads.

    Phases: idle → [verifying →] waiting (a window is open) → validating (a cookie was
    captured) → active | refused | failed. `reason` is machine-readable and never a value.
    `capture` and `validate` are injection points for tests only.
    """

    def __init__(
        self,
        ctx: Context,
        *,
        capture: CaptureFn | None = None,
        validate: ValidateFn | None = None,
        timeout_s: int = SIGN_IN_TIMEOUT_S,
        launch_wait_s: float = LAUNCH_WAIT_S,
    ) -> None:
        self.ctx = ctx
        self._capture = capture
        self._validate: ValidateFn = validate or validate_cookie
        self.timeout_s = timeout_s
        self.launch_wait_s = launch_wait_s
        self.phase: str = "idle"
        self.reason: str | None = None
        self.browser: str | None = None
        self.session_after: str | None = None
        self._deadline: float | None = None
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Event()
        self._version = 0
        self._captured = False

    # --- state ---------------------------------------------------------------------
    @property
    def in_progress(self) -> bool:
        return self._task is not None and not self._task.done()

    def _pulse(self) -> None:
        self._version += 1
        self._changed.set()  # wake every waiter …
        self._changed.clear()  # … and arm for the next change

    def _set(self, phase: str, reason: str | None = None) -> None:
        self.phase, self.reason = phase, reason
        self._pulse()

    def _on_launch(self, label: str) -> None:
        self.browser = label
        log.info("sign-in: %s opened on the sign-in page", label)
        self._pulse()

    async def _wait_until(self, done: Callable[[], bool], budget_s: float) -> None:
        deadline = time.monotonic() + budget_s
        while not done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                return

    def expires_in_s(self) -> int | None:
        if self._deadline is None or not self.in_progress:
            return None
        return max(int(self._deadline - time.monotonic()), 0)

    @staticmethod
    def _cancel_requested() -> bool:
        """Whether `cancel()` has asked this task to stop, even if a library between here and
        the await swallowed the `CancelledError`. Read before the one irreversible step."""
        task = asyncio.current_task()
        return task is not None and task.cancelling() > 0

    # --- rt_sign_in ------------------------------------------------------------------
    async def start(self, *, force: bool = False) -> dict[str, Any]:
        ctx = self.ctx
        if self.in_progress:
            return self._answer(
                "in_progress",
                None,
                "A sign-in is already in progress"
                + (f" in {self.browser}" if self.browser else "")
                + ". Call rt_auth_status(wait_s=45) to wait for it; do not start another.",
            )

        # Clear the previous run's state BEFORE the refusal checks, not after: every early
        # return below goes through `_answer`, which reports `browser` and `expires_in_s`.
        # Left stale, a refusal claims a window is open — "browser: chrome, expires_in_s: 240"
        # — for a call that opened nothing. Safe here because `in_progress` already returned.
        self.browser = None
        self._deadline = None
        self._captured = False
        self.session_after = None

        credential = load_credential(ctx.config)
        if credential.source == "env":
            return self._answer(
                "refused",
                "env_override",
                "RTINGS_SESSION_COOKIE is set, and it takes precedence over anything a sign-in "
                "would store, so a captured cookie would be ignored. Clear it where the server "
                "is configured (the env block of the MCP server entry, or the shell), then call "
                "rt_sign_in again. Note the env var also cannot be refreshed when RTINGS "
                "re-issues the cookie, so a stored credential is the durable choice.",
            )

        # The guard. `force` means one thing: replace a credential RTINGS accepts right now.
        verify: str | None = None
        if not force and credential.present:
            probe = ctx.auth.cached_probe()
            if probe is not None and probe.logged_in:
                return self._answer("refused", "session_active", self._session_active_text())
            # Stored but unproven in this process. Resolve it rather than refuse blind.
            verify = credential.configured

        if self._capture is None and not extra_installed():
            return self._answer(
                "refused",
                "browser_extra_missing",
                "The browser extra is not installed, so no window can be opened. Install it "
                f"with `{INSTALL_COMMAND}` and restart the server, or run `rtings-mcp auth` in "
                "a terminal and paste a cookie there.",
            )

        self._set("verifying" if verify is not None else "waiting")
        self._task = asyncio.create_task(self._run(verify), name="rt-sign-in")
        # Answer within seconds either way: a launch failure, or a stored cookie RTINGS still
        # accepts, is reported now; a slow launch or a slow check is reported by
        # rt_auth_status. The user, not this call, is what takes minutes.
        await self._wait_until(self._answerable, self.launch_wait_s)
        return self._answer_for_phase()

    def _answerable(self) -> bool:
        if self.phase == "verifying":
            return False
        return not (self.phase == "waiting" and self.browser is None)

    def _answer_for_phase(self) -> dict[str, Any]:
        if self.phase == "failed":
            return self._answer("failed", self.reason, self._failure_text(self.reason))
        if self.phase == "refused":
            return self._answer("refused", self.reason, self._session_active_text())
        if self.phase == "verifying":
            return self._answer(
                "verifying",
                None,
                "A session cookie is stored but has not been confirmed in this process, so it "
                "is being checked against RTINGS before any window opens. If RTINGS still "
                "accepts it nothing changes and the sign-in is refused; if it is rejected, a "
                "sign-in window opens. Call rt_auth_status(wait_s=45) for the outcome.",
            )
        return self._answer(
            "waiting",
            None,
            f"A {self.browser or 'browser'} window has opened on RTINGS' sign-in page. Ask the "
            "user to sign in there. The server never sees the password and keeps only the "
            "resulting session cookie. The window closes itself once signed in and gives up "
            f"after {self.expires_in_s() or 0} s. Call rt_auth_status(wait_s=45) to wait for "
            "the outcome.",
        )

    def _answer(self, status: str, reason: str | None, instructions: str) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "instructions": instructions,
            "browser": self.browser,
            "expires_in_s": self.expires_in_s(),
            "session": self.session_after or self.ctx.auth.session_value(),
        }

    def _session_active_text(self) -> str:
        return (
            "A session is stored and RTINGS confirmed it live in this process, so signing in "
            "again would only replace a working cookie. Pass force=true to replace it anyway — "
            "to switch accounts, say. A cookie RTINGS has rejected is renewed without force. "
            "Note the session slides on use: it lasts as long as you keep using the server and "
            "lapses 30 days after you stop, so routine renewal is not needed."
        )

    def _failure_text(self, reason: str | None) -> str:
        r = reason or ""
        if r == "browser_not_found":
            return (
                "No installed Chrome, Edge or Chromium-family browser could be launched; "
                "nothing is downloaded. Install Chrome, or run `rtings-mcp auth` in a terminal "
                "and paste a cookie there."
            )
        if r == "browser_extra_missing":
            return (
                f"The browser extra is not installed. Install it with `{INSTALL_COMMAND}` and "
                "restart the server, or run `rtings-mcp auth` in a terminal."
            )
        if r == "window_closed":
            return (
                "The window was closed before the sign-in completed; nothing was captured and "
                "nothing changed. Call rt_sign_in again to retry."
            )
        if r == "capture_timeout":
            return (
                f"No signed-in session appeared within {self.timeout_s} s; nothing was captured "
                "and nothing changed. Call rt_sign_in again to retry."
            )
        if r in DECLINED:
            return (
                "RTINGS issued a session but it did not read as signed in, so nothing was "
                "stored. That usually means the sign-in did not complete in the window. Retry "
                "rt_sign_in, or paste a cookie with `rtings-mcp auth`."
            )
        if r.startswith("could_not_check:"):
            detail = r.split(":", 1)[1]
            if not self._captured:
                return (
                    "The stored session could not be checked against RTINGS "
                    f"({detail}), so no window was opened and nothing changed. Retry once the "
                    "network is back, or pass force=true to open a window without the check."
                )
            return (
                "A cookie was captured but could not be checked against RTINGS "
                f"({detail}); nothing was stored. Retry once the network is back."
            )
        if r.startswith("save_failed:"):
            # the file's path is under the user's home: it goes to the log, never to a response
            return (
                "The cookie authenticated but could not be written to the config directory "
                f"({r.split(':', 1)[1]}); nothing changed. Check that the directory is writable "
                "(the server log names it), or run `rtings-mcp auth` in a terminal."
            )
        if r.startswith("internal_error:"):
            return (
                f"The sign-in hit an unexpected error ({r.split(':', 1)[1]}). rt_auth_status "
                "reports what is stored; call rt_sign_in again to retry."
            )
        if r == "cancelled":
            return "The sign-in was cancelled by server shutdown; nothing changed."
        return f"The sign-in failed ({r or 'unknown'}); nothing changed."

    async def _run(self, verify: str | None) -> None:
        try:
            if verify is not None and not await self._verify_stored(verify):
                return
            await self._capture_and_store()
        except asyncio.CancelledError:
            self._set("failed", "cancelled")
            raise
        except Exception as exc:  # a live phase must never outlive the task
            log.warning("sign-in: unexpected %s; the flow is abandoned", type(exc).__name__)
            self._set("failed", f"internal_error:{type(exc).__name__}")

    async def _verify_stored(self, cookie_value: str) -> bool:
        """Pre-flight check of a stored-but-unproven cookie. Returns whether to open a window:
        only when RTINGS itself declined it."""
        log.info("sign-in: a session is stored but unproven; checking it before any window")
        outcome = await self._validate(self.ctx.config, cookie_value)
        if outcome in SIGNED_IN:
            log.info("sign-in: the stored session is live; no window opened, nothing changed")
            self.session_after = outcome
            self._set("refused", "session_active")
            return False
        if outcome in DECLINED:
            log.info("sign-in: RTINGS declined the stored session (%s)", outcome)
            return True
        # could_not_check:<reason>: no verdict on the cookie, so neither "it works" nor "it is
        # dead" may be claimed, and no window opens on a guess.
        log.info("sign-in: the stored session could not be judged (%s); nothing changed", outcome)
        self._set("failed", outcome)
        return False

    async def _capture_and_store(self) -> None:
        ctx = self.ctx
        capture = self._capture or capture_session
        self._deadline = time.monotonic() + self.timeout_s
        if self.phase != "waiting":
            self._set("waiting")
        try:
            cookie_value = await capture(timeout_s=self.timeout_s, on_launch=self._on_launch)
        except BrowserExtraMissing:
            self._set("failed", "browser_extra_missing")
            return
        except BrowserNotFound as exc:
            log.warning("sign-in: %s", exc)
            self._set("failed", "browser_not_found")
            return
        except WindowClosed:
            self._set("failed", "window_closed")
            return
        except CaptureTimeout:
            self._set("failed", "capture_timeout")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the browser step must never take the server down
            log.warning("sign-in: the browser step failed with %s", type(exc).__name__)
            self._set("failed", f"browser_error:{type(exc).__name__}")
            return

        self._captured = True
        log.info("sign-in: a cookie was captured; checking it against rtings.com")
        self._set("validating")
        outcome = await self._validate(ctx.config, cookie_value)
        if outcome not in SIGNED_IN:
            log.info("sign-in: the captured cookie did not read as signed in (%s)", outcome)
            self._set("failed", outcome)
            return
        if self._cancel_requested():
            # shutdown asked us to stop and something below swallowed the cancellation: the
            # status already says `cancelled`, and storing now would make that a lie
            log.info("sign-in: cancelled before the cookie was stored; nothing changed")
            return
        try:
            store_credential(ctx.config, cookie_value)
        except (OSError, ValueError) as exc:
            log.error(
                "sign-in: could not store the session at %s (%s)",
                ctx.config.session_file,
                type(exc).__name__,
            )
            self._set("failed", f"save_failed:{type(exc).__name__}")
            return
        # Adopt it in this process too, or the running transport keeps using the old
        # credential until a restart, and rt_auth_status would report a session the data path
        # is not actually using.
        ctx.transport.credential = load_credential(ctx.config)
        ctx.transport.adopt_rotated_cookie(cookie_value)
        await ctx.auth.session_probe(force=True)
        self.session_after = outcome
        log.info("sign-in: %s session stored at %s", outcome, ctx.config.session_file)
        self._set("active")

    # --- rt_auth_status ---------------------------------------------------------------
    async def status(self, wait_s: int = 0) -> dict[str, Any]:
        try:
            budget = min(max(int(wait_s or 0), 0), STATUS_WAIT_MAX_S)
        except (TypeError, ValueError):
            budget = 0
        if budget and self.in_progress:
            seen = self._version
            await self._wait_until(lambda: self._version != seen, budget)
        if not self.in_progress:
            # "Is my membership live?" deserves an answer, not `unknown`: on a fresh cache
            # nothing has probed yet, and the CLI's `auth --status` already probes. It is
            # the session probe (`/tv/tools/table`, never a review page), throttled by its
            # TTL, and skipped while a sign-in is running (member round S14, 2026-09-07).
            await self.ctx.auth.ensure_session()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        ctx = self.ctx
        credential = load_credential(ctx.config)
        probe = ctx.auth.cached_probe()
        return {
            "session": probe.session if probe else "unknown",
            "stored": credential.present,
            "source": credential.source,
            "stored_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(credential.stored_at))
                if credential.stored_at
                else None
            ),
            "sign_in": self.phase,
            "reason": self.reason,
            "browser": self.browser,
            "previews_remaining": ctx.auth.previews_remaining(probe),
            "cookie_name": SESSION_COOKIE_NAME,
        }

    async def cancel(self) -> None:
        """Server shutdown: stop a sign-in in flight. The window closes; nothing is stored."""
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        self._set("failed", "cancelled")
        try:
            await asyncio.wait_for(asyncio.shield(task), CANCEL_WAIT_S)
        except asyncio.CancelledError:
            if not task.done():  # the caller's own cancellation, not the task's: propagate
                raise
        except Exception:
            pass


# --------------------------------------------------------------------------- tool bodies

_FLOWS: dict[int, SignInFlow] = {}


def flow_for(ctx: Context) -> SignInFlow:
    """One flow per Context, created on first use. Keyed by identity so a test context and the
    process context never share a phase."""
    flow = _FLOWS.get(id(ctx))
    if flow is None:
        flow = SignInFlow(ctx)
        _FLOWS[id(ctx)] = flow
    return flow


async def rt_sign_in(ctx: Context, force: bool = False) -> dict[str, Any]:
    return await flow_for(ctx).start(force=bool(force))


async def rt_auth_status(ctx: Context, wait_s: int = 0) -> dict[str, Any]:
    return await flow_for(ctx).status(wait_s=wait_s)
