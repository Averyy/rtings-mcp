"""The HTTP layer: two wafer sessions, our own shaping, and response classification
(SPEC §6, §9).

Design points that are load-bearing rather than stylistic:

* **Two sessions.** ``www.rtings.com`` carries the credential; ``i.rtings.com`` is an
  uncredentialed static-asset CDN. One session would force the origin's politeness floor
  onto 2-94 KB curve files *and* offer ``_rtings_session`` (``Domain=.rtings.com``) to the
  CDN for nothing.
* **``max_retries=0`` and ``max_rotations=0`` on both.** ``max_retries`` is a constructor
  kwarg — a per-request value is a ``TypeError`` at best and ignored at worst, which would
  have let ``product_vue_page__page_body`` run three times and consume three metered
  previews. ``max_rotations=0`` also makes wafer *return* 403/429/challenge/empty-200
  rather than raise, which is what "classify from the response, not an exception" needs.
* **Never a constructor ``headers=``.** It replaces wafer's self-consistent emulation
  envelope wholesale. Per-request headers merge, which is why the browser headers below
  are safe to set that way.
* **Never ``cache_dir=``.** It persists solver cookies to disk — a credential-shaped
  artifact this project writes nowhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import wafer
from wafer import AsyncSession

from . import errors
from .config import (
    API_BASE,
    API_HOST,
    BASE_URL,
    CDN_BASE,
    CDN_HOST,
    SESSION_COOKIE_NAME,
    Config,
)
from .errors import RtingsError
from .fsutil import ensure_dir
from .ratelimit import HostCooldown, TokenBucket

log = logging.getLogger(__name__)

#: ``test_results`` scales with rows, so the request side caps the test count and this
#: bounds the response side. ``ResponseTooLarge`` is an exception, not a status — it maps
#: to ``fetch_failed`` and the caller shrinks the request.
API_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CDN_MAX_RESPONSE_BYTES = 256 * 1024

API_TOTAL_TIMEOUT_S = 45.0
API_ATTEMPT_TIMEOUT_S = 20.0
CDN_TOTAL_TIMEOUT_S = 30.0
CDN_ATTEMPT_TIMEOUT_S = 15.0

#: Response headers worth recording. Never bodies, never cookies, never ``set-cookie``.
_TELEMETRY_HEADERS = (
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "x-cache",
    "age",
    "x-amz-cf-pop",
    "via",
    "server",
    "cache-control",
)


@dataclass(slots=True)
class FetchResult:
    """A successful fetch. Bodies never reach a log or a tool response."""

    status: int
    text: str
    headers: dict[str, str]
    url: str
    elapsed: float
    from_host: str
    json_body: Any = None


@dataclass(slots=True)
class CredentialState:
    """What the process knows about the configured credential (SPEC §6).

    Identity, not presence: RTINGS re-mints ``_rtings_session`` on a plain anonymous GET,
    so "a cookie is in the jar" is satisfied by RTINGS' own anonymous cookie.
    """

    configured: str | None = None
    source: str | None = None  # "env" | "file"
    #: Set when the jar's value stops matching the configured one. It triggers the HTML
    #: probe, never an immediate error — RTINGS may legitimately re-mint a *logged-in*
    #: session, and classifying that from the value alone throws away a good credential.
    jar_mismatch: bool = False

    @property
    def present(self) -> bool:
        return bool(self.configured)


class Transport:
    """Owns both wafer sessions, the buckets, the semaphore and the cooldown."""

    def __init__(
        self,
        config: Config,
        *,
        credential: CredentialState | None = None,
        cooldown: HostCooldown | None = None,
    ) -> None:
        self.config = config
        self.credential = credential or CredentialState()
        self.cooldown = cooldown or HostCooldown(config.cache_dir / "cooldown")
        self._api_bucket = TokenBucket(
            capacity=config.rate_burst, interval=config.rate_interval_s
        )
        self._cdn_bucket = TokenBucket(
            capacity=config.cdn_rate_burst, interval=config.cdn_rate_interval_s, jitter=0.05
        )
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._api_session: AsyncSession | None = None
        self._cdn_session: AsyncSession | None = None
        self._session_lock = asyncio.Lock()
        self._telemetry_path = config.cache_dir / "telemetry" / "requests.jsonl"

    # -- sessions -------------------------------------------------------------------

    async def api_session(self) -> AsyncSession:
        if self._api_session is None:
            async with self._session_lock:
                if self._api_session is None:
                    session = AsyncSession(
                        max_retries=0,
                        max_rotations=0,
                        max_failures=None,
                        rate_limit=0.0,
                        max_response_size=API_MAX_RESPONSE_BYTES,
                        timeout=API_TOTAL_TIMEOUT_S,
                        attempt_timeout=API_ATTEMPT_TIMEOUT_S,
                    )
                    self._inject_credential(session)
                    self._api_session = session
        return self._api_session

    async def cdn_session(self) -> AsyncSession:
        """Uncredentialed by construction — the per-session jar is what makes it so."""
        if self._cdn_session is None:
            async with self._session_lock:
                if self._cdn_session is None:
                    self._cdn_session = AsyncSession(
                        max_retries=0,
                        max_rotations=0,
                        max_failures=None,
                        rate_limit=0.0,
                        max_response_size=CDN_MAX_RESPONSE_BYTES,
                        timeout=CDN_TOTAL_TIMEOUT_S,
                        attempt_timeout=CDN_ATTEMPT_TIMEOUT_S,
                    )
        return self._cdn_session

    def _inject_credential(self, session: AsyncSession) -> None:
        """Inject the cookie with explicit attributes — a pasted ``name=value`` has none."""
        value = self.credential.configured
        if not value:
            return
        session.add_cookie(
            f"{SESSION_COOKIE_NAME}={value}; Domain=.rtings.com; Path=/; Secure; HttpOnly",
            BASE_URL,
        )

    async def check_credential_identity(self) -> bool:
        """True when the jar still holds the configured credential.

        Compares against the **configured** value, never presence: RTINGS sets a fresh
        ``_rtings_session`` on any anonymous GET, so a presence check passes when the
        credential is long gone. A mismatch sets ``jar_mismatch``, which the auth layer
        resolves with a probe rather than an immediate error.
        """
        if not self.credential.present:
            return True
        session = await self.api_session()
        jar_value = session.get_cookie(SESSION_COOKIE_NAME, BASE_URL)
        matched = jar_value == self.credential.configured
        self.credential.jar_mismatch = not matched
        return matched

    def adopt_rotated_cookie(self, value: str) -> None:
        """Adopt a server-rotated credential.

        **Only** after the HTML probe proved the rotating response was logged in
        (``current_user`` non-null): RTINGS re-mints this cookie on anonymous requests too,
        so writing back without that proof would replace the credential with an anonymous
        one. With the proof it is not merely safe but necessary — the session slides on
        every response, so a credential that is never refreshed expires 30 days after it was
        pasted however much it is used.
        """
        self.credential.configured = value
        self.credential.jar_mismatch = False

    async def current_jar_cookie(self) -> str | None:
        session = await self.api_session()
        return session.get_cookie(SESSION_COOKIE_NAME, BASE_URL)

    # -- request plumbing -----------------------------------------------------------

    def _preflight_cooldown(self, host: str) -> None:
        remaining = self.cooldown.remaining(host)
        if remaining > 0:
            raise RtingsError(
                errors.COOLDOWN_ACTIVE,
                f"a cooldown for {host} is still in force; no request was made",
                retry_after=round(remaining, 1),
            )

    def _classify(self, resp: Any, host: str, url: str) -> None:
        """Status precedence, first match wins (SPEC §7). Installs or clears the cooldown."""
        challenge = getattr(resp, "challenge_type", None)
        status = resp.status_code

        if challenge:
            state = self.cooldown.record_backoff(host, retry_after=None, reason=challenge)
            raise RtingsError(
                errors.CHALLENGED,
                f"a bot challenge was returned ({challenge})",
                retry_after=round(state.remaining, 1),
                details={"status": status},
            )

        retry_after = getattr(resp, "retry_after", None)
        if status == 429 or (status == 503 and retry_after is not None):
            state = self.cooldown.record_backoff(
                host, retry_after=retry_after, reason=f"http_{status}"
            )
            raise RtingsError(
                errors.RATE_LIMITED,
                f"RTINGS returned {status}",
                retry_after=round(retry_after if retry_after else state.remaining, 1),
                details={"status": status},
            )

        if status != 200:
            # A bare 503 with no Retry-After installs NO cooldown: we were not told to wait.
            raise RtingsError(
                errors.FETCH_FAILED,
                f"HTTP {status} from {host}",
                details={"status": status, "url": url},
            )

        if not (resp.text or "").strip():
            raise RtingsError(
                errors.FETCH_FAILED,
                "empty 200 response",
                details={"status": status, "url": url},
            )

        self.cooldown.clear(host)

    async def _perform(
        self,
        *,
        session: AsyncSession,
        method: str,
        url: str,
        host: str,
        bucket: TokenBucket,
        headers: dict[str, str] | None,
        json_body: dict[str, Any] | None,
        query_label: str,
        total_timeout: float,
        attempt_timeout: float,
    ) -> FetchResult:
        async with self._semaphore:
            # Order matters: cooldown before the token, so a cooling host does not burn one.
            self._preflight_cooldown(host)
            limiter_wait = await bucket.acquire()
            # Compute the budget AFTER the wait, or the queueing eats the attempt budget.
            started = time.monotonic()
            try:
                resp = await session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=total_timeout,
                    attempt_timeout=attempt_timeout,
                )
            except wafer.ResponseTooLarge as exc:
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, None, reason="response_too_large")
                raise RtingsError(
                    errors.FETCH_FAILED,
                    "response exceeded the size cap; shrink the request",
                    details={"limit": getattr(exc, "limit", None)},
                ) from exc
            except wafer.WaferTimeout as exc:
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, None, reason="timeout")
                raise RtingsError(errors.FETCH_FAILED, "request timed out") from exc
            except wafer.ChallengeDetected as exc:
                # Unreachable under max_rotations=0; kept so a settings change fails loudly.
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, None, reason=f"challenge:{exc.challenge_type}")
                state = self.cooldown.record_backoff(
                    host, retry_after=None, reason=exc.challenge_type
                )
                raise RtingsError(
                    errors.CHALLENGED,
                    f"a bot challenge was returned ({exc.challenge_type})",
                    retry_after=round(state.remaining, 1),
                ) from exc
            except wafer.RateLimited as exc:
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, 429, reason="rate_limited")
                state = self.cooldown.record_backoff(
                    host, retry_after=exc.retry_after, reason="rate_limited"
                )
                raise RtingsError(
                    errors.RATE_LIMITED,
                    "RTINGS rate-limited the request",
                    retry_after=round(exc.retry_after or state.remaining, 1),
                ) from exc
            except wafer.EmptyResponse as exc:
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, 200, reason="empty_200")
                raise RtingsError(errors.FETCH_FAILED, "empty 200 response") from exc
            except wafer.WaferError as exc:
                self._telemetry(query_label, host, None, time.monotonic() - started,
                                limiter_wait, None, reason=type(exc).__name__)
                raise RtingsError(
                    errors.FETCH_FAILED, f"transport error ({type(exc).__name__})"
                ) from exc

            elapsed = time.monotonic() - started
            self._telemetry(query_label, host, resp, elapsed, limiter_wait, resp.status_code)
            self._classify(resp, host, url)
            return FetchResult(
                status=resp.status_code,
                text=resp.text,
                headers=dict(resp.headers),
                url=resp.url,
                elapsed=elapsed,
                from_host=host,
            )

    # -- public fetches -------------------------------------------------------------

    async def api_post(
        self,
        query: str,
        body: dict[str, Any],
        *,
        referer: str | None = None,
        browser_headers: bool = True,
    ) -> Any:
        """POST one ``/api/v2/safe/<query>`` and return the parsed JSON.

        The browser headers go out **per request** on every call. The cookie is the sole
        credential (no CSRF), but server-side ``Origin``/``Referer``/``Sec-Fetch-*``
        validation is the one unprovable risk, and these remove it for free.

        ``browser_headers=False`` exists **only** to measure that risk in the one logged-in
        session that can (Phase 0 capture d). Without a switch here the question cannot be
        answered through the project's own transport at all. Never use it in a tool path.
        """
        url = f"{API_BASE}/{query}"
        headers = (
            {
                "Origin": BASE_URL,
                "Referer": referer or f"{BASE_URL}/",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            if browser_headers
            else None
        )
        result = await self._perform(
            session=await self.api_session(),
            method="POST",
            url=url,
            host=API_HOST,
            bucket=self._api_bucket,
            headers=headers,
            json_body=body,
            query_label=query,
            total_timeout=API_TOTAL_TIMEOUT_S,
            attempt_timeout=API_ATTEMPT_TIMEOUT_S,
        )
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise RtingsError(
                errors.PAYLOAD_MISSING,
                f"{query} returned a 200 that is not JSON",
            ) from exc
        if isinstance(payload, dict) and payload.get("errors"):
            # **`errors[]` beside `data` is a PARTIAL-FIELD notice, not a failure.**
            # Measured 2026-09-04: `distribution_tooltip__test` returns
            # "The field edit_url on an object of type Comparison was hidden due to
            # permissions" — three admin-only fields stripped — *and* a complete `data`
            # payload. Treating any errors[] as fatal throws that data away.
            #
            # A genuinely bad request looks nothing like this: `column_options` with an
            # unknown silo returns `{"data": {"silo": null}}` and **no errors[] at all**,
            # which `_dig` already reports as `payload_missing`. So `api_error` is reserved
            # for an errors[] with no data behind it.
            if payload.get("data") is None:
                raise RtingsError(
                    errors.API_ERROR,
                    f"{query} returned errors[] and no data",
                    details={"count": len(payload["errors"])},
                )
            log.debug(
                "%s: %d field(s) stripped by permissions; data returned",
                query,
                len(payload["errors"]),
            )
        return payload

    async def api_get_html(self, path: str) -> FetchResult:
        """GET one rtings.com page. Used for ``GLOBALS`` discovery, the auth probe and
        recommendations — the three HTML-only facts (SPEC §6, §7)."""
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        headers = {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-User": "?1",
            "Referer": f"{BASE_URL}/",
        }
        return await self._perform(
            session=await self.api_session(),
            method="GET",
            url=url,
            host=API_HOST,
            bucket=self._api_bucket,
            headers=headers,
            json_body=None,
            query_label="html",
            total_timeout=API_TOTAL_TIMEOUT_S,
            attempt_timeout=API_ATTEMPT_TIMEOUT_S,
        )

    async def cdn_get_json(self, path: str) -> Any:
        """GET a curve from ``i.rtings.com`` on the **uncredentialed** session."""
        url = path if path.startswith("http") else f"{CDN_BASE}{path}"
        result = await self._perform(
            session=await self.cdn_session(),
            method="GET",
            url=url,
            host=CDN_HOST,
            bucket=self._cdn_bucket,
            headers={"Referer": f"{BASE_URL}/", "Sec-Fetch-Site": "cross-site"},
            json_body=None,
            query_label="cdn_curve",
            total_timeout=CDN_TOTAL_TIMEOUT_S,
            attempt_timeout=CDN_ATTEMPT_TIMEOUT_S,
        )
        try:
            return json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise RtingsError(errors.PAYLOAD_MISSING, "curve JSON did not parse") from exc

    # -- telemetry ------------------------------------------------------------------

    def _telemetry(
        self,
        query: str,
        host: str,
        resp: Any,
        elapsed: float,
        limiter_wait: float,
        status: int | None,
        *,
        reason: str | None = None,
    ) -> None:
        """Append one header-only line per response (SPEC §9).

        This is how the open "behaviour under sustained volume" question gets answered from
        real use rather than by inducing a limit. **Never bodies, never cookies, never
        ``set-cookie``.**
        """
        if not self.config.telemetry:
            return
        record: dict[str, Any] = {
            "ts": time.time(),
            "host": host,
            "query": query,
            "status": status,
            "elapsed": round(elapsed, 3),
            "limiter_wait": round(limiter_wait, 3),
        }
        if reason:
            record["reason"] = reason
        if resp is not None:
            record["retries"] = getattr(resp, "retries", None)
            record["rotations"] = getattr(resp, "rotations", None)
            record["challenge_type"] = getattr(resp, "challenge_type", None)
            headers = getattr(resp, "headers", {}) or {}
            for key in _TELEMETRY_HEADERS:
                if key in headers:
                    record[key] = headers[key]
        try:
            ensure_dir(self._telemetry_path.parent)
            with self._telemetry_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            log.debug("telemetry append failed", exc_info=True)


@dataclass(slots=True)
class SingleFlight:
    """Dedupe concurrent fetches of the same key within one process (SPEC §9).

    Cross-process duplication is the cache lock's job; this only stops one process from
    pulling the same payload twice.
    """

    _inflight: dict[str, asyncio.Future] = field(default_factory=dict)

    async def run(self, key: str, factory):  # type: ignore[no-untyped-def]
        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[key] = future
        try:
            result = await factory()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            # Consume the exception so a discarded future does not warn at GC time.
            future.exception()
            raise
        else:
            if not future.done():
                future.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)


def cache_root_for(config: Config) -> Path:
    ensure_dir(config.cache_dir)
    return config.cache_dir
