"""rtings-mcp — RTINGS.com test data as structured MCP tools.

The one thing to know before reading anything else: RTINGS gates some measurements
server-side, **per category**, and this server's whole job is to be structurally incapable of
lying about that. A gated value comes back ``null`` with ``status: "tested_gated"`` — which
is a different fact from ``not_tested``, ``not_applicable``, ``review_unpublished`` and
``coverage_unknown``, and the code never collapses two of them into one null.
"""

from __future__ import annotations

__version__ = "0.3.0"

__all__ = ["__version__"]
