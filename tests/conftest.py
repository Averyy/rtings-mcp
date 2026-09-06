"""Shared fixtures.

Every fixture here is either synthetic or captured from an **anonymous** fetch. No fixture
in this repo may contain unblurred member values, and any ``GLOBALS.session`` shape is
redacted — ``current_user`` carries a real person's name and email.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtings_mcp.cache import Cache
from rtings_mcp.config import Config, load_config
from rtings_mcp.schema import parse_column_options

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolate_from_the_developers_own_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No test may read the machine's real cache dir, config dir or cookie.

    The ``config`` and ``ctx`` fixtures redirect both dirs, but a test that reaches a tool
    through ``server.rt_*`` goes via the process-wide ``get_context()``, which calls
    ``load_config()`` on the real environment. Once a membership is signed in, that made
    ``test_an_error_comes_back_as_a_structured_value_not_a_protocol_error`` fail with
    ``auth_state == "member"``: the singleton had picked up the developer's own
    ``~/.cache/rtings-mcp/probe/last_probe.json``. The suite is supposed to be offline and
    anonymous, so whether it passes must not depend on who is signed in — and nothing here
    may write into a real cache dir or read a real credential.
    """
    monkeypatch.setenv("RTINGS_CACHE_DIR", str(tmp_path / "auto-cache"))
    monkeypatch.setenv("RTINGS_CONFIG_DIR", str(tmp_path / "auto-config"))
    monkeypatch.delenv("RTINGS_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("RTINGS_SESSION_OVERRIDE", raising=False)
    monkeypatch.delenv("RTINGS_MEMBER_MODE", raising=False)

    from rtings_mcp import context

    context.reset_context()  # a singleton built by an earlier test carries its config with it
    yield
    context.reset_context()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return load_config(
        {
            "RTINGS_CACHE_DIR": str(tmp_path / "cache"),
            "RTINGS_CONFIG_DIR": str(tmp_path / "config"),
        }
    )


@pytest.fixture
def cache(config: Config) -> Cache:
    return Cache(config)


@pytest.fixture
def tv_schema():
    """A small hand-built silo schema exercising the shapes that matter.

    Deliberately includes: a public test (``insider_only:false``) with a score, a gated
    numeric test with a unit, a ``word`` test whose values look numeric, a ``graph`` test, a
    ``group`` parent, and a legacy-only test that is defined but not on the current bench.
    """
    return parse_column_options("tv", load_fixture("column_options_min.json"))
