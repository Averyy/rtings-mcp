"""GLOBALS extraction and session classification — the HTML-only facts."""

from __future__ import annotations

import pytest

from rtings_mcp.errors import RtingsError
from rtings_mcp.htmlprobe import (
    classify_session,
    extract_bench_list,
    extract_data_props,
    extract_globals,
    extract_silos,
    find_has_insider_access,
    page_title,
)

ANON_GLOBALS = """
<html><head><title>TV Table Tool - RTINGS.com</title></head><body>
<script>var GLOBALS = {"session": {"current_user": null, "access_state":
 {"access_level": 1, "preview_level": 2, "access_limit": null,
  "previewed_products": []}},
 "static": {"silos": [{"url_part": "tv", "name": "TV", "has_paywall": true,
   "test_bench": {"id": "227", "name": "v2.2"}},
  {"url_part": "mattress", "name": "Mattress", "has_paywall": true,
   "test_bench": {"id": "236", "name": "v1.2"}}],
  "silo": {"latest_test_bench_id": "227", "test_benches":
   [{"id": "227", "is_recent": true, "major": false},
    {"id": "210", "is_recent": true, "major": false},
    {"id": "171", "is_recent": false, "major": true}]}},
 "ads": {}};</script>
<div data-props="{&quot;has_insider_access&quot;:false,&quot;silo_layout&quot;:{}}"></div>
</body></html>
"""

#: Shape only. A real logged-in `current_user` carries the user's name, email and
#: subscription details, so every value here is a placeholder and the cookie is absent.
MEMBER_GLOBALS = """
<script>var GLOBALS = {"session": {"current_user": {"id": "REDACTED",
 "name": "REDACTED", "email": "REDACTED"}, "access_state":
 {"access_level": 3, "preview_level": 2, "access_limit": 3,
  "previewed_products": ["1", "2"]}}, "static": {"silos": []}};</script>
"""


#: The shape a REAL member session returns, measured 2026-09-06 (RECON §13.2) and redacted:
#: `current_user.is_insider` is the field that separates member from free. Every string value
#: is a placeholder — the real object carries a name, an email and a subscription date.
INSIDER_GLOBALS = """
<script>var GLOBALS = {"session": {"current_user": {"id": "REDACTED",
 "email": "REDACTED", "username": "REDACTED", "is_insider": true,
 "insider_status": "REDACTED", "insider_end_at": "REDACTED",
 "is_confirmed": true, "is_admin": false, "paywall_test_account": null},
 "access_state": {"access_level": 1, "preview_level": 2, "access_limit": null,
  "previewed_products": []}}, "static": {"silos": []}};</script>
"""


def test_extract_globals_and_title():
    parsed = extract_globals(ANON_GLOBALS)
    assert set(parsed) == {"session", "static", "ads"}
    assert page_title(ANON_GLOBALS) == "TV Table Tool - RTINGS.com"


def test_missing_globals_is_a_loud_drift_alarm():
    """A silently empty GLOBALS would classify every session as anonymous."""
    with pytest.raises(RtingsError) as excinfo:
        extract_globals("<html><body>nothing here</body></html>")
    assert excinfo.value.code == "payload_missing"


def test_brace_matching_survives_braces_inside_strings():
    html = 'var GLOBALS = {"a": "} not the end {", "b": {"c": 1}};'
    assert extract_globals(html) == {"a": "} not the end {", "b": {"c": 1}}


def test_brace_matching_survives_escaped_quotes():
    html = r'var GLOBALS = {"a": "he said \"}\"", "b": 2};'
    assert extract_globals(html)["b"] == 2


def test_extract_silos():
    silos = extract_silos(extract_globals(ANON_GLOBALS))
    assert [s["url_part"] for s in silos] == ["tv", "mattress"]


def test_extract_silos_raises_when_absent():
    with pytest.raises(RtingsError):
        extract_silos({"static": {}})


def test_bench_list_uses_is_recent_not_list_order():
    """`[227, 210, 197]` is the TV answer, not the rule: 'top 3 in list order' and 'same
    major version' were both tested and both fail."""
    bench = extract_bench_list(extract_globals(ANON_GLOBALS))
    assert bench["latest_test_bench_id"] == "227"
    recent = [b["id"] for b in bench["test_benches"] if b["is_recent"]]
    assert recent == ["227", "210"]
    assert "171" not in recent  # newer-looking `major` bench is NOT recent


def test_anonymous_session_is_not_an_error():
    probe = classify_session(
        extract_globals(ANON_GLOBALS),
        cookie_configured=False,
        source_url="/tv/tools/table",
        probed_at=0.0,
    )
    assert probe.session == "anonymous"
    assert probe.logged_in is False
    assert probe.access_level == 1 and probe.preview_level == 2
    # Anonymous is one level BELOW the preview threshold: it never had a budget.
    assert probe.previews_remaining is None


def test_configured_cookie_that_comes_back_logged_out_is_expired():
    """Expiry and anonymity are different states, and only the cookie tells them apart."""
    probe = classify_session(
        extract_globals(ANON_GLOBALS),
        cookie_configured=True,
        source_url="/",
        probed_at=0.0,
    )
    assert probe.session == "expired"


def test_logged_in_without_positive_insider_evidence_is_free_and_says_so():
    probe = classify_session(
        extract_globals(MEMBER_GLOBALS),
        cookie_configured=True,
        source_url="/",
        probed_at=0.0,
    )
    assert probe.session == "member"  # access_level 3 > preview_level 2
    assert probe.previews_remaining == 1  # access_limit 3 - 2 previewed


def test_member_requires_positive_evidence():
    globals_obj = extract_globals(MEMBER_GLOBALS)
    globals_obj["session"]["access_state"]["access_level"] = 2
    probe = classify_session(
        globals_obj, cookie_configured=True, source_url="/", probed_at=0.0
    )
    assert probe.session == "free"
    assert probe.note  # the member/free boundary is provisional and says so


def test_current_user_is_insider_alone_decides_member():
    """The measured field leads, and it must work when nothing else corroborates.

    `INSIDER_GLOBALS` is deliberately hostile to the three older signals: no analytics
    marker, no `has_insider_access`, and `access_level 1` which is BELOW `preview_level 2`.
    Before `is_insider` was read, that combination classified a paying member as `free` —
    and per `config._session_override` a member read as free is quietly crippled: served
    cached anonymous nulls for up to 7 days and refused `rt_product` without
    `consume_preview`. Nothing here corroborates, which is the point.
    """
    probe = classify_session(
        extract_globals(INSIDER_GLOBALS),
        cookie_configured=True,
        source_url="/",
        probed_at=0.0,
    )
    assert probe.session == "member"
    assert probe.note is None, "a measured positive signal needs no provisional caveat"


def test_is_insider_is_read_strictly_so_a_truthy_value_cannot_promote():
    """`is True`, never truthiness. A string `"false"` is truthy in Python, so a loose check
    would promote every free account the day RTINGS changes the field to an enum."""
    for value in ("true", "false", 1, "insider", {}):
        globals_obj = extract_globals(INSIDER_GLOBALS)
        globals_obj["session"]["current_user"]["is_insider"] = value
        probe = classify_session(
            globals_obj, cookie_configured=True, source_url="/", probed_at=0.0
        )
        assert probe.session == "free", f"{value!r} is not a measured insider signal"


def test_is_insider_false_with_no_other_evidence_is_free_and_names_the_field():
    globals_obj = extract_globals(INSIDER_GLOBALS)
    globals_obj["session"]["current_user"]["is_insider"] = False
    probe = classify_session(
        globals_obj, cookie_configured=True, source_url="/", probed_at=0.0
    )
    assert probe.session == "free"
    assert "is_insider=False" in (probe.note or ""), "the note must say what it actually saw"


def test_a_missing_current_user_object_does_not_crash_the_classifier():
    """`current_user` is `null` for anonymous, so the `is_insider` lookup must tolerate it."""
    probe = classify_session(
        extract_globals(ANON_GLOBALS),
        cookie_configured=False,
        source_url="/",
        probed_at=0.0,
    )
    assert probe.session == "anonymous"


def test_has_insider_access_is_found_inside_escaped_data_props():
    """It lives in a data-props attribute, so a raw-text search silently never matches."""
    assert find_has_insider_access(ANON_GLOBALS) is False
    assert find_has_insider_access("<html></html>") is None


def test_data_props_parses_escaped_json():
    blobs = extract_data_props(ANON_GLOBALS, contains="silo_layout")
    assert blobs and blobs[0]["has_insider_access"] is False


def test_session_object_absent_yields_unknown_not_anonymous():
    probe = classify_session({}, cookie_configured=False, source_url="/", probed_at=0.0)
    assert probe.session == "unknown"
    assert probe.note
