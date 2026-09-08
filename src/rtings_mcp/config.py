"""Configuration surface (SPEC §9).

Every knob is an environment variable with a documented default. Nothing here reads a
credential from anywhere but the two sanctioned places (SPEC §6): the stored session file
and ``RTINGS_SESSION_COOKIE``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

API_HOST = "www.rtings.com"
CDN_HOST = "i.rtings.com"
BASE_URL = f"https://{API_HOST}"
API_BASE = f"{BASE_URL}/api/v2/safe"
CDN_BASE = f"https://{CDN_HOST}"

SESSION_COOKIE_NAME = "_rtings_session"

#: Member mode is OFF until a bought membership confirms that a cookie actually flips
#: ``unblurred`` on the API — the one blocking unknown (Phase 0). Everything member mode
#: needs is built and exercised: the ``cache_tier`` filename segment, the demand/write
#: tier rules, write-time demotion and the preview budget all ship from day one carrying
#: ``anonymous``, so enabling it adds values to an existing axis rather than migrating
#: anything. With it off, every tier-keyed write is ``anonymous`` and no row selection
#: keys on a tier — so the server cannot claim member support it has not measured.
MEMBER_MODE_ENV = "RTINGS_MEMBER_MODE"

#: Cache format version. Bumping it invalidates the whole tree rather than migrating.
#:
#: 2 (2026-09-04): the schema parse now carries `derived_category_id`, recovered from list
#: position, so a v1 cached schema would restore a flat scramble of groups with no categories.
#: Bumped whenever a cached payload's SHAPE changes, not just the cache's layout. The
#: derived surfaces (`recs/<silo>/_lists.json`, `recs/<silo>/<list>`, `articles/`) store
#: what the extractor produced, not what RTINGS sent, so a parser that learns a new field
#: is invisible to an existing file: measured 2026-09-08, a cache written on 2026-09-04
#: still served a best-of index with no `kind` and no brand pages (TTL_RECS is 7 days,
#: TTL_REVIEWS 30), which is a released fix that reaches nobody until it expires. 3:
#: `kind`/brand lists and `template` on the best-of surfaces.
CACHE_FORMAT_VERSION = 3

#: TTL per surface, in seconds (SPEC §8). A uniform TTL is a correctness bug, not a
#: freshness preference: a new bench would go unnoticed for a month while the server ranks
#: on a stale ``is_recent`` set.
TTL_SILOS = 86_400  # 1 day
TTL_BENCH = 86_400  # 1 day
TTL_CATALOG = 3 * 86_400  # 3 days — also the coverage generation key
TTL_SCHEMA = 30 * 86_400
TTL_RESULTS_CURRENT = 7 * 86_400  # mitigation for the open coverage_stale hole
TTL_RESULTS_LEGACY = 30 * 86_400  # a closed bench gains no products
TTL_REVIEWS = 30 * 86_400
TTL_GRAPH_POSITIVE = 180 * 86_400  # content-addressed CDN paths
TTL_GRAPH_NEGATIVE = 3 * 86_400  # a negative is NOT content-addressed
TTL_RECS = 7 * 86_400
TTL_PROBE = 900  # 15 min — credential health, never long-lived


def _get(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_float(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


#: Values a user may assert for their own session when the probe gets it wrong.
SESSION_OVERRIDES = frozenset({"member", "free", "anonymous"})


def _session_override(env: Mapping[str, str], warnings: list[str]) -> str | None:
    """``RTINGS_SESSION_OVERRIDE`` — a user ASSERTION, never an inference.

    Member-vs-free is ``current_user.is_insider`` (measured 2026-09-06, ``RECON.md`` §13.2),
    but a page can ship without it, and a logged-in session with no positive signal is called
    ``free``. A real member classified ``free`` is quietly crippled — served cached anonymous
    nulls for up to 7 days and refused ``rt_product`` without ``consume_preview``. This is the
    escape hatch for that case, and it does not break "never infer auth from data": the user
    is telling us, not the bytes.
    """
    raw = _get(env, "RTINGS_SESSION_OVERRIDE")
    if raw is None:
        return None
    value = raw.lower()
    if value not in SESSION_OVERRIDES:
        warnings.append(
            f"RTINGS_SESSION_OVERRIDE={raw!r} is not one of {sorted(SESSION_OVERRIDES)}; ignored"
        )
        return None
    return value


@dataclass(slots=True)
class Config:
    """Resolved runtime configuration. Built once per process from the environment."""

    cache_dir: Path
    config_dir: Path
    session_cookie_env: str | None
    cache_ttl_days: int
    cache_max_mb: int
    rate_interval_s: float
    rate_burst: int
    cdn_rate_interval_s: float
    cdn_rate_burst: int
    concurrency: int
    enable_graph: bool
    graph_max_points: int
    max_preview_spend: int
    member_mode: bool
    session_override: str | None
    telemetry: bool
    #: Character budget for one rt_ratings response, measured before serialization. MCP
    #: clients cap a tool result (Claude Code drops the whole thing past ~50 K chars), so
    #: the server trims the window and says so rather than let the client discard it.
    max_response_chars: int = 40_000
    warnings: list[str] = field(default_factory=list)

    @property
    def session_file(self) -> Path:
        """The stored credential. Never confusable with ``probe/last_probe.json`` (SPEC §6)."""
        return self.config_dir / "session.json"

    def ttl_results(self, *, is_current_bench: bool) -> int:
        base = TTL_RESULTS_CURRENT if is_current_bench else TTL_RESULTS_LEGACY
        # RTINGS_CACHE_TTL_DAYS tunes the measurement surfaces only; the routing surfaces
        # (silos, bench, catalog, schema) keep their own shorter clocks.
        if self.cache_ttl_days != 30:
            scale = self.cache_ttl_days / 30
            return max(60, int(base * scale))
        return base

    def ttl_reviews(self) -> int:
        return max(60, self.cache_ttl_days * 86_400)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (defaults to the process environment)."""
    src: Mapping[str, str] = os.environ if env is None else env
    warnings: list[str] = []

    cache_dir = Path(
        _get(src, "RTINGS_CACHE_DIR") or (Path.home() / ".cache" / "rtings-mcp")
    ).expanduser()
    config_dir = Path(
        _get(src, "RTINGS_CONFIG_DIR") or (Path.home() / ".config" / "rtings-mcp")
    ).expanduser()

    interval = _env_float(src, "RTINGS_RATE_INTERVAL_S", 2.0)
    burst = _env_int(src, "RTINGS_RATE_BURST", 5, minimum=1)

    # Deprecated alias: pins the interval and forces burst=1 — the old flat-interval
    # behaviour, exactly (SPEC §9).
    if _get(src, "RTINGS_MIN_REQUEST_INTERVAL_S") is not None:
        interval = _env_float(src, "RTINGS_MIN_REQUEST_INTERVAL_S", interval)
        burst = 1
        warnings.append(
            "RTINGS_MIN_REQUEST_INTERVAL_S is deprecated; it pins RTINGS_RATE_INTERVAL_S "
            "and forces RTINGS_RATE_BURST=1"
        )

    return Config(
        cache_dir=cache_dir,
        config_dir=config_dir,
        session_cookie_env=_get(src, "RTINGS_SESSION_COOKIE"),
        cache_ttl_days=_env_int(src, "RTINGS_CACHE_TTL_DAYS", 30, minimum=1),
        cache_max_mb=_env_int(src, "RTINGS_CACHE_MAX_MB", 1024, minimum=1),
        rate_interval_s=interval,
        rate_burst=burst,
        cdn_rate_interval_s=_env_float(src, "RTINGS_CDN_RATE_INTERVAL_S", 0.25),
        cdn_rate_burst=_env_int(src, "RTINGS_CDN_RATE_BURST", 10, minimum=1),
        concurrency=_env_int(src, "RTINGS_CONCURRENCY", 1, minimum=1),
        enable_graph=_env_bool(src, "RTINGS_ENABLE_GRAPH", True),
        graph_max_points=_env_int(src, "RTINGS_GRAPH_MAX_POINTS", 200, minimum=2),
        max_preview_spend=_env_int(src, "RTINGS_MAX_PREVIEW_SPEND", 1, minimum=0),
        member_mode=_env_bool(src, "RTINGS_MEMBER_MODE", True),
        session_override=_session_override(src, warnings),
        telemetry=_env_bool(src, "RTINGS_TELEMETRY", True),
        max_response_chars=_env_int(src, "RTINGS_MAX_RESPONSE_CHARS", 40_000, minimum=4_000),
        warnings=warnings,
    )


#: The silos known when this release shipped. Ships as a ``description``/``examples`` HINT on
#: the ``silo`` parameter, **never** a JSON-Schema ``enum``: an enum is enforced client-side,
#: so a silo RTINGS adds mid-release would be unreachable until a new release ships, and it
#: would create a second allowlist that can disagree with live ``static.silos``. Validation is
#: server-side against the live list only; a silo outside this set is fetched normally with a
#: ``silo_hint_drift`` warning. Lives here rather than in ``server.py`` so the repository can
#: raise that warning without importing the server (a cycle).
KNOWN_SILOS: tuple[str, ...] = (
    "tv", "headphones", "monitor", "soundbar", "mouse", "keyboard", "printer",
    "robot-vacuum", "vacuum", "dehumidifier", "projector", "toaster-oven",
    "keyboard-switch", "air-purifier", "running-shoes", "humidifier", "refrigerator",
    "mattress", "air-conditioner", "microwave", "blender", "air-fryer", "toaster", "vpn",
    "router", "speaker", "camera", "laptop",
)
SILO_HINT_SET = frozenset(KNOWN_SILOS)
SILO_HINT = ", ".join(KNOWN_SILOS)
