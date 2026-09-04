"""MCP wiring: the tools exist, and the seven-state enum reaches the client's schema."""

from __future__ import annotations

import json

import pytest

from rtings_mcp import server
from rtings_mcp.normalize import STATUS_VALUES

EXPECTED_TOOLS = {
    "rt_silos",
    "rt_schema",
    "rt_ratings",
    "rt_product",
    "rt_graph",
    "rt_search",
    "rt_recommendations",
}


@pytest.fixture
async def tools():
    listed = await server.mcp.list_tools()
    return {tool.name: tool for tool in listed}


async def test_exactly_the_seven_tools_are_registered(tools):
    assert set(tools) == EXPECTED_TOOLS


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
