"""`rt_sign_in` / `rt_auth_status`: the in-conversation sign-in.

The property that matters most here is the negative one — **a cookie RTINGS did not confirm is
never stored** — because the failure it prevents is silent: a captured anonymous session written
over a working credential would leave every later call quietly logged out. RTINGS mints
`_rtings_session` for anonymous requests too and re-issues it on every response, so "a cookie
appeared" and "the cookie changed" are both true within a second of opening the page. Only the
probe's verdict counts.

`capture` and `validate` are injected throughout: these tests never launch a browser and never
touch the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from rtings_mcp import auth_tools
from rtings_mcp.auth import load_credential, store_credential
from rtings_mcp.auth_tools import SignInFlow
from rtings_mcp.browser_auth import BrowserNotFound, CaptureTimeout, WindowClosed

# The stub-transport Context lives with the offline tool tests; importing the fixtures keeps
# one wiring rather than a second copy that can drift from it.
from test_tools_offline import ctx, member_probe, payloads  # noqa: F401

pytestmark = pytest.mark.asyncio

CAPTURED = "captured-session-value"


def flow(ctx, *, capture=None, validate=None, **kw):
    async def default_capture(timeout_s=300, on_launch=None):
        if on_launch:
            on_launch("chrome")
        return CAPTURED

    async def default_validate(config, cookie):
        return "member"

    return SignInFlow(
        ctx,
        capture=capture or default_capture,
        validate=validate or default_validate,
        launch_wait_s=2.0,
        **kw,
    )


async def settle(f):
    """Wait for the background task, swallowing the cancellation `cancel()` causes."""
    if f._task is None:
        return
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(asyncio.shield(f._task), 5)


def stored(ctx):
    return load_credential(ctx.config).configured


# -- the negative property ----------------------------------------------------------


async def test_a_cookie_rtings_does_not_confirm_is_never_stored(ctx):
    """The whole reason the flow validates before it saves. RTINGS hands an anonymous visitor
    a session cookie too, so capturing one proves nothing on its own."""

    async def anonymous(config, cookie):
        return "anonymous"

    f = flow(ctx, validate=anonymous)
    await f.start()
    await settle(f)
    assert f.phase == "failed"
    assert f.reason == "anonymous"
    assert stored(ctx) is None, "an unconfirmed cookie must not reach the credential file"


async def test_a_cookie_that_could_not_be_checked_is_never_stored(ctx):
    """A transport failure is no verdict on the cookie, so it is neither trusted nor blamed."""

    async def offline(config, cookie):
        return "could_not_check:fetch_failed"

    f = flow(ctx, validate=offline)
    await f.start()
    await settle(f)
    assert f.phase == "failed"
    assert f.reason.startswith("could_not_check:")
    assert stored(ctx) is None


async def test_a_confirmed_cookie_is_stored_and_adopted(ctx):
    f = flow(ctx)
    await f.start()
    await settle(f)
    assert f.phase == "active"
    assert stored(ctx) == CAPTURED
    # adopted in-process too, or the running transport keeps using the old credential and
    # rt_auth_status reports a session the data path is not actually using
    assert ctx.transport.credential.configured == CAPTURED


async def test_a_free_account_counts_as_signed_in(ctx):
    """Unlike Consumer Reports, `free` is a real logged-in state here — it is what the metered
    preview budget exists for — so refusing it would throw away a working credential."""

    async def free(config, cookie):
        return "free"

    f = flow(ctx, validate=free)
    await f.start()
    await settle(f)
    assert f.phase == "active"
    assert stored(ctx) == CAPTURED


# -- the guard ----------------------------------------------------------------------


async def test_a_live_session_is_refused_without_force(ctx):
    """And refused from the CACHED probe, without a network check.

    Both paths end in `refused`, so asserting the outcome alone passes even with the fast
    path removed — and the flow would then re-probe RTINGS on every sign-in attempt against a
    session it already knows is live. Counting the checks is what pins it.
    """
    checks = []

    async def counting(config, cookie):
        checks.append(cookie)
        return "member"

    store_credential(ctx.config, "EXISTING")
    member_probe(ctx)
    f = flow(ctx, validate=counting)
    out = await f.start()
    assert out["status"] == "refused"
    assert out["reason"] == "session_active"
    assert checks == [], "a probe already knows this session is live; do not re-check it"
    assert stored(ctx) == "EXISTING", "a working credential must survive a refused sign-in"


async def test_a_refusal_never_reports_the_PREVIOUS_run_s_window(ctx):
    """A refusal opens nothing, so it must not describe a window.

    `_answer` reports `browser` and `expires_in_s` off instance state. Reset after the
    refusal checks instead of before, and a `session_active` refusal answers
    `browser: "chrome", expires_in_s: 240` — an agent reading that tells the user to go
    look for a Chrome window that was closed minutes ago. The first sign-in here succeeds
    quickly, so the stale deadline is still in the future when the second call refuses.
    """
    f = flow(ctx)
    first = await f.start()
    await settle(f)
    assert first["browser"] == "chrome" and first["expires_in_s"] > 0
    assert f.phase == "active"

    member_probe(ctx)
    again = await f.start()
    assert again["reason"] == "session_active"
    assert again["browser"] is None, "no window was opened by this call"
    assert again["expires_in_s"] is None, "nothing is counting down"


async def test_force_replaces_a_live_session(ctx):
    store_credential(ctx.config, "EXISTING")
    member_probe(ctx)
    f = flow(ctx)
    await f.start(force=True)
    await settle(f)
    assert f.phase == "active"
    assert stored(ctx) == CAPTURED


async def test_the_env_var_refuses_because_it_would_win_anyway(ctx, monkeypatch):
    """Storing a cookie the env var overrides would report success and change nothing."""
    from dataclasses import replace

    ctx.config = replace(ctx.config, session_cookie_env="ENV-VALUE")
    f = flow(ctx)
    out = await f.start()
    assert out["status"] == "refused"
    assert out["reason"] == "env_override"
    assert "RTINGS_SESSION_COOKIE" in out["instructions"]


# -- the pre-flight check of a stored-but-unproven cookie ---------------------------


async def test_an_unproven_stored_cookie_is_verified_before_any_window_opens(ctx):
    """`session` is `unknown` until a probe runs, and a fresh Claude Desktop process may never
    have run one. Refusing on `unknown` would refuse a DEAD cookie forever; opening a window on
    it would replace a working one for no reason. So it is resolved, not guessed."""
    store_credential(ctx.config, "EXISTING")
    ctx.auth._probe = None
    opened = []

    async def never_called(timeout_s=300, on_launch=None):
        opened.append(True)
        return CAPTURED

    async def still_good(config, cookie):
        assert cookie == "EXISTING"
        return "member"

    f = flow(ctx, capture=never_called, validate=still_good)
    await f.start()
    await settle(f)
    assert f.phase == "refused"
    assert f.reason == "session_active"
    assert opened == [], "no window may open for a cookie RTINGS still accepts"
    assert stored(ctx) == "EXISTING"


async def test_a_stored_cookie_rtings_rejects_opens_the_window(ctx):
    store_credential(ctx.config, "DEAD")
    ctx.auth._probe = None

    async def rejects_then_accepts(config, cookie):
        return "member" if cookie == CAPTURED else "expired"

    f = flow(ctx, validate=rejects_then_accepts)
    await f.start()
    await settle(f)
    assert f.phase == "active"
    assert stored(ctx) == CAPTURED


async def test_an_unjudgeable_stored_cookie_opens_no_window(ctx):
    """Neither "it works" nor "it is dead" may be claimed from a transport failure."""
    store_credential(ctx.config, "EXISTING")
    ctx.auth._probe = None
    opened = []

    async def never_called(timeout_s=300, on_launch=None):
        opened.append(True)
        return CAPTURED

    async def cannot_tell(config, cookie):
        return "could_not_check:challenged"

    f = flow(ctx, capture=never_called, validate=cannot_tell)
    await f.start()
    await settle(f)
    assert f.phase == "failed"
    assert opened == []
    assert stored(ctx) == "EXISTING"


# -- browser failures change nothing ------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (WindowClosed("closed"), "window_closed"),
        (CaptureTimeout("timeout"), "capture_timeout"),
        (BrowserNotFound("none"), "browser_not_found"),
    ],
)
async def test_a_browser_failure_stores_nothing_and_says_which(ctx, exc, reason):
    async def boom(timeout_s=300, on_launch=None):
        raise exc

    f = flow(ctx, capture=boom)
    await f.start()
    await settle(f)
    assert f.phase == "failed"
    assert f.reason == reason
    assert stored(ctx) is None
    assert "nothing" in f._failure_text(reason).lower()


# -- the poll ------------------------------------------------------------------------


async def test_sign_in_answers_without_waiting_for_the_human(ctx):
    """Claude Desktop kills a tool call at 60 s; a human sign-in takes as long as it takes."""
    release = asyncio.Event()

    async def slow(timeout_s=300, on_launch=None):
        if on_launch:
            on_launch("chrome")
        await release.wait()
        return CAPTURED

    f = flow(ctx, capture=slow)
    out = await asyncio.wait_for(f.start(), 5)
    assert out["status"] == "waiting"
    assert out["browser"] == "chrome"
    assert f.in_progress
    release.set()
    await settle(f)
    assert f.phase == "active"


async def test_status_long_polls_and_is_capped(ctx):
    release = asyncio.Event()

    async def slow(timeout_s=300, on_launch=None):
        if on_launch:
            on_launch("chrome")
        await release.wait()
        return CAPTURED

    f = flow(ctx, capture=slow)
    await f.start()
    snap = await f.status()
    assert snap["sign_in"] == "waiting"

    async def finish():
        await asyncio.sleep(0.05)
        release.set()

    finisher = asyncio.ensure_future(finish())
    snap = await asyncio.wait_for(f.status(wait_s=5), 10)
    await settle(f)
    assert (await f.status())["sign_in"] == "active"
    # the cap holds whatever the caller asks for
    await finisher
    assert auth_tools.STATUS_WAIT_MAX_S <= 45, "the cap must stay under Desktop's 60 s limit"


async def test_a_second_sign_in_does_not_start_another_window(ctx):
    release = asyncio.Event()
    launches = []

    async def slow(timeout_s=300, on_launch=None):
        launches.append(True)
        if on_launch:
            on_launch("chrome")
        await release.wait()
        return CAPTURED

    f = flow(ctx, capture=slow)
    await f.start()
    out = await f.start()
    assert out["status"] == "in_progress"
    release.set()
    await settle(f)
    assert len(launches) == 1


async def test_cancel_stores_nothing(ctx):
    release = asyncio.Event()

    async def slow(timeout_s=300, on_launch=None):
        if on_launch:
            on_launch("chrome")
        await release.wait()
        return CAPTURED

    f = flow(ctx, capture=slow)
    await f.start()
    await f.cancel()
    assert f.phase == "failed"
    assert f.reason == "cancelled"
    assert stored(ctx) is None


# -- the envelope never leaks the value ---------------------------------------------


async def test_no_response_ever_contains_the_cookie_value(ctx):
    f = flow(ctx)
    first = await f.start()
    await settle(f)
    snap = await f.status()
    blob = json.dumps([first, snap])
    assert CAPTURED not in blob
    assert "_rtings_session=" not in blob


async def test_status_reports_what_is_stored_without_opening_anything(ctx):
    store_credential(ctx.config, "EXISTING")
    snap = await auth_tools.rt_auth_status(ctx)
    assert snap["stored"] is True
    assert snap["source"] == "file"
    assert snap["cookie_name"] == "_rtings_session"
    assert snap["sign_in"] == "idle"
    assert "EXISTING" not in json.dumps(snap)


async def test_status_resolves_the_session_instead_of_saying_unknown(ctx):
    """On a fresh cache nothing has probed, and `rt_auth_status` answered `unknown` to "is my
    membership live?" while the CLI's `auth --status` probed (member round S14, 2026-09-07).
    Without a credential the answer is `anonymous` by construction and costs no request."""
    snap = await auth_tools.rt_auth_status(ctx)
    assert snap["session"] == "anonymous"
    store_credential(ctx.config, "EXISTING")
    ctx.transport.credential = load_credential(ctx.config)
    ctx.auth._probe = None
    snap = await auth_tools.rt_auth_status(ctx)
    assert snap["session"] == "free"
    assert "EXISTING" not in json.dumps(snap)


# -- validate_cookie itself: the guard that was read but never run ------------------


@pytest.fixture
def stub_build(monkeypatch, payloads):
    """Run the REAL `validate_cookie` against the stub transport.

    Every test above injects `validate`, so `validate_cookie` — the function that turns a
    probe into "member" / "expired" / "could_not_check" and the one the CLI calls too — had
    no coverage at all. It builds its own `Context`, so the seam is `Context.build`.
    """
    from rtings_mcp.auth import AuthManager
    from rtings_mcp.cache import Cache
    from rtings_mcp.context import Context
    from rtings_mcp.repository import Repository
    from test_tools_offline import StubTransport

    built = {}

    def fake_build(cls, config=None):
        transport = StubTransport(config, payloads)
        # What the page says back is what the cookie is worth; `session_page` is the stub's
        # knob for exactly that.
        transport.session_page = built.get("page", "member")
        transport.credential.configured = config.session_cookie_env
        cache = Cache(config)
        auth = AuthManager(config=config, cache=cache, transport=transport)
        built["config"] = config
        built["transport"] = transport
        return Context(
            config=config,
            cache=cache,
            transport=transport,
            auth=auth,
            repo=Repository(config, cache, transport, auth),
        )

    monkeypatch.setattr(Context, "build", classmethod(fake_build))
    return built


async def test_validate_cookie_calls_a_cookie_rtings_logs_out_expired(ctx, stub_build):
    """The negative half: a cookie that comes back logged out must be `expired`, or a dead
    credential would be written over a working one."""
    stub_build["page"] = "anonymous"  # a configured cookie that came back logged out
    assert await auth_tools.validate_cookie(ctx.config, "DEAD") == "expired"
    assert stub_build["config"].session_cookie_env == "DEAD", "the candidate rode in on it"


async def test_validate_cookie_confirms_a_live_member_cookie(ctx, stub_build):
    stub_build["page"] = "member"
    assert await auth_tools.validate_cookie(ctx.config, CAPTURED) == "member"


async def test_validate_cookie_never_writes_to_the_real_cache_or_credential(
    ctx, stub_build
):
    """An `AuthManager` persists its probe under the cache root, so the check runs on a
    throwaway cache dir — and that dir is removed afterwards."""
    store_credential(ctx.config, "EXISTING")
    before = sorted(p.name for p in ctx.config.cache_dir.rglob("*"))
    stub_build["page"] = "anonymous"

    assert await auth_tools.validate_cookie(ctx.config, "DEAD") == "expired"

    scratch = stub_build["config"].cache_dir
    assert scratch != ctx.config.cache_dir, "never the caller's cache"
    assert not scratch.exists(), "and the scratch dir is cleaned up"
    assert sorted(p.name for p in ctx.config.cache_dir.rglob("*")) == before
    assert load_credential(ctx.config).configured == "EXISTING", "nothing was stored"
    assert stub_build["config"].session_override is None, "an override would beg the question"


async def test_a_probe_that_crashes_is_no_verdict_on_the_cookie(ctx, monkeypatch, stub_build):
    from rtings_mcp.auth import AuthManager

    async def boom(self, *, force=False):
        raise RuntimeError("network gone")

    monkeypatch.setattr(AuthManager, "session_probe", boom)
    outcome = await auth_tools.validate_cookie(ctx.config, "MAYBE")
    assert outcome == "could_not_check:RuntimeError"
    assert outcome.split(":")[0] not in ("member", "expired", "anonymous")


async def test_an_unknown_probe_reports_its_note_not_a_verdict(ctx, monkeypatch, stub_build):
    from rtings_mcp.auth import AuthManager

    async def unknown(self, *, force=False):
        return self.unknown_probe("challenged")

    monkeypatch.setattr(AuthManager, "session_probe", unknown)
    assert await auth_tools.validate_cookie(ctx.config, "MAYBE") == "could_not_check:challenged"
