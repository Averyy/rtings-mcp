"""The auth seam: data_tier in the safe direction, the derivation table, tier rules."""

from __future__ import annotations

import time

import pytest

from rtings_mcp import auth as auth_module
from rtings_mcp.auth import (
    DATA_TIER_UNBLURRED,
    DATA_TIER_UNPROVEN,
    AuthManager,
    derive_auth_state,
    derive_data_tier,
    load_credential,
    parse_curl_cookie,
    store_credential,
)
from rtings_mcp.cache import ANONYMOUS, MEMBER
from rtings_mcp.config import load_config
from rtings_mcp.htmlprobe import SessionProbe

INSIDER = {"11", "12000"}


def probe(session="anonymous", **over):
    base = dict(
        session=session,
        logged_in=session in {"member", "free"},
        access_level=1,
        preview_level=2,
        access_limit=None,
        previewed_products=[],
        has_insider_access=None,
        probed_at=time.time(),
        source_url="/tv/tools/table",
    )
    base.update(over)
    return SessionProbe(**base)


# -- data_tier is read in the SAFE direction only ---------------------------------


def test_unblurred_insider_row_proves_the_row_came_through_unblurred():
    rows = [{"original_id": "11", "unblurred": True}]
    assert derive_data_tier(rows, INSIDER) == DATA_TIER_UNBLURRED


def test_all_null_is_unproven_never_anonymous():
    """`all null => anonymous` is forbidden: it could equally be an expired session."""
    rows = [{"original_id": "11", "unblurred": False}]
    assert derive_data_tier(rows, INSIDER) == DATA_TIER_UNPROVEN


def test_an_unblurred_PUBLIC_row_proves_nothing():
    """A public test is unblurred anonymously, so it carries no tier information."""
    rows = [{"original_id": "208", "unblurred": True}]
    assert derive_data_tier(rows, INSIDER) == DATA_TIER_UNPROVEN


def test_empty_response_is_unproven():
    assert derive_data_tier([], INSIDER) == DATA_TIER_UNPROVEN


# -- auth_state derivation --------------------------------------------------------


def test_member_with_unproven_data_is_still_member_not_a_warning():
    """The normal case for a member asking about public tests."""
    assert derive_auth_state("member", DATA_TIER_UNPROVEN) == "member"


def test_anonymous_with_unblurred_data_is_anonymous_not_preview():
    """On 16 of 28 silos an anonymous caller gets unblurred insider values as ordinary
    behaviour. Calling that `preview` would invent a grant nobody made."""
    assert derive_auth_state("anonymous", DATA_TIER_UNBLURRED) == "anonymous"
    assert derive_auth_state("free", DATA_TIER_UNBLURRED) == "free"


def test_expired_session_with_cached_member_rows_says_both_things():
    """One enum cannot say 'this data is real' and 'your session is dead' — this state can.

    But it is derived from stored provenance (a served file's `cache_tier`), never from the
    `unblurred` bit: on the 16 open silos anonymous data is unblurred as ordinary behaviour,
    so keying on that bit would call live anonymous mattress data "stale member rows".
    """
    assert derive_auth_state("expired", DATA_TIER_UNBLURRED) == "expired"
    assert derive_auth_state("expired", DATA_TIER_UNPROVEN) == "expired"
    assert (
        derive_auth_state("expired", DATA_TIER_UNBLURRED, member_data_served=True)
        == "stale_member_data"
    )


def test_a_session_override_is_a_user_assertion_not_an_inference(tmp_path):
    """Which field separates member from free has never been measured. If the classifier
    guesses `free` for a real member, that member is served cached nulls for a week."""
    from rtings_mcp.config import load_config as _load

    config = _load(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "c"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "cfg"),
            "RTINGS_SESSION_OVERRIDE": "member",
        }
    )
    assert config.session_override == "member"

    bad = _load(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "c2"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "cfg2"),
            "RTINGS_SESSION_OVERRIDE": "insider",
        }
    )
    assert bad.session_override is None
    assert any("RTINGS_SESSION_OVERRIDE" in w for w in bad.warnings)


def test_unknown_session_never_claims_anything():
    assert derive_auth_state("unknown", DATA_TIER_UNBLURRED) == "unproven_session"


# -- credential handling ----------------------------------------------------------


def test_parse_curl_cookie_extracts_only_the_one_cookie():
    curl = (
        "curl 'https://www.rtings.com/tv' -H 'Cookie: exp_user_id=abc; "
        "_rtings_session=SECRETVALUE123; current-pageviews=3' -H 'Accept: */*'"
    )
    assert parse_curl_cookie(curl) == "SECRETVALUE123"


def test_parse_curl_cookie_returns_none_when_absent():
    assert parse_curl_cookie("curl 'https://www.rtings.com/'") is None
    assert parse_curl_cookie("") is None


def test_store_and_load_credential_is_owner_only(tmp_path):
    config = load_config(
        {"RTINGS_CONFIG_DIR": str(tmp_path / "cfg"), "RTINGS_CACHE_DIR": str(tmp_path / "c")}
    )
    path = store_credential(config, "VALUE")
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert load_credential(config).configured == "VALUE"
    assert load_credential(config).source == "file"


def test_env_var_takes_precedence_and_accepts_a_pasted_curl(tmp_path):
    config = load_config(
        {
            "RTINGS_CONFIG_DIR": str(tmp_path / "cfg"),
            "RTINGS_CACHE_DIR": str(tmp_path / "c"),
            "RTINGS_SESSION_COOKIE": "Cookie: _rtings_session=FROMENV; other=1",
        }
    )
    store_credential(config, "FROMFILE")
    credential = load_credential(config)
    assert credential.configured == "FROMENV" and credential.source == "env"


def test_the_credential_file_is_never_in_the_cache_dir(tmp_path):
    config = load_config(
        {"RTINGS_CONFIG_DIR": str(tmp_path / "cfg"), "RTINGS_CACHE_DIR": str(tmp_path / "c")}
    )
    assert not config.session_file.is_relative_to(config.cache_dir)
    assert config.session_file.name == "session.json"


# -- tier rules -------------------------------------------------------------------


@pytest.fixture
def auth(tmp_path):
    from rtings_mcp.cache import Cache
    from rtings_mcp.http import Transport

    config = load_config(
        {"RTINGS_CACHE_DIR": str(tmp_path / "c"), "RTINGS_CONFIG_DIR": str(tmp_path / "cfg")}
    )
    return AuthManager(config=config, cache=Cache(config), transport=Transport(config))


@pytest.fixture
def member_auth(tmp_path):
    from rtings_mcp.cache import Cache
    from rtings_mcp.http import Transport

    config = load_config(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "c2"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "cfg2"),
            "RTINGS_MEMBER_MODE": "1",
        }
    )
    return AuthManager(config=config, cache=Cache(config), transport=Transport(config))


def test_member_mode_off_pins_every_tier_to_anonymous(auth):
    """Whether a membership cookie flips `unblurred` on the API is unmeasured, so no tier
    above anonymous may be demanded or written until it is."""
    assert auth.probe_tier(probe("member")) == ANONYMOUS
    assert auth.demand_tier("tests", probe("member")) == ANONYMOUS
    assert auth.demand_tier("reviews", probe("member")) == ANONYMOUS


def test_free_never_demands_a_tier_on_the_table_path(member_auth):
    """A free account unlocks nothing there, so a `free` slice would be a byte-identical
    duplicate of the anonymous one."""
    assert member_auth.demand_tier("tests", probe("free")) == ANONYMOUS
    assert member_auth.demand_tier("ratings", probe("free")) == ANONYMOUS
    assert member_auth.demand_tier("reviews", probe("free")) == "free"


def test_expired_and_unknown_demand_anonymous(member_auth):
    """A configured-but-expired cookie must not demand a tier that can never arrive."""
    assert member_auth.demand_tier("reviews", probe("expired")) == ANONYMOUS
    assert member_auth.demand_tier("reviews", probe("unknown")) == ANONYMOUS


# -- write-time demotion ----------------------------------------------------------


def demote(auth, **over):
    kwargs = dict(
        tier=MEMBER,
        surface="tests",
        rows=[{"original_id": "11", "status": "tested", "unblurred": False, "product_id": "1"}],
        insider_ids=INSIDER,
        unpublished_product_ids=set(),
        probe=probe("member"),
    )
    kwargs.update(over)
    return auth.should_demote(**kwargs)


def test_demotes_when_the_response_contradicts_a_member_probe(member_auth):
    assert demote(member_auth) is True


def test_does_not_demote_when_any_insider_row_came_back_unblurred(member_auth):
    rows = [{"original_id": "11", "status": "tested", "unblurred": True, "product_id": "1"}]
    assert demote(member_auth, rows=rows) is False


def test_a_public_only_slice_is_vacuous(member_auth):
    """Otherwise the tier deadlock returns through the side door."""
    rows = [{"original_id": "208", "status": "tested", "unblurred": True, "product_id": "1"}]
    assert demote(member_auth, rows=rows) is False


def test_an_all_na_slice_is_vacuous(member_auth):
    rows = [{"original_id": "11", "status": "na", "unblurred": False, "product_id": "1"}]
    assert demote(member_auth, rows=rows) is False


def test_unpublished_products_never_drive_demotion(member_auth):
    """An in-progress review is blurred for everyone; demoting on it would reinstate the
    permanent refetch loop for every unpublished product."""
    assert demote(member_auth, unpublished_product_ids={"1"}) is False


def test_anonymous_tier_never_demotes(member_auth):
    assert demote(member_auth, tier=ANONYMOUS) is False


def test_a_cloudfront_cached_probe_blocks_demotion(member_auth):
    """If the probe page is served from the anonymous cache to a member cookie, every write
    would demote and every read would miss, forever."""
    assert demote(member_auth, probe=probe("member", x_cache="Hit from cloudfront")) is False


def test_free_on_the_table_path_is_vacuous(member_auth):
    assert demote(member_auth, tier="free") is False


def test_free_on_a_review_it_already_previewed_predicts_unblurred(member_auth):
    """A metered preview is believed to unblur that specific review, so a blurred response
    for a product already in `previewed_products` contradicts the probe the same way a
    member's does. Inverting this check left the whole suite green — it had no test."""
    previewed = probe("free", previewed_products=["7"])
    assert (
        demote(
            member_auth,
            tier="free",
            surface="reviews",
            product_id="7",
            probe=previewed,
        )
        is True
    )


def test_free_on_a_review_it_has_not_previewed_is_vacuous(member_auth):
    """Nothing predicted it would be unblurred, so a blurred response proves nothing."""
    assert (
        demote(
            member_auth,
            tier="free",
            surface="reviews",
            product_id="7",
            probe=probe("free", previewed_products=[]),
        )
        is False
    )


# -- write-time demotion on `ratings/`, which has neither field the test path branches on --

#: A ratings row: no `status`, and its `original_id` is a USAGE id, not a test id.
RATING_ROWS = [
    {"original_id": "1", "product_id": "1", "score": None, "unblurred": False},
    {"original_id": "2", "product_id": "2", "score": None, "unblurred": False},
]


def test_a_fully_blurred_ratings_response_demotes(member_auth):
    """The test-path predicate needs `status:"tested"` and an `insider_only` id. A ratings
    row has neither, so that loop skipped every row and demotion was structurally DEAD on
    this surface — a member's blurred response was stamped `member` and served for the whole
    TTL, because a cache hit never re-probes."""
    assert demote(member_auth, surface="ratings", rows=RATING_ROWS) is True


def test_a_ratings_response_with_any_unblurred_score_does_not_demote(member_auth):
    rows = [dict(RATING_ROWS[0]), {**RATING_ROWS[1], "unblurred": True, "score": 7.7}]
    assert demote(member_auth, surface="ratings", rows=rows) is False


def test_an_all_early_access_ratings_response_is_vacuous(member_auth):
    """Blurred for everyone, so it cannot be evidence the session lapsed."""
    assert (
        demote(
            member_auth,
            surface="ratings",
            rows=RATING_ROWS,
            unpublished_product_ids={"1", "2"},
        )
        is False
    )


def test_an_empty_ratings_response_is_vacuous(member_auth):
    assert demote(member_auth, surface="ratings", rows=[]) is False


def test_a_ratings_file_records_its_unblurred_rows_for_pruning(member_auth):
    """`has_unblurred_insider` is the key pruning and never-downgrade read. Deriving it via
    test-scoped `insider_ids` made it always False for ratings, so the "keep the newest file
    holding unblurred data" exemption could never fire — a later blurred refetch would prune
    away the one file holding a member's real scores."""
    unblurred = [{**RATING_ROWS[0], "unblurred": True, "score": 7.7}]
    notes = auth_module.envelope_notes_for(unblurred, INSIDER, surface="ratings")
    assert notes["has_unblurred_insider"] is True
    blurred = auth_module.envelope_notes_for(RATING_ROWS, INSIDER, surface="ratings")
    assert blurred["has_unblurred_insider"] is False


# -- preview budget ---------------------------------------------------------------


def test_anonymous_has_no_previews_to_spend(auth):
    auth._probe = probe("anonymous")
    assert auth.preview_would_spend("39008") is False
    assert auth.previews_remaining() is None


def test_free_account_spends_only_on_a_product_not_already_previewed(auth):
    auth._probe = probe("free", access_limit=3, previewed_products=["1", "2"])
    assert auth.preview_would_spend("1") is False
    assert auth.preview_would_spend("39008") is True
    assert auth.previews_remaining() == 1


# -- write-time demotion on `verdicts/`, which has a THIRD row shape ----------------


def test_a_verdicts_payload_with_no_scores_demotes(member_auth):
    """This surface had no demotion at all, so a withheld payload written under a member
    probe was served as member data for the full 30-day reviews TTL."""
    review = {"product_score_sets": [{"score": None}, {"score": None}]}
    assert auth_module.verdicts_contradict_tier(review, MEMBER) is True


def test_a_verdicts_payload_with_any_score_does_not_demote(member_auth):
    review = {"product_score_sets": [{"score": None}, {"score": 7.7}]}
    assert auth_module.verdicts_contradict_tier(review, MEMBER) is False


def test_an_empty_verdicts_payload_is_vacuous(member_auth):
    assert auth_module.verdicts_contradict_tier({}, MEMBER) is False
    assert auth_module.verdicts_contradict_tier({"product_score_sets": []}, MEMBER) is False


def test_verdicts_demotion_ignores_user_has_access(member_auth):
    """`user_has_access` appears to track SILO ENFORCEMENT, not membership (`false` on TV,
    `true` on mattress, both anonymous). If it never flips for a member on a gated silo,
    demoting on it would demote every member write there and miss every read — forever."""
    scored_but_flagged = {
        "user_has_access": False,
        "product_score_sets": [{"score": 8.1}],
    }
    assert auth_module.verdicts_contradict_tier(scored_but_flagged, MEMBER) is False


def test_anonymous_verdicts_never_demote(member_auth):
    review = {"product_score_sets": [{"score": None}]}
    assert auth_module.verdicts_contradict_tier(review, ANONYMOUS) is False
