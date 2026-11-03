"""The real ``Transport``, not the double.

``StubTransport`` overrides ``api_post``/``api_get_html``/``cdn_get_json`` without calling
``super()``, so ``Transport._perform``/``_classify`` and ``api_post``'s ``errors[]`` handling
are dead code under the rest of the suite. Mutation-testing confirmed it: deleting the
challenge-precedence branch, and making any ``errors[]`` fatal, both left 261 tests green.

These drive the shipped code by injecting a fake wafer session at ``_api_session`` — the one
seam that leaves ``_perform``, the cooldown, the token bucket and the JSON handling intact.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace

import pytest
from wafer import AsyncSession

from rtings_mcp import errors
from rtings_mcp.auth import load_credential, store_credential
from rtings_mcp.auth_tools import SignInFlow, _close_transport
from rtings_mcp.config import BASE_URL, SESSION_COOKIE_NAME, load_config
from rtings_mcp.context import Context
from rtings_mcp.errors import RtingsError
from rtings_mcp.http import CredentialState, Transport

# The probe pages the offline tool tests already classify; one copy, not a second that drifts.
from test_tools_offline import FREE_HTML, MEMBER_HTML, PAGE_HTML

pytestmark = pytest.mark.asyncio


class FakeResponse:
    """The attributes ``_classify`` and ``_perform`` actually read off a wafer response."""

    def __init__(
        self,
        status_code=200,
        text="{}",
        *,
        challenge_type=None,
        retry_after=None,
        headers=None,
        url="https://www.rtings.com/api/v2/safe/q",
    ):
        self.status_code = status_code
        self.text = text
        self.challenge_type = challenge_type
        self.retry_after = retry_after
        self.headers = headers or {}
        self.url = url


class FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        resp = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture
def transport(tmp_path):
    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CONFIG_DIR": str(tmp_path / "cfg")}
    )
    return Transport(config)


def wire(transport, *responses):
    """Install a fake session so the REAL _perform/_classify/api_post run over it."""
    session = FakeSession(*responses)
    transport._api_session = session
    return session


async def post(transport):
    return await transport.api_post("table_tool__test_results", {"variables": {}})


# -- status precedence: first match wins ------------------------------------------


async def test_a_challenge_wins_over_its_status_code(transport):
    """`challenged` is checked BEFORE the status branches. A challenge served with 429 must
    not be reported as `rate_limited`: one says "wait 30 seconds", the other does not."""
    wire(transport, FakeResponse(429, challenge_type="cloudflare", retry_after=5))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.CHALLENGED


async def test_a_challenge_on_a_200_is_still_a_challenge(transport):
    wire(transport, FakeResponse(200, text='{"data":{}}', challenge_type="turnstile"))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.CHALLENGED


async def test_429_is_rate_limited_and_carries_retry_after(transport):
    """Split from `challenged` deliberately: collapsing them tells the agent "no" when the
    answer is "in 30 seconds"."""
    wire(transport, FakeResponse(429, retry_after=17))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.RATE_LIMITED
    # The wait the server will enforce, not RTINGS' raw 17: the cooldown floors at 30 s, and
    # reporting 17 sent an agent back into a `cooldown_active` refusal (found 2026-11-02).
    enforced = transport.cooldown.remaining("www.rtings.com")
    assert enforced > 29  # floors at 30 s; it has ticked down a little since
    assert exc.value.retry_after == pytest.approx(enforced, abs=1.0)


async def test_503_with_retry_after_is_rate_limited(transport):
    wire(transport, FakeResponse(503, retry_after=9))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.RATE_LIMITED
    assert exc.value.retry_after > 29, "the enforced wait, not the raw Retry-After"


async def test_a_bare_503_is_fetch_failed_and_installs_no_cooldown(transport):
    """We were not told to wait, so we do not invent a wait."""
    wire(transport, FakeResponse(503))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.FETCH_FAILED
    assert transport.cooldown.remaining("www.rtings.com") == 0


async def test_an_empty_200_is_fetch_failed_not_the_drift_alarm(transport):
    """`payload_missing` means "the shape changed"; this is "the transport failed"."""
    wire(transport, FakeResponse(200, text="   "))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.FETCH_FAILED


async def test_a_200_that_is_not_json_is_the_drift_alarm(transport):
    wire(transport, FakeResponse(200, text="<html>nope</html>"))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.PAYLOAD_MISSING


# -- the cooldown is installed and cleared by these paths --------------------------


async def test_a_rate_limit_installs_a_cooldown_that_a_200_then_clears(transport):
    wire(transport, FakeResponse(429, retry_after=30))
    with pytest.raises(RtingsError):
        await post(transport)
    assert transport.cooldown.remaining("www.rtings.com") > 0

    transport.cooldown.clear("www.rtings.com")
    wire(transport, FakeResponse(200, text='{"data":{"ok":1}}'))
    await post(transport)
    assert transport.cooldown.remaining("www.rtings.com") == 0


async def test_a_cooling_host_is_refused_without_spending_a_request(transport):
    session = wire(transport, FakeResponse(429, retry_after=30))
    with pytest.raises(RtingsError):
        await post(transport)
    before = len(session.calls)
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.COOLDOWN_ACTIVE
    assert exc.value.code in errors.RETRYABLE, "the agent must be told to come back"
    assert len(session.calls) == before, "a cooling host must not burn a request"


# -- errors[] beside data is a partial-field notice, not a failure -----------------


async def test_partial_field_errors_do_not_discard_the_data(transport):
    """Measured: `distribution_tooltip__test` strips three admin-only fields, reports it in
    `errors[]`, and returns a complete payload. Treating any errors[] as fatal throws it
    away. Mutating the shipped code to do so left the whole suite green."""
    body = json.dumps(
        {
            "data": {"target": {"help_what": "The TV's maximum luminance"}},
            "errors": [{"message": "The field edit_url ... was hidden due to permissions"}],
        }
    )
    wire(transport, FakeResponse(200, text=body))
    payload = await post(transport)
    assert payload["data"]["target"]["help_what"].startswith("The TV's")


async def test_errors_with_no_data_is_a_real_api_error(transport):
    body = json.dumps({"data": None, "errors": [{"message": "boom"}]})
    wire(transport, FakeResponse(200, text=body))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.API_ERROR


# -- the request the real client sends --------------------------------------------


async def test_browser_headers_go_out_per_request(transport):
    """The cookie is the sole credential, but server-side Origin/Referer validation is the
    one unprovable risk and these remove it for free."""
    session = wire(transport, FakeResponse(200, text='{"data":{}}'))
    await transport.api_post("q", {"variables": {}}, referer="https://www.rtings.com/tv")
    sent = session.calls[-1]["headers"]
    assert sent["Origin"] == "https://www.rtings.com"
    assert sent["Referer"] == "https://www.rtings.com/tv"
    assert sent["Sec-Fetch-Site"] == "same-origin"
    assert sent["Sec-Fetch-Mode"] == "cors"
    assert sent["Sec-Fetch-Dest"] == "empty"


async def test_the_total_and_attempt_timeouts_are_always_paired(transport):
    """`timeout` is a TOTAL budget across retries; unpaired, one hanging request eats it."""
    session = wire(transport, FakeResponse(200, text='{"data":{}}'))
    await post(transport)
    call = session.calls[-1]
    assert call["timeout"] and call["attempt_timeout"]
    assert call["attempt_timeout"] <= call["timeout"]


async def test_telemetry_never_records_a_body_or_a_cookie(transport, tmp_path):
    """Challenge pages carry tokens and member pages carry profile data."""
    wire(
        transport,
        FakeResponse(
            200,
            text='{"data":{"secret":"MEMBER-PROFILE-DATA"}}',
            headers={"set-cookie": "_rtings_session=SECRETVALUE123; Path=/"},
        ),
    )
    await post(transport)
    log = transport.config.cache_dir / "telemetry" / "requests.jsonl"
    if log.exists():
        written = log.read_text()
        assert "SECRETVALUE123" not in written
        assert "MEMBER-PROFILE-DATA" not in written
        assert "set-cookie" not in written.lower()
        # Several processes share one log; a line is attributable only by its pid.
        assert '"pid":' in written


async def test_the_cdn_cap_clears_the_largest_curve_rtings_actually_publishes():
    """256 KB was sized from TV, whose largest curve is ~74 KB — and it silently turned real
    published curves into `fetch_failed: response exceeded the size cap`. Measured live
    2026-09-05: speaker "Raw Frequency Response Graph" 361 KB, soundbar "H Frequency
    Response" 335 KB, headphones "Harmonics Levels" 190 KB.

    This asserts headroom over the measured worst case so the constant cannot be tuned back
    down without the number that justifies it. The reply is resampled to ~200 points
    regardless, so a bigger cap bounds the fetch, not what the caller receives.
    """
    from rtings_mcp.http import CDN_MAX_RESPONSE_BYTES

    largest_observed = 361 * 1024
    assert largest_observed < CDN_MAX_RESPONSE_BYTES, (
        "a curve RTINGS publishes must not be unfetchable; re-measure before lowering this"
    )


# -- a sign-in must reach the jar the running session sends (2026-10-22, PR #1) ------------
#
# `StubTransport` has no jar: its `current_jar_cookie` returns `credential.configured`, so
# "the running transport adopted the cookie" was asserted against the metadata, and passed,
# while every real request kept sending the previous cookie until a restart. These drive
# wafer's REAL cookie jar; only `AsyncSession.request` — the wire — is replaced.


class FakeRtings:
    """RTINGS' session behaviour, read off the cookie the jar actually sends.

    It answers the probe page signed in only for a cookie it accepts, and re-issues
    `_rtings_session` on every response — rotated when signed in, an anonymous re-mint
    otherwise (`RECON.md` §5, §12.15) — at RTINGS' own scope, `Domain=rtings.com; Path=/`
    (measured 2026-11-02, the same scope `_inject_credential` writes).
    """

    def __init__(self, accepts=(), page=MEMBER_HTML):
        self.accepts = set(accepts)
        self.page = page
        self.sent: list[str | None] = []

    def install(self, monkeypatch):
        rtings = self

        async def request(session, method, url, **kwargs):
            return rtings.answer(session, url)

        monkeypatch.setattr(AsyncSession, "request", request)

    def answer(self, session, url):
        cookie = session.get_cookie(SESSION_COOKIE_NAME, url)
        self.sent.append(cookie)
        n = len(self.sent)
        if cookie in self.accepts:
            reissued, text = f"rotated-{n}", self.page
            self.accepts.add(reissued)
        else:
            reissued, text = f"anonymous-{n}", PAGE_HTML
        session.add_cookie(
            f"{SESSION_COOKIE_NAME}={reissued}; Domain=.rtings.com; Path=/; HttpOnly", BASE_URL
        )
        return FakeResponse(200, text=text, url=url)


@pytest.fixture
async def live_ctx(config):
    """A real Context over the real Transport; the caller stores a credential first if any."""
    built: list[Context] = []

    def build(**overrides):
        ctx = Context.build(replace(config, rate_interval_s=0, **overrides))
        built.append(ctx)
        return ctx

    yield build
    for ctx in built:
        await _close_transport(ctx.transport)


def signin(ctx, captured="new-cookie", opened=None, validate=None):
    async def capture(timeout_s=300, on_launch=None):
        if opened is not None:
            opened.append(True)
        if on_launch:
            on_launch("chrome")
        return captured

    async def accepted(config, cookie):
        return "member"  # the throwaway check accepted it; the point is what happens next

    return SignInFlow(ctx, capture=capture, validate=validate or accepted, launch_wait_s=2.0)


@pytest.mark.parametrize(("page", "expected"), [(MEMBER_HTML, "member"), (FREE_HTML, "free")])
async def test_a_sign_in_reaches_the_jar_the_running_session_sends(
    live_ctx, monkeypatch, config, page, expected
):
    """The reported defect, end to end: a process that had already made a request kept
    sending the old cookie (or RTINGS' anonymous re-mint of it) after a sign-in, while
    `rt_sign_in` said `active`."""
    store_credential(config, "expired-cookie")
    ctx = live_ctx()
    rtings = FakeRtings(accepts={"new-cookie"}, page=page)
    rtings.install(monkeypatch)
    assert (await ctx.auth.session_probe(force=True)).session == "expired"
    session = await ctx.transport.api_session()
    session.add_cookie("product-previews=spent; Domain=.rtings.com; Path=/", BASE_URL)

    flow = signin(ctx)
    await flow._capture_and_store()

    assert flow.phase == "active", flow.reason
    assert rtings.sent[-1] == "new-cookie", "the confirming probe must go out on the new cookie"
    assert ctx.auth.cached_probe().session == expected
    assert await ctx.transport.api_session() is session, "the jar is updated, never rebuilt"
    assert session.get_cookie("product-previews", BASE_URL) == "spent"
    # RTINGS re-issued it on that probe; the proven-logged-in rotation is what gets kept.
    jar = await ctx.transport.current_jar_cookie()
    assert jar.startswith("rotated-")
    assert ctx.transport.credential.configured == jar
    assert load_credential(config).configured == jar


@pytest.mark.parametrize("override", [None, "member"])
async def test_a_sign_in_the_running_connection_cannot_confirm_is_not_active(
    live_ctx, monkeypatch, config, override
):
    """`validate_cookie` checks on a throwaway transport. If the connection the tools use
    then reads logged out, `active` is the old lie — and an `RTINGS_SESSION_OVERRIDE` must
    not paper over it, because it rewrites `session` and leaves `logged_in` as RTINGS said."""
    ctx = live_ctx(session_override=override)
    FakeRtings(accepts=()).install(monkeypatch)

    flow = signin(ctx)
    await flow._capture_and_store()

    assert flow.phase == "failed"
    assert flow.reason == "stored_session_not_active"
    assert flow.snapshot()["session"] == (override or "expired")
    # RTINGS accepted it on the check, so it stays stored for the next start to load.
    assert load_credential(config).configured == "new-cookie"
    assert "new-cookie" not in flow._failure_text(flow.reason)


async def test_a_cookie_stored_behind_the_running_server_is_adopted_before_it_is_called_live(
    live_ctx, monkeypatch, config
):
    """`rtings-mcp auth` stored a cookie while the server ran anonymously. `rt_sign_in`'s
    guard validated it and answered `session_active` — "RTINGS confirmed it live in this
    process" — while this process never sent it. Now it is adopted and probed first."""
    ctx = live_ctx()
    rtings = FakeRtings(accepts={"pasted-cookie"})
    rtings.install(monkeypatch)
    await ctx.transport.api_get_html("/tv/tools/table")  # anonymous use builds the jar
    store_credential(config, "pasted-cookie")  # the CLI, in another terminal
    opened: list[bool] = []

    flow = signin(ctx, opened=opened)
    await flow.start()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(asyncio.shield(flow._task), 5)

    assert (flow.phase, flow.reason) == ("refused", "session_active")
    assert opened == [], "no window may open for a cookie RTINGS still accepts"
    assert rtings.sent[-1] == "pasted-cookie"
    assert ctx.auth.cached_probe().logged_in


async def settle(flow):
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(asyncio.shield(flow._task), 5)


@pytest.mark.parametrize("override", [None, "member"])
async def test_the_verify_path_never_answers_session_active_for_a_logged_out_connection(
    live_ctx, monkeypatch, config, override
):
    """The guard's `session_active` says "RTINGS confirmed it live in this process". A stored
    cookie the throwaway check accepts but this connection reads logged out must not get it,
    and an override must not supply it."""
    store_credential(config, "stored-cookie")
    ctx = live_ctx(session_override=override)
    FakeRtings(accepts=()).install(monkeypatch)
    opened: list[bool] = []

    flow = signin(ctx, opened=opened)
    await flow.start()
    await settle(flow)

    assert (flow.phase, flow.reason) == ("failed", "stored_session_not_active")
    assert opened == []
    assert load_credential(config).configured == "stored-cookie"


@pytest.mark.parametrize("path", ["capture", "verify"])
async def test_a_connection_that_cannot_reach_rtings_is_no_verdict_on_the_cookie(
    live_ctx, monkeypatch, config, path
):
    """The throwaway check has its own cooldown dir, so it can accept a cookie while this
    connection is still cooling down from a 429. The probe then never reaches RTINGS and
    reads `unknown`. Calling that "not signed in" and advising a restart would blame the
    cookie for a transport fact."""
    from rtings_mcp.config import API_HOST

    if path == "verify":
        store_credential(config, "new-cookie")
    ctx = live_ctx()
    rtings = FakeRtings(accepts={"new-cookie"})
    rtings.install(monkeypatch)
    ctx.transport.cooldown.record_backoff(API_HOST, retry_after=60.0, reason="rate_limited")
    opened: list[bool] = []

    flow = signin(ctx, opened=opened)
    if path == "capture":
        await flow._capture_and_store()
    else:
        await flow.start()
        await settle(flow)
        assert opened == []

    assert flow.phase == "failed"
    assert flow.reason.startswith("could_not_confirm:"), flow.reason
    assert "cooldown_active" in flow.reason
    assert rtings.sent == [], "a cooling host gets no request"
    text = flow._failure_text(flow.reason)
    assert "no verdict" in text and "new-cookie" not in text
    assert load_credential(config).configured == "new-cookie"


async def test_adopting_a_cookie_never_borrows_a_newer_files_baseline(
    live_ctx, monkeypatch, config
):
    """The verify path read the stored cookie before `validate_cookie` went to the network.
    If another process stores a different one meanwhile, adopting ours with THAT file's
    `stored_at` would let our next rotation write-back overwrite the newer cookie."""
    store_credential(config, "cookie-a")
    ctx = live_ctx()
    FakeRtings(accepts={"cookie-a"}).install(monkeypatch)

    async def validate_while_another_process_writes(cfg, cookie):
        assert cookie == "cookie-a"
        store_credential(config, "cookie-b")  # e.g. `rtings-mcp auth` in another terminal
        return "member"

    opened: list[bool] = []
    flow = signin(ctx, opened=opened, validate=validate_while_another_process_writes)
    await flow.start()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(asyncio.shield(flow._task), 5)

    assert (flow.phase, flow.reason) == ("refused", "session_active")
    assert opened == []
    assert (await ctx.transport.current_jar_cookie()).startswith("rotated-")
    assert load_credential(config).configured == "cookie-b", "the newer file must survive"


async def test_a_response_still_in_flight_cannot_overwrite_the_new_cookie(config, monkeypatch):
    """A response on the wire carries a `Set-Cookie` for the previous session. Landing after
    the injection it would overwrite the new cookie, so the swap drains first, and a request
    that arrives meanwhile queues behind it and goes out with the new one."""
    transport = Transport(
        replace(config, rate_interval_s=0, concurrency=2),
        credential=CredentialState(configured="old-cookie", source="file"),
    )
    started, release = asyncio.Event(), asyncio.Event()
    sent: dict[str, str | None] = {}

    async def request(session, method, url, **kwargs):
        sent[url.rsplit("/", 1)[-1]] = session.get_cookie(SESSION_COOKIE_NAME, url)
        if url.endswith("/slow"):
            started.set()
            await release.wait()
            session.add_cookie(
                f"{SESSION_COOKIE_NAME}=old-rotated; Domain=.rtings.com; Path=/", BASE_URL
            )
        return FakeResponse(200, text="<html></html>", url=url)

    monkeypatch.setattr(AsyncSession, "request", request)
    tasks: list[asyncio.Task] = []
    try:
        tasks.append(asyncio.create_task(transport.api_get_html("/slow")))
        await started.wait()
        swap = asyncio.create_task(
            transport.replace_credential(CredentialState(configured="new-cookie", source="file"))
        )
        tasks.append(swap)
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(transport.api_get_html("/queued")))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not swap.done(), "the swap must wait for the response still on the wire"
        assert "queued" not in sent, "a request arriving during the swap queues behind it"
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert sent == {"slow": "old-cookie", "queued": "new-cookie"}
        assert await transport.current_jar_cookie() == "new-cookie"
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _close_transport(transport)


async def test_replacing_the_credential_keeps_the_jar_and_the_preview_meter(config):
    """Rebuilding the session would reset `product-previews`, which this server must never
    do, so the new cookie goes into the existing jar — over RTINGS' copy, not beside it."""
    transport = Transport(config, credential=CredentialState(configured="old-cookie"))
    session = await transport.api_session()
    session.add_cookie(
        f"{SESSION_COOKIE_NAME}=anonymous-remint; Domain=.rtings.com; Path=/; HttpOnly", BASE_URL
    )
    session.add_cookie("product-previews=spent; Domain=.rtings.com; Path=/", BASE_URL)
    session.add_cookie("other=kept; Path=/tv", BASE_URL + "/tv/tools/table")
    try:
        await transport.replace_credential(CredentialState(configured="new-cookie"))
        assert await transport.api_session() is session
        assert session.get_cookie(SESSION_COOKIE_NAME, BASE_URL) == "new-cookie"
        assert session.get_cookie("product-previews", BASE_URL) == "spent"
        assert session.get_cookie("other", BASE_URL + "/tv/tools/table") == "kept"
        copies = [
            c for c in session.cookie_scope_summary(BASE_URL) if c["name"] == SESSION_COOKIE_NAME
        ]
        assert len(copies) == 1, "two copies would both go out, and RTINGS picks one"
    finally:
        await _close_transport(transport)


async def test_a_credential_replaced_before_any_request_is_what_the_session_is_built_with(
    config,
):
    transport = Transport(config)
    await transport.replace_credential(CredentialState(configured="new-cookie"))
    try:
        assert await transport.current_jar_cookie() == "new-cookie"
    finally:
        await _close_transport(transport)
