"""Extraction of the three HTML-only facts (SPEC §6, §7, RECON §1, §5, §8).

The JSON API is the source for everything measurable, but three things live only in the
page and have no API equivalent:

* ``GLOBALS.static.silos`` — all 28 silos in one fetch (discovery, no crawl);
* ``GLOBALS.session`` — the **only** auth marker anywhere (the API response carries none);
* the per-silo bench list carrying ``is_recent`` — absent from ``column_options``.

Everything here is pure string/JSON work so it can be unit-tested against fixtures without
a network call.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import errors
from .errors import RtingsError

_GLOBALS_RE = re.compile(r"\bGLOBALS\s*=\s*(?=\{)")
_DATA_PROPS_RE = re.compile(r'data-props\s*=\s*"([^"]*)"', re.IGNORECASE)
_DATA_PROPS_SQ_RE = re.compile(r"data-props\s*=\s*'([^']*)'", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def page_title(html: str) -> str | None:
    """The one piece of a body it is safe to log or return (SPEC §9)."""
    match = _TITLE_RE.search(html)
    return html_module.unescape(match.group(1)).strip() if match else None


def _match_braces(text: str, start: int) -> str:
    """Return the balanced ``{...}`` beginning at ``start``, respecting strings/escapes."""
    if start >= len(text) or text[start] != "{":
        raise RtingsError(errors.PAYLOAD_MISSING, "expected an object literal")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise RtingsError(errors.PAYLOAD_MISSING, "unbalanced object literal in the page")


def extract_globals(html: str) -> dict[str, Any]:
    """Parse the inline ``var GLOBALS = {...}`` object.

    Raises ``payload_missing`` — a loud drift alarm — rather than returning an empty dict,
    because a silently empty ``GLOBALS`` would classify every session as anonymous.
    """
    match = _GLOBALS_RE.search(html)
    if not match:
        raise RtingsError(errors.PAYLOAD_MISSING, "no inline GLOBALS object in the page")
    blob = _match_braces(html, match.end())
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise RtingsError(errors.PAYLOAD_MISSING, "GLOBALS did not parse as JSON") from exc
    if not isinstance(parsed, dict):
        raise RtingsError(errors.PAYLOAD_MISSING, "GLOBALS is not an object")
    return parsed


def extract_data_props(html: str, *, contains: str | None = None) -> list[dict[str, Any]]:
    """Parse every ``data-props="..."`` blob in the page (HTML-escaped JSON).

    ``contains`` filters to blobs whose raw text mentions a marker key, which is how the
    review body and the recommendation page are picked out of a page with several
    components. Unparseable blobs are skipped, not fatal — only the caller knows whether
    the one it wanted was among them.
    """
    found: list[dict[str, Any]] = []
    for pattern in (_DATA_PROPS_RE, _DATA_PROPS_SQ_RE):
        for match in pattern.finditer(html):
            raw = html_module.unescape(match.group(1))
            if contains and contains not in raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
    return found


# -- session -------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SessionProbe:
    """The result of one HTML probe — credential health, never entitlement to data.

    ``session`` is the only thing allowed to say whether the caller is a member. The data
    says only whether a row was unblurred (a gift link or metered preview does that too).
    """

    session: str  # member | free | anonymous | expired | unknown
    logged_in: bool
    access_level: int | None
    preview_level: int | None
    access_limit: int | None
    previewed_products: list[str]
    has_insider_access: bool | None
    probed_at: float
    source_url: str
    #: ``X-Cache`` on the probe GET. If a member cookie is answered from CloudFront's
    #: anonymous cache, write-time demotion must be skipped or every write demotes and
    #: every read misses, forever (SPEC §8, Phase 0 capture f).
    x_cache: str | None = None
    note: str | None = None
    #: ``user_is_insider`` / ``membership_type`` / ``user_type`` from the page's analytics
    #: literals. Reported verbatim so a human can see exactly what the classifier saw.
    markers: dict[str, Any] = field(default_factory=dict)

    @property
    def previews_remaining(self) -> int | None:
        if self.access_limit is None:
            return None
        return max(0, self.access_limit - len(self.previewed_products))


def classify_session(
    globals_obj: dict[str, Any],
    *,
    cookie_configured: bool,
    source_url: str,
    probed_at: float,
    x_cache: str | None = None,
    has_insider_access: bool | None = None,
    markers: dict[str, Any] | None = None,
) -> SessionProbe:
    """Classify credential health from ``GLOBALS.session`` (RECON §5).

    Never from "a cookie is set", never from "scores are null", never from a UI string.

    Measured anonymously: ``access_level: 1``, ``preview_level: 2``, ``access_limit: null``
    — anonymous is one level *below* the preview threshold, so it never had a budget. The
    ladder the client's own code implies is 1 = anonymous, 2 = logged-in free, higher =
    insider.

    **Which field separates ``member`` from ``free`` is not yet measured** (only the
    anonymous shape has ever been seen — Phase 0 capture a). Until it is, a logged-in
    session is called ``member`` only on positive evidence (``has_insider_access`` or an
    access level above the preview threshold) and ``free`` otherwise, and the note records
    that the boundary is provisional.
    """
    session_obj = globals_obj.get("session")
    if not isinstance(session_obj, dict):
        return SessionProbe(
            session="unknown",
            logged_in=False,
            access_level=None,
            preview_level=None,
            access_limit=None,
            previewed_products=[],
            has_insider_access=has_insider_access,
            probed_at=probed_at,
            source_url=source_url,
            x_cache=x_cache,
            markers=dict(markers or {}),
            note="GLOBALS carried no session object",
        )

    current_user = session_obj.get("current_user")
    access_state = session_obj.get("access_state")
    access_state = access_state if isinstance(access_state, dict) else {}

    access_level = _as_int(access_state.get("access_level"))
    preview_level = _as_int(access_state.get("preview_level"))
    access_limit = _as_int(access_state.get("access_limit"))
    previewed = access_state.get("previewed_products")
    previewed_ids = [str(p) for p in previewed] if isinstance(previewed, list) else []

    logged_in = current_user is not None
    marker_map = dict(markers or {})
    insider_marker = marker_map.get("user_is_insider")
    note: str | None = None

    if not logged_in:
        # A configured cookie that comes back logged out is expiry, not anonymity.
        session = "expired" if cookie_configured else "anonymous"
    elif (
        insider_marker is True
        or has_insider_access is True
        or (
            access_level is not None
            and preview_level is not None
            and access_level > preview_level
        )
    ):
        # `userIsInsider` leads: it is a literal boolean naming exactly the distinction we
        # need. `has_insider_access` and the access-level comparison corroborate.
        session = "member"
    else:
        session = "free"
        note = (
            "logged in without a positive insider signal (userIsInsider="
            f"{insider_marker!r}, membership_type={marker_map.get('membership_type')!r}); "
            "the member/free boundary is provisional until a logged-in session has been "
            "measured — set RTINGS_SESSION_OVERRIDE=member if this is wrong"
        )

    return SessionProbe(
        session=session,
        logged_in=logged_in,
        access_level=access_level,
        preview_level=preview_level,
        access_limit=access_limit,
        previewed_products=previewed_ids,
        has_insider_access=has_insider_access,
        probed_at=probed_at,
        source_url=source_url,
        x_cache=x_cache,
        markers=marker_map,
        note=note,
    )


_INSIDER_ACCESS_RE = re.compile(r'"has_insider_access"\s*:\s*(true|false)')
_USER_IS_INSIDER_RE = re.compile(r"""['"]userIsInsider['"]\s*:\s*(true|false)""")
_MEMBERSHIP_TYPE_RE = re.compile(r"""['"]membership_type['"]\s*:\s*['"]([^'"]*)['"]""")
_USER_TYPE_RE = re.compile(r"""['"]user_type['"]\s*:\s*['"]([^'"]*)['"]""")


def find_membership_markers(html: str) -> dict[str, Any]:
    """Read the analytics markers, which name the member/free boundary directly.

    Every page carries a plain (unescaped) ``var TRACKING_PROPS = {...}`` with ``user_type``
    and ``membership_type``, plus a second literal with ``userIsInsider``. Anonymously they
    read ``"Visitor"``, ``"no plan"`` and ``false``.

    This matters because **which field separates ``member`` from ``free`` in
    ``GLOBALS.session`` has never been measured** — the access-level comparison is a guess
    read off the client bundle. ``userIsInsider`` is a literal boolean naming the thing we
    need, so it is the primary signal and the rest corroborate it. Phase 0 confirms what a
    real membership puts here; ``rtings-mcp auth --status`` prints all of it.
    """
    out: dict[str, Any] = {}
    insider = _USER_IS_INSIDER_RE.search(html)
    if insider:
        out["user_is_insider"] = insider.group(1) == "true"
    membership = _MEMBERSHIP_TYPE_RE.search(html)
    if membership:
        out["membership_type"] = membership.group(1)
    user_type = _USER_TYPE_RE.search(html)
    if user_type:
        out["user_type"] = user_type.group(1)
    return out


def find_has_insider_access(html: str) -> bool | None:
    """The corroborating page-prop signal. ``None`` when the page does not carry it.

    It lives inside a ``data-props`` attribute, i.e. **HTML-escaped** (``&quot;``), so a
    raw-text search silently never matches. Verified 2026-09-03 on ``/tv``: one occurrence,
    escaped. The raw form is tried first in case a page ever inlines it unescaped.
    """
    match = _INSIDER_ACCESS_RE.search(html)
    if match:
        return match.group(1) == "true"
    for raw in _DATA_PROPS_RE.finditer(html):
        inner = _INSIDER_ACCESS_RE.search(html_module.unescape(raw.group(1)))
        if inner:
            return inner.group(1) == "true"
    return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# -- silos and benches ---------------------------------------------------------------


def extract_silos(globals_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """``GLOBALS.static.silos`` — the category index. No A-Z page exists."""
    static = globals_obj.get("static")
    silos = static.get("silos") if isinstance(static, dict) else None
    if not isinstance(silos, list) or not silos:
        raise RtingsError(errors.PAYLOAD_MISSING, "GLOBALS.static.silos is absent or empty")
    return [s for s in silos if isinstance(s, dict) and s.get("url_part")]


def extract_bench_list(globals_obj: dict[str, Any]) -> dict[str, Any] | None:
    """Find the object carrying ``test_benches[]`` with ``is_recent`` flags.

    It lives at ``GLOBALS.static.silo`` — the *current* silo of the page being fetched
    (verified 2026-09-03: ``/mouse/tools/table`` yields ``latest 233``, recent ``[233,199]``,
    matching RECON §8) — so this is a **per-silo page fetch**, not something one page
    answers for all 28. The pinned path is tried first and a structural walk is the
    fallback, so a rename is degraded coverage rather than a hard failure.

    ``is_recent`` is a **server-set flag** and the only correct source of the default
    comparison set: the two plausible shortcuts ("top 3 in list order", "same major
    version") were both tested and both fail, and the set size varies per silo (tv 3,
    headphones 4, mouse 2). The page's bench list is also **longer** than
    ``column_options``' (TV: 18 vs 14) and its entries carry no ``display_name``, so bench
    *ids* come from here and bench *definitions* from the schema.
    """

    def build(node: dict[str, Any]) -> dict[str, Any] | None:
        benches = node.get("test_benches")
        if not isinstance(benches, list) or not benches:
            return None
        if not any(isinstance(b, dict) and "is_recent" in b for b in benches):
            return None
        return {
            "latest_test_bench_id": _as_str(node.get("latest_test_bench_id")),
            "test_benches": [
                {
                    "id": _as_str(b.get("id")),
                    "is_recent": bool(b.get("is_recent")),
                    "major": bool(b.get("major")),
                }
                for b in benches
                if isinstance(b, dict) and b.get("id") is not None
            ],
        }

    static = globals_obj.get("static")
    if isinstance(static, dict):
        silo = static.get("silo")
        if isinstance(silo, dict):
            pinned = build(silo)
            if pinned:
                return pinned

    best: dict[str, Any] | None = None

    def walk(node: Any, depth: int) -> None:
        nonlocal best
        if depth > 8 or best is not None:
            return
        if isinstance(node, dict):
            found = build(node)
            if found:
                best = found
                return
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(globals_obj, 0)
    return best


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)
