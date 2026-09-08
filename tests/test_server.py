"""MCP wiring: the tools exist, and the seven-state enum reaches the client's schema."""

from __future__ import annotations

import json

import pytest

from rtings_mcp import server
from rtings_mcp.normalize import STATUS_VALUES

#: The eight data tools …
DATA_TOOLS = {
    "rt_silos",
    "rt_schema",
    "rt_ratings",
    "rt_product",
    "rt_graph",
    "rt_search",
    "rt_recommendations",
    "rt_article",
}
#: … and the two that connect a membership. They are separate here because the data tools all
#: carry `BaseEnvelopeOut` and these two deliberately do not: a sign-in serves no measurement
#: rows, so `data_tier` / `scores_available` / `test_benches` would be invented claims.
AUTH_TOOLS = {"rt_sign_in", "rt_auth_status"}
EXPECTED_TOOLS = DATA_TOOLS | AUTH_TOOLS


@pytest.fixture
async def tools():
    listed = await server.mcp.list_tools()
    return {tool.name: tool for tool in listed}


async def test_exactly_the_expected_tools_are_registered(tools):
    assert set(tools) == EXPECTED_TOOLS


async def test_the_sign_in_tools_are_not_wired_to_the_measurement_envelope(tools):
    """`rt_sign_in` serves no rows, so an envelope claiming a `data_tier` for it would be a
    statement about data nobody fetched."""
    for name in AUTH_TOOLS:
        blob = json.dumps(tools[name].output_schema)
        assert "data_tier" not in blob, f"{name} declares data_tier"
        assert "scores_available" not in blob, f"{name} declares scores_available"
        assert "session" in blob, f"{name} must still report credential health"


async def test_every_tool_declares_an_output_schema(tools):
    for name, tool in tools.items():
        assert tool.output_schema, f"{name} has no outputSchema"


async def test_the_seven_row_states_reach_the_client_schema(tools):
    """The distinction only protects an agent if the agent can see it before calling."""
    blob = json.dumps(tools["rt_ratings"].output_schema)
    for status in STATUS_VALUES:
        assert status in blob, f"{status} is missing from rt_ratings' outputSchema"


async def test_silo_is_a_hint_not_a_closed_enum(tools):
    """A JSON-Schema enum is enforced client-side, so a silo RTINGS adds mid-release would
    be unreachable until a new release ships."""
    params = tools["rt_ratings"].input_schema["properties"]["silo"]
    assert "enum" not in params
    assert "tv" in json.dumps(params)  # the hint is still there
    assert "mattress" in json.dumps(params)


async def test_tool_descriptions_state_the_gated_distinction(tools):
    text = " ".join((tool.description or "") for tool in tools.values()).lower()
    assert "gated" in text
    assert "not_tested" in text or "not tested" in text


async def test_server_instructions_warn_against_the_core_misreading():
    instructions = (server.mcp.instructions or "").lower()
    assert "tested_gated" in instructions
    assert "never report a gated null as 'not tested'" in instructions


async def test_rt_product_documents_its_cost(tools):
    description = tools["rt_product"].description or ""
    assert "consume_preview" in description
    assert "preview" in description.lower()


async def test_an_error_comes_back_as_a_structured_value_not_a_protocol_error(monkeypatch):
    """An agent must be able to tell 'RTINGS has no data' from 'the fetch failed'."""
    from rtings_mcp import errors, services
    from rtings_mcp.errors import RtingsError

    async def boom(*args, **kwargs):
        raise RtingsError(errors.UNKNOWN_SILO, "nope")

    monkeypatch.setattr(services, "rt_silos", boom)
    result = await server.rt_silos()
    assert result.error is not None
    assert result.error.code == "unknown_silo"
    assert result.data is None
    assert result.auth_state == "unproven_session"


# -- the lean-row serializer must never drop the safety fields ----------------------


def test_a_null_value_survives_serialization():
    """The output model omits null optionals to halve the payload. `value` and `gated` are
    exempt: "a gated value is null, never absent" is the project's core promise, and an
    agent must read row["value"] as None rather than hit a KeyError."""
    from rtings_mcp.models import ValueOut

    row = ValueOut(
        original_id="11",
        name="Native Contrast",
        kind="number",
        status="tested_gated",
        value=None,
        gated=True,
        insider_only=True,
    )
    dumped = row.model_dump(mode="json")
    assert "value" in dumped and dumped["value"] is None
    assert dumped["gated"] is True
    assert dumped["status"] == "tested_gated"
    # ...and the noise is gone.
    for empty in ("raw_value", "unit", "precision", "score", "display", "warning", "media"):
        assert empty not in dumped, f"{empty} was null and should have been omitted"


def test_a_visible_row_keeps_its_value_and_gated_false():
    from rtings_mcp.models import ValueOut

    dumped = ValueOut(
        original_id="208",
        name="Resolution",
        kind="word",
        status="tested_visible",
        value="4k",
        gated=False,
    ).model_dump(mode="json")
    assert dumped["value"] == "4k"
    assert dumped["gated"] is False


def test_an_empty_visible_row_keeps_the_null_pair():
    """A visible row can be genuinely empty; `{value: null, gated: null}` must survive."""
    from rtings_mcp.models import ValueOut

    dumped = ValueOut(
        original_id="32186", name="Odd", kind="number", status="tested_visible",
        value=None, gated=None,
    ).model_dump(mode="json")
    assert "value" in dumped and dumped["value"] is None
    assert "gated" in dumped and dumped["gated"] is None


def test_envelope_fields_are_never_dropped():
    """`error: null` means "no error" — a caller checking `out["error"] is None` must not
    get a KeyError, so envelopes deliberately do not inherit the lean serializer."""
    from rtings_mcp.models import SearchEnvelope

    dumped = SearchEnvelope(
        auth_state="anonymous", data_tier="unproven", session="anonymous"
    ).model_dump(mode="json")
    for key in ("error", "data", "scores_available", "sorted_by", "previews_remaining"):
        assert key in dumped, f"{key} must stay on the envelope even when null"


def test_the_instructions_gated_list_matches_the_enforcement_snapshot():
    """The "READ THIS FIRST" block names the 12 gated categories as static prose, and the
    calling LLM treats it as the authoritative routing signal. Unlike `SILO_HINT` it has no
    runtime fallback, and the release checklist never mentioned it — so a paywall shift
    would leave it confidently wrong with nothing to catch it.

    This is a DOC-vs-DOC guard, not a runtime read: `server.py` must never consult the
    snapshot (SPEC §10), and it does not — the server derives the split live via rt_silos.
    """
    import json
    from pathlib import Path

    snapshot = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "enforcement-snapshot.json").read_text()
    )
    enforcing = {
        silo for silo, row in snapshot["silos"].items() if row["enforces_paywall"]
    }
    instructions = server.mcp.instructions
    # Parse the parenthesised list rather than substring-matching: `vacuum` is a substring
    # of `robot-vacuum`, so a naive `in` check reports the open silo as gated.
    listed_text = instructions.split("the flagship ones (")[1].split(")")[0]
    listed = {token.strip() for token in listed_text.split(",") if token.strip()}

    open_silos = {
        silo for silo, row in snapshot["silos"].items() if not row["enforces_paywall"]
    }
    assert listed - enforcing == set(), (
        f"listed as gated but open in the snapshot: {sorted(listed - enforcing)}"
    )
    assert enforcing - listed == set(), (
        f"gate but are not listed: {sorted(enforcing - listed)}. "
        "Re-run `rtings-mcp scan` and update the instructions with the snapshot."
    )
    assert not (listed & open_silos)


#: Measured 2026-09-08 against Claude Code: the client cuts a tool description at exactly
#: 2048 characters and appends "… [truncated]". Anything past that is not read.
CLIENT_DESCRIPTION_LIMIT = 2048

#: What a caller cannot work the tool without. All of it must survive the cut — the filter
#: vocabulary was at character ~1540 of a 4,229-character description and the agent
#: filtered by `name_contains` and scanned sizes by eye instead (shopper round 3).
ESSENTIALS = {
    "rt_ratings": [
        "product_ids",
        "brand",
        "name_contains",
        "published",
        "variant",
        "sold_in",
        "No prefix means descending",
        "not applied",
        "tested_gated",
        "not_tested",
        "offset",
    ],
    "rt_schema": ["find=", "original_id"],
    "rt_product": ["include_verdicts"],
}


async def test_every_tool_description_fits_or_front_loads_the_client_budget(tools):
    """A description is cut at 2048 characters by the client, silently. A tool may run
    longer than that — the tail is reference — but nothing a caller NEEDS may sit past
    the cut, because they will never see it."""
    for name, tool in tools.items():
        visible = (tool.description or "")[:CLIENT_DESCRIPTION_LIMIT]
        for phrase in ESSENTIALS.get(name, []):
            assert phrase in visible, (
                f"{name}: {phrase!r} is past the client's {CLIENT_DESCRIPTION_LIMIT}-char "
                "cut, so no caller will ever read it"
            )


async def test_no_description_wastes_the_budget_on_indentation(tools):
    """CPython 3.13 strips a docstring's leading indentation at compile time and 3.12 does
    not, so the same source shipped a longer description on the older runtime — and the
    client's cut is at a fixed 2048 characters. Caught in CI 2026-09-08: `rt_ratings`'
    paging sat inside the window on 3.14 and outside it on 3.12."""
    for name, tool in tools.items():
        lines = (tool.description or "").splitlines()
        assert not any(line.startswith((" ", "\t")) for line in lines[:3]), (
            f"{name}: description carries source indentation; every leading space is a "
            "character the client's 2048-char cut spends on nothing"
        )


async def test_the_instructions_fit_the_client_budget():
    """The server's own routing prose is cut at 2048 characters, the same limit as a tool
    description (both measured 2026-09-08). It was 4,314: steps 3-8 and the whole sign-in
    section — including "NEVER call rt_sign_in unasked", which stops the server opening a
    browser window on the user's screen — never reached the calling LLM at all."""
    text = server.mcp.instructions or ""
    assert len(text) <= CLIENT_DESCRIPTION_LIMIT, (
        f"instructions are {len(text)} chars; everything past "
        f"{CLIENT_DESCRIPTION_LIMIT} is silently dropped"
    )
    # Per-tool detail belongs in each tool's own description. These are the things no
    # single tool can say, so losing any of them has no fallback.
    for phrase in (
        "rt_silos() FIRST",
        "tested_gated",
        "NEVER report a gated null",
        "grouped by bench",
        "rt_recommendations",
        "include_verdicts=true",
        "rt_article",
        "NO PRICES",
        "NEVER call rt_sign_in unasked",
    ):
        assert phrase in text, f"instructions no longer say {phrase!r}"
