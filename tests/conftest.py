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
