"""Process-wide wiring: one config, one cache, one transport pair, one repository.

Sessions are built once and reused (SPEC §9): recreating one discards the TLS identity,
the in-memory cookie jar and the limiter state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .auth import AuthManager, load_credential
from .cache import Cache
from .config import Config, load_config
from .http import Transport
from .ratelimit import HostCooldown
from .repository import Repository

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Context:
    config: Config
    cache: Cache
    transport: Transport
    auth: AuthManager
    repo: Repository

    @classmethod
    def build(cls, config: Config | None = None) -> Context:
        cfg = config or load_config()
        cache = Cache(cfg)
        credential = load_credential(cfg)
        transport = Transport(
            cfg,
            credential=credential,
            cooldown=HostCooldown(cfg.cache_dir / "cooldown"),
        )
        auth = AuthManager(config=cfg, cache=cache, transport=transport)
        repo = Repository(cfg, cache, transport, auth)
        return cls(config=cfg, cache=cache, transport=transport, auth=auth, repo=repo)


_CONTEXT: Context | None = None


def get_context() -> Context:
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = Context.build()
    return _CONTEXT


def reset_context() -> None:
    """Test seam. Never called by the server."""
    global _CONTEXT
    _CONTEXT = None
