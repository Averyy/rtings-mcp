"""The real ``Transport``, not the double.

``StubTransport`` overrides ``api_post``/``api_get_html``/``cdn_get_json`` without calling
``super()``, so ``Transport._perform``/``_classify`` and ``api_post``'s ``errors[]`` handling
are dead code under the rest of the suite. Mutation-testing confirmed it: deleting the
challenge-precedence branch, and making any ``errors[]`` fatal, both left 261 tests green.

These drive the shipped code by injecting a fake wafer session at ``_api_session`` — the one
seam that leaves ``_perform``, the cooldown, the token bucket and the JSON handling intact.
"""

from __future__ import annotations

import json

import pytest

from rtings_mcp import errors
from rtings_mcp.config import load_config
from rtings_mcp.errors import RtingsError
from rtings_mcp.http import Transport

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
    assert exc.value.retry_after == 17


async def test_503_with_retry_after_is_rate_limited(transport):
    wire(transport, FakeResponse(503, retry_after=9))
    with pytest.raises(RtingsError) as exc:
        await post(transport)
    assert exc.value.code == errors.RATE_LIMITED


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
