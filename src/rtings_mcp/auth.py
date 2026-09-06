"""The auth seam: credential loading, the HTML probe, and the two-source join (SPEC §6).

The genuinely new problem, with no Consumer Reports analogue: **the JSON API response
carries no auth field at all.** Auth lives only in the HTML ``GLOBALS.session``. So a data
fetch (JSON) and an auth reading (HTML) are different requests, and they must be joined
without ever inferring auth from null data.

Three fields, three different questions:

* ``session`` — credential *health*, from the HTML probe only.
* ``data_tier`` — what the served *bytes* prove, in the safe direction only: an
  ``unblurred:true`` on an ``insider_only`` test proves the row was unblurred **for us**;
  absence proves nothing. Values are ``unblurred``/``unproven``, deliberately never
  ``member`` — a metered preview or a gift link unblurs too, and naming that tier ``member``
  would promote a non-member off a preview.
* ``auth_state`` — derived from the two, a summary for humans. Every rule in this codebase
  keys on ``session`` or ``data_tier`` directly, never on ``auth_state``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .cache import ANONYMOUS, FREE, MEMBER, Cache
from .config import SESSION_COOKIE_NAME, TTL_PROBE, Config
from .errors import RtingsError
from .fsutil import atomic_write_bytes, ensure_dir, file_lock
from .htmlprobe import (
    SessionProbe,
    classify_session,
    extract_globals,
    find_has_insider_access,
    find_membership_markers,
)
from .http import CredentialState, Transport

log = logging.getLogger(__name__)

DATA_TIER_UNBLURRED = "unblurred"
DATA_TIER_UNPROVEN = "unproven"

#: Why an ``anonymous``-labelled write was refused (:meth:`AuthManager.anonymous_write_refusal`).
#: An Early Access row that came through unblurred is member-only data on EVERY silo;
#: an unblurred ``insider_only`` row is member-only data only where the silo enforces, and
#: "unproven" is the honest word for a silo no signed-out fetch has yet shown to be open.
REFUSAL_EARLY_ACCESS = "early_access_unblurred"
REFUSAL_UNPROVEN_OPEN = "silo_not_proven_open"

#: Pinned to a NON-review page. The review path is the metered one, so probing there would
#: spend a free account's preview every time the server checks whether it is logged in —
#: and ``rt_product`` re-probes after every call.
PROBE_PATH = "/tv/tools/table"

_COOKIE_IN_CURL_RE = re.compile(
    rf"{SESSION_COOKIE_NAME}=([^;'\"\s]+)",
)

#: The derivation table. ``(session, data_tier) -> auth_state``.
#:
#: **Corrected 2026-09-03 against a live measurement.** The table used to map
#: ``(anonymous, unblurred)`` and ``(free, unblurred)`` to ``preview`` — "a share or gift
#: link". That was written when the model assumed every silo gates. It does not: on 16 of 28
#: silos an anonymous caller gets ``unblurred:true`` on ``insider_only`` tests as ordinary
#: behaviour (measured: mattress ``Thickness`` = 39.7 cm, ``Normalized Stiffness @ Lumbar``
#: = 42.98 Pa/mm, anonymous, no session at all). Reporting ``preview`` there invents a grant
#: nobody made — an entitlement claim from ordinary public data, which is the same class of
#: error as calling a metered preview ``member``.
#:
#: ``stale_member_data`` survives because it says something the data alone cannot: cached
#: unblurred rows served after the session died.
_AUTH_STATE: dict[tuple[str, str], str] = {
    ("member", DATA_TIER_UNBLURRED): "member",
    ("member", DATA_TIER_UNPROVEN): "member",
    ("free", DATA_TIER_UNBLURRED): "free",
    ("free", DATA_TIER_UNPROVEN): "free",
    ("anonymous", DATA_TIER_UNBLURRED): "anonymous",
    ("anonymous", DATA_TIER_UNPROVEN): "anonymous",
    # `stale_member_data` is NOT derived here: on the 16 open silos anonymous data is
    # `unblurred` as ordinary behaviour, so keying it on that bit tells a user whose cookie
    # expired that live anonymous mattress data is "cached member rows served after the
    # session died". It is derived from **stored provenance** instead — a served file whose
    # `cache_tier` is `member` — which is the only thing that actually means member data.
    ("expired", DATA_TIER_UNBLURRED): "expired",
    ("expired", DATA_TIER_UNPROVEN): "expired",
    ("unknown", DATA_TIER_UNBLURRED): "unproven_session",
    ("unknown", DATA_TIER_UNPROVEN): "unproven_session",
}


def derive_auth_state(
    session: str, data_tier: str, *, member_data_served: bool = False
) -> str:
    """``member_data_served`` comes from a served file's stored ``cache_tier``, never from
    the data. It is the only input that legitimately says "these are member rows"."""
    if session == "expired" and member_data_served:
        return "stale_member_data"
    return _AUTH_STATE.get((session, data_tier), "unproven_session")


def derive_data_tier(rows: list[dict[str, Any]], insider_ids: set[str]) -> str:
    """Read the data in the **safe direction only**.

    ``unblurred`` when at least one ``insider_only`` row came back unblurred for us;
    ``unproven`` otherwise. ``all null => anonymous`` is forbidden: it could equally be an
    expired session, and ``data_tier: unproven`` is the *normal* case for a member asking
    about public tests.
    """
    for row in rows:
        if not row.get("unblurred"):
            continue
        original_id = row.get("original_id")
        if original_id is not None and str(original_id) in insider_ids:
            return DATA_TIER_UNBLURRED
    return DATA_TIER_UNPROVEN


#: `table_tool__ratings` rows are `{original_id, product_id, score, suitable, unblurred,
#: usage}` — no `status`, and usage definitions carry no `insider_only`. Every rule keyed on
#: either of those is a no-op here, so the surface needs naming, not parameterising.
RATINGS_SURFACE = "ratings"


def _all_ratings_blurred(
    rows: list[dict[str, Any]], unpublished_product_ids: set[str]
) -> bool:
    """Did a whole ratings response come back withheld?

    The test-path predicate looks for `insider_only` rows with `status:"tested"`. A ratings
    row has neither field, so that loop skipped every row, `saw_insider_tested` never became
    true, and **write-time demotion was structurally dead on this surface** — a member's
    fully-blurred response after a lapsed session was still stamped `member` and served for
    the whole TTL, because a cache hit never re-probes.

    Usage ratings gate wholesale rather than per-flag, so every row is gate-relevant and
    `unblurred` alone decides. An all-Early-Access response stays vacuous, as on every other
    surface: an in-progress review is blurred for everyone and must not drive demotion.
    """
    saw_gateable = False
    for row in rows:
        product = row.get("product_id")
        if product is not None and str(product) in unpublished_product_ids:
            continue
        saw_gateable = True
        if row.get("unblurred"):
            return False
    return saw_gateable


def verdicts_contradict_tier(review: dict[str, Any], tier: str) -> bool:
    """Should a ``verdicts/`` write be demoted to ``anonymous``?

    This surface had **no** write-time demotion at all, so a member-tier write whose payload
    came back withheld was stamped ``member`` and served for the full 30-day reviews TTL —
    a cache hit never re-probes.

    **Deliberately NOT keyed on ``user_has_access``.** That is the payload's own blur flag,
    but it *appears* to track silo enforcement rather than membership (`false` on TV, `true`
    on mattress, both anonymous — `RECON.md` §12.16). Two data points is a lead, not a fact,
    and if it never flips for a member on a gated silo then demoting on it would demote every
    member write there, miss every read, and refetch forever — the exact deadlock the
    ``cache_tier``/``data_tier`` rule exists to prevent.

    Keyed instead on what the tier actually predicts: a member should see **usage scores**.
    All-null scores where the tier predicts otherwise is the same evidence an all-blurred
    slice is on the table path, and it does not depend on what ``user_has_access`` means. An
    empty score set stays vacuous, as everywhere else.
    """
    if tier == ANONYMOUS:
        return False
    entries = [e for e in (review.get("product_score_sets") or []) if isinstance(e, dict)]
    if not entries:
        return False
    return all(entry.get("score") is None for entry in entries)


def parse_curl_cookie(text: str) -> str | None:
    """Pull ``_rtings_session`` out of a pasted "Copy as cURL".

    ``_rtings_session`` is **HttpOnly**, so ``document.cookie`` cannot read it and cURL is
    the only capture gesture — there is no console fallback.
    """
    match = _COOKIE_IN_CURL_RE.search(text or "")
    return match.group(1) if match else None


def load_credential(config: Config) -> CredentialState:
    """Env var first, then the stored file. Never anywhere else, never a password."""
    if config.session_cookie_env:
        value = parse_curl_cookie(config.session_cookie_env) or config.session_cookie_env.strip()
        return CredentialState(configured=value, source="env")
    try:
        raw = json.loads(config.session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return CredentialState()
    value = raw.get(SESSION_COOKIE_NAME) if isinstance(raw, dict) else None
    if not value:
        return CredentialState()
    stored_at = 0.0
    with contextlib.suppress(TypeError, ValueError):
        stored_at = float(raw.get("stored_at") or 0.0)
    return CredentialState(configured=str(value), source="file", stored_at=stored_at)


def store_credential(
    config: Config, cookie_value: str, *, not_newer_than: float | None = None
) -> Path | None:
    """Persist the cookie ``0600`` in the config dir. Never the cache, never a log.

    ``not_newer_than`` makes the write a compare-and-swap for the **rotation** path: two
    processes sharing a config dir each load the credential once at startup, so without it
    a process holding a stale baseline can overwrite a rotation another process wrote
    seconds ago. Returns ``None`` when the write was skipped for that reason. A user-driven
    paste passes nothing and always wins — it is the newest fact by definition.
    """
    ensure_dir(config.config_dir)
    if not_newer_than is not None:
        try:
            existing = json.loads(config.session_file.read_text(encoding="utf-8"))
            stored_at = float(existing.get("stored_at") or 0.0)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            stored_at = 0.0
        if stored_at > not_newer_than:
            return None
    payload = json.dumps(
        {SESSION_COOKIE_NAME: cookie_value, "stored_at": time.time()}, separators=(",", ":")
    ).encode("utf-8")
    atomic_write_bytes(config.session_file, payload)
    return config.session_file


def clear_credential(config: Config) -> bool:
    try:
        config.session_file.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


@dataclass(slots=True)
class AuthManager:
    """Owns the probe, its persistence and the demand/write tier rules."""

    config: Config
    cache: Cache
    transport: Transport
    _probe: SessionProbe | None = None
    _warned_env_rotation: bool = False

    @property
    def probe_lock_path(self) -> Path:
        return self.cache.root / "probe" / "probe.lock"

    @property
    def probe_path(self) -> Path:
        # NEVER `session.json` — that is the credential file's name in the config dir and
        # the two must not be confusable.
        return self.cache.root / "probe" / "last_probe.json"

    # -- probe ----------------------------------------------------------------------

    def _load_persisted(self) -> SessionProbe | None:
        try:
            raw = json.loads(self.probe_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        try:
            probe = SessionProbe(
                session=str(raw["session"]),
                logged_in=bool(raw.get("logged_in")),
                access_level=raw.get("access_level"),
                preview_level=raw.get("preview_level"),
                access_limit=raw.get("access_limit"),
                previewed_products=[str(p) for p in raw.get("previewed_products", [])],
                has_insider_access=raw.get("has_insider_access"),
                probed_at=float(raw["probed_at"]),
                source_url=str(raw.get("source_url", "")),
                x_cache=raw.get("x_cache"),
                markers=raw.get("markers") or {},
                note=raw.get("note"),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return probe

    def _persist(self, probe: SessionProbe) -> None:
        payload = {
            "session": probe.session,
            "logged_in": probe.logged_in,
            "access_level": probe.access_level,
            "preview_level": probe.preview_level,
            "access_limit": probe.access_limit,
            "previewed_products": probe.previewed_products,
            "has_insider_access": probe.has_insider_access,
            "probed_at": probe.probed_at,
            "source_url": probe.source_url,
            "x_cache": probe.x_cache,
            "markers": probe.markers,
            "note": probe.note,
        }
        try:
            atomic_write_bytes(
                self.probe_path, json.dumps(payload, separators=(",", ":")).encode("utf-8")
            )
        except OSError:
            log.debug("could not persist the probe", exc_info=True)

    def cached_probe(self) -> SessionProbe | None:
        if self._probe is not None:
            return self._probe
        persisted = self._load_persisted()
        if persisted is not None:
            self._probe = persisted
        return self._probe

    def unknown_probe(self, note: str) -> SessionProbe:
        return SessionProbe(
            session="unknown",
            logged_in=False,
            access_level=None,
            preview_level=None,
            access_limit=None,
            previewed_products=[],
            has_insider_access=None,
            probed_at=time.time(),
            source_url="",
            note=note,
        )

    async def ensure_session(self) -> SessionProbe:
        """Resolve ``session`` for a tool call, lazily and at the lowest cost that is honest.

        **With no credential configured the answer needs no network at all**: without a
        credential you cannot be logged in, so the session is ``anonymous`` by construction.
        That is an inference from *our own configuration*, not from the response — the rule
        it must not break is "never infer auth from null data", and this touches no data.

        With a credential configured, only the HTML probe can tell ``member`` from ``free``
        from ``expired``, so it runs — once per process and then once per TTL, never per
        call, and never against a review page (that path is the metered one).
        """
        existing = self.cached_probe()
        if not self.transport.credential.present:
            if existing is not None and existing.session == "anonymous":
                return existing
            probe = SessionProbe(
                session="anonymous",
                logged_in=False,
                access_level=None,
                preview_level=None,
                access_limit=None,
                previewed_products=[],
                has_insider_access=None,
                probed_at=time.time(),
                source_url="",
                note="no credential configured, so no probe was needed",
            )
            self._probe = probe
            return probe
        return await self.session_probe()

    async def session_probe(self, *, force: bool = False) -> SessionProbe:
        """Return a probe, refreshing past ``TTL_PROBE`` or when ``force`` is set.

        ``force`` is used on the write-time demotion path, which must **never** be throttled
        — the read-path throttle exists so a cache hit is cheap, not so a write can be
        labelled from a stale answer.

        Read and written **inside the cross-process lock** at budget-check time: a second
        process reading a stale copy would let two processes each believe one preview
        remains.
        """
        async with file_lock(self.probe_lock_path):
            existing = self.cached_probe()
            if (
                not force
                and existing is not None
                and (time.time() - existing.probed_at) < TTL_PROBE
            ):
                return existing
            probe = await self._run_probe()
            self._probe = probe
            self._persist(probe)
            return probe

    async def _run_probe(self) -> SessionProbe:
        cookie_configured = self.transport.credential.present
        try:
            result = await self.transport.api_get_html(PROBE_PATH)
        except RtingsError as exc:
            return self.unknown_probe(f"probe failed: {exc.code}")
        try:
            globals_obj = extract_globals(result.text)
        except RtingsError:
            return self.unknown_probe("probe page carried no parseable GLOBALS")

        probe = classify_session(
            globals_obj,
            cookie_configured=cookie_configured,
            source_url=result.url,
            probed_at=time.time(),
            x_cache=result.headers.get("x-cache"),
            has_insider_access=find_has_insider_access(result.text),
            markers=find_membership_markers(result.text),
        )
        override = self.config.session_override
        if override and probe.session != override:
            log.info("RTINGS_SESSION_OVERRIDE=%s applied over probe=%s", override, probe.session)
            probe = replace(
                probe,
                session=override,
                note=(
                    f"session asserted by RTINGS_SESSION_OVERRIDE; the probe read "
                    f"{probe.session!r}"
                ),
            )

        # **Rotation write-back — required, and gated on proof (measured 2026-09-04).**
        #
        # RTINGS re-issues `_rtings_session` on EVERY response — HTML GET and API POST alike
        # — with a fresh `expires` of exactly 30 days from that response, and a new encrypted
        # value each time. So the 30 days is a **sliding idle window**, not a hard deadline
        # from login: the credential lives as long as it is used, and dies 30 days after it
        # stops. The login page has no "remember me" because it does not need one.
        #
        # An earlier draft refused to persist rotations at all, reasoning that an anonymous
        # GET also mints a cookie and a blind write-back would overwrite the credential with
        # an anonymous one. That danger is real; abstinence is the wrong answer to it.
        # Refusing to persist freezes the stored blob at the pasted value, so it expires 30
        # days after the paste **no matter how much the server is used** — the re-paste
        # treadmill, caused by us rather than by RTINGS.
        #
        # The guard is proof, not abstinence: persist only the jar value that just produced a
        # response with `current_user` non-null. An anonymous session can never satisfy that.
        if cookie_configured:
            jar_value = await self.transport.current_jar_cookie()
            if jar_value and jar_value != self.transport.credential.configured:
                if probe.logged_in:
                    self.transport.adopt_rotated_cookie(jar_value)
                    if self.transport.credential.source == "file":
                        try:
                            written = store_credential(
                                self.config,
                                jar_value,
                                not_newer_than=self.transport.credential.stored_at,
                            )
                            if written is not None:
                                # Advance the baseline, or our own next rotation compares
                                # against the pre-write stamp, sees the file as "newer",
                                # and skips every subsequent write-back.
                                self.transport.credential.stored_at = time.time()
                            else:
                                log.debug(
                                    "another process stored a newer session cookie; "
                                    "keeping ours in memory and not overwriting theirs"
                                )
                        except OSError:
                            log.warning(
                                "could not persist the re-issued session cookie; it stays "
                                "valid for this process but the stored copy will age out"
                            )
                    elif not self._warned_env_rotation:
                        self._warned_env_rotation = True
                        log.warning(
                            "RTINGS_SESSION_COOKIE cannot be refreshed automatically. The "
                            "session slides on use, so a STORED credential (`rtings-mcp "
                            "auth`) lasts indefinitely; the env var expires 30 days after "
                            "the value was minted."
                        )
                else:
                    self.transport.credential.jar_mismatch = True
        return probe

    def session_value(self) -> str:
        probe = self.cached_probe()
        return probe.session if probe else "unknown"

    # -- tiers ----------------------------------------------------------------------

    def probe_tier(self, probe: SessionProbe | None = None) -> str:
        """The ``cache_tier`` a probe justifies. ``expired``/``unknown`` demand ``anonymous``
        — a configured-but-expired cookie must not demand a tier that can never arrive.

        **Pinned to ``anonymous`` while member mode is off.** That was the Phase-0 gate:
        until a bought membership showed a cookie flipping ``unblurred`` on the API, no tier
        above ``anonymous`` could be demanded or written, because the server would have been
        keying its cache on an entitlement it had never observed. **Measured 2026-09-06
        (RECON §13.1) — 588/588 unblurred against 0/588 anonymous — so the default is now
        on**, and the pin is an opt-out rather than a gate.

        Turning it off is still supported and still resolves every tier to ``anonymous``.
        Note what that costs a signed-in user: the write guard then refuses to cache rows it
        cannot honestly label, so they are served and re-fetched on every call.
        """
        if not self.config.member_mode:
            return ANONYMOUS
        probe = probe or self.cached_probe()
        if probe is None:
            return ANONYMOUS
        if probe.session == MEMBER:
            return MEMBER
        if probe.session == FREE:
            return FREE
        return ANONYMOUS

    def demand_tier(self, surface: str, probe: SessionProbe | None = None) -> str:
        """The minimum ``cache_tier`` a read will accept, **probe vs probe**.

        ``free`` exists only on ``reviews/``: a free account unlocks nothing on the table
        path, so a ``free`` probe fetching ``tests/``/``ratings/`` would miss every
        ``anonymous`` slice and write a byte-identical ``free`` copy.
        """
        tier = self.probe_tier(probe)
        if surface == "reviews":
            return tier
        return MEMBER if tier == MEMBER else ANONYMOUS

    def write_tier(self, surface: str, probe: SessionProbe | None = None) -> str:
        """The ``cache_tier`` to stamp on a write.

        A successful probe is a precondition for any tier above ``anonymous``, and that is
        enforced at the call sites (``Repository._fetch_slice_chunk`` and
        ``Repository.review``), which re-probe **unthrottled** before writing — the read-path
        throttle exists so a cache hit is cheap, not so a write can be labelled from a stale
        answer.
        """
        return self.demand_tier(surface, probe)

    def should_demote(
        self,
        *,
        tier: str,
        surface: str,
        rows: list[dict[str, Any]],
        insider_ids: set[str],
        unpublished_product_ids: set[str],
        probe: SessionProbe | None,
        product_id: str | None = None,
    ) -> bool:
        """Write-time demotion (SPEC §8).

        The probe and the fetch race, so labelling a file with the last probe writes
        ``member`` over a response that came back fully blurred after the session lapsed —
        and the hit rule then serves those nulls to a re-authenticated member for the whole
        TTL, because a cache *hit* never re-probes.

        Demote to ``anonymous`` when the response contains ``insider_only`` rows with
        ``status:"tested"``, none of them ``unblurred:true``, **and** the tier predicts
        unblurred. Deliberately narrow: a public-only slice, an all-``na`` slice and a
        ``free`` table fetch are all vacuous under this predicate, so the tier deadlock does
        not come back through the side door.
        """
        if tier == ANONYMOUS:
            return False

        # Refuse to demote off a CloudFront-cached probe: if the probe page is served from
        # the anonymous cache to a member cookie, every write would demote and every read
        # would miss, forever.
        if probe is not None and probe.x_cache and "hit" in probe.x_cache.lower():
            return False

        predicts_unblurred = tier == MEMBER or (
            tier == FREE
            and surface == "reviews"
            and probe is not None
            and product_id is not None
            and product_id in set(probe.previewed_products)
        )
        if not predicts_unblurred:
            return False

        if surface == RATINGS_SURFACE:
            return _all_ratings_blurred(rows, unpublished_product_ids)

        saw_insider_tested = False
        for row in rows:
            if row.get("status") != "tested":
                continue
            original_id = row.get("original_id")
            if original_id is None or str(original_id) not in insider_ids:
                continue
            # An in-progress review is blurred for everyone, so it must not drive demotion:
            # a member fetching one would demote, then miss the hit rule on every later
            # call, reinstating the permanent refetch loop for every unpublished product.
            row_product = row.get("product_id")
            if row_product is not None and str(row_product) in unpublished_product_ids:
                continue
            saw_insider_tested = True
            if row.get("unblurred"):
                return False
        return saw_insider_tested

    # -- the anonymous-label guard (member mode OFF) ---------------------------------

    def session_may_unblur(self, probe: SessionProbe | None = None) -> bool:
        """Could the configured credential have unblurred what the API just served?

        This is the switch that keeps the whole guard **inert for the ordinary anonymous
        user**: with no credential configured nothing was sent, so nothing could have been
        unblurred *for us* — an inference from our own configuration, never from the data,
        and it costs no probe.

        With a credential configured the answer comes from the probe. ``member``, ``free``
        and ``unknown`` may have (a free account's metered preview unblurs a review, and an
        unknown probe proves nothing either way). ``expired``/``anonymous`` did not — a dead
        session cannot resurrect, so what came back is what anonymous gets — **unless the
        probe page was a CloudFront cache hit**, in which case the logged-out reading may be
        the CDN's anonymous copy answered to a live member cookie (the same hazard
        :meth:`should_demote` refuses to demote on, read in the other direction).
        """
        if not self.transport.credential.present:
            return False
        probe = probe or self.cached_probe()
        if probe is None:
            return True
        if probe.session in (MEMBER, FREE, "unknown"):
            return True
        return bool(probe.x_cache and "hit" in probe.x_cache.lower())

    def anonymous_write_refusal(
        self,
        *,
        surface: str,
        rows: list[dict[str, Any]],
        insider_ids: set[str],
        unpublished_product_ids: set[str],
        probe: SessionProbe | None,
        anonymous_serves: bool,
    ) -> str | None:
        """Why a response about to be labelled ``anonymous`` must NOT be, or ``None``.

        With ``RTINGS_MEMBER_MODE`` off every write is stamped ``anonymous`` — a label that
        promises "a signed-out session would have received these bytes". A member's fetch
        of an enforcing silo breaks that promise (tv: 588/588 ``insider_only`` rows
        unblurred for a member, 0/588 anonymously), and a cache hit on that file later
        serves member-only measurements to a signed-out user under an ``anonymous`` label.

        **The naive predicate — "logged in and unblurred insider rows" — over-fires on 16
        of 28 silos.** On mattress, air-purifier, vpn and the rest anonymous receives
        ``insider_only`` rows ``unblurred:true`` as ordinary behaviour, so a member's bytes
        there ARE what anonymous gets and the label is true. The two cases are
        indistinguishable from one response (both are 100% unblurred), so the tie-break is
        **an anonymous observation of the same (silo, bench)**: ``anonymous_serves`` is
        true when a signed-out fetch of this surface came back fully unblurred there
        (:meth:`ObservationStore.anonymous_serves`), and only then is "unblurred because
        the silo is open" the proven explanation.

        Two things are refused regardless of that proof:

        * an unblurred ``status:"tested"`` row for a ``published:false`` product — Early
          Access is blurred for anonymous on every silo, open ones included, and a
          membership is exactly what lifts it (``RECON.md`` §12.10), so such a row is
          member-only data wherever it appears;
        * nothing else: an unblurred ``na`` row is NOT evidence (47% of ``na`` rows are
          ``unblurred:true`` anonymously, ``RECON.md`` §11.4), and an unblurred **public**
          row proves nothing (public tests ship their value anonymously), so both stay
          vacuous — a guard that fired on them would refuse public-only slices forever.

        Usage rows (``ratings``, and verdict scores, which are usage ratings by another
        name) carry no ``status`` and no ``insider_only``; every unblurred one is
        gate-relevant, and the proof is the anonymous observation of the *usage* surface.

        Returns :data:`REFUSAL_EARLY_ACCESS`, :data:`REFUSAL_UNPROVEN_OPEN`, or ``None``
        when the ``anonymous`` label is honest. **Never** returns a refusal when
        :meth:`session_may_unblur` is false: an anonymous session cannot have unblurred
        anything, so its writes are honest by construction and this method is a no-op for
        it — no probe, no observation read, no warning.
        """
        if not self.session_may_unblur(probe):
            return None
        saw_gateable_unblurred = False
        for row in rows:
            if not row.get("unblurred"):
                continue
            product = row.get("product_id")
            unpublished = product is not None and str(product) in unpublished_product_ids
            if surface == RATINGS_SURFACE:
                if unpublished:
                    return REFUSAL_EARLY_ACCESS
                saw_gateable_unblurred = True
                continue
            if row.get("status") != "tested":
                continue
            if unpublished:
                return REFUSAL_EARLY_ACCESS
            original_id = row.get("original_id")
            if original_id is not None and str(original_id) in insider_ids:
                saw_gateable_unblurred = True
        if saw_gateable_unblurred and not anonymous_serves:
            return REFUSAL_UNPROVEN_OPEN
        return None

    # -- preview budget -------------------------------------------------------------

    def previews_remaining(self, probe: SessionProbe | None = None) -> int | None:
        probe = probe or self.cached_probe()
        return probe.previews_remaining if probe else None

    def preview_would_spend(self, product_id: str, probe: SessionProbe | None = None) -> bool:
        """True when fetching this review would consume one of a free account's previews.

        Anonymous has **zero** previews — measured: ``access_level 1`` sits one level below
        ``preview_level 2``, so it never had a budget, and no amount of page-walking changes
        ``access_state``. A member has none of this problem.
        """
        probe = probe or self.cached_probe()
        if probe is None or probe.session != FREE:
            return False
        return str(product_id) not in set(probe.previewed_products)


def envelope_notes_for(
    rows: list[dict[str, Any]], insider_ids: set[str], *, surface: str
) -> dict[str, Any]:
    """The per-file bit that never-downgrade and pruning key on.

    ``surface`` is required rather than defaulted: getting it wrong is silent, and it was
    wrong for ``ratings/`` — see :func:`_all_ratings_blurred`.
    """
    if surface == RATINGS_SURFACE:
        # `derive_data_tier` requires the row's `original_id` to be in `insider_ids`, but a
        # ratings row's id is a USAGE id and `insider_ids` holds TEST ids — a cross-namespace
        # lookup that is meaningless in both directions (and a numeric collision would make
        # it wrong rather than merely useless). A usage row's own `unblurred` bit is the
        # whole signal.
        has_unblurred = any(row.get("unblurred") for row in rows)
    else:
        has_unblurred = derive_data_tier(rows, insider_ids) == DATA_TIER_UNBLURRED
    return {"has_unblurred_insider": has_unblurred}
