"""karani.config — the single user-configuration surface (ADR 0015).

`karani.toml` owns structure (what to hunt, which providers, which
endpoints); the environment owns secrets (API keys, tokens, DSNs).
Precedence: built-in defaults < karani.toml < environment variables.
Defaults reproduce pre-config behavior exactly, so the file is optional.
"""
from __future__ import annotations

from .loader import config_sources, get_config, load_config, reload_config
from .schema import KaraniConfig

__all__ = ["KaraniConfig", "get_config", "load_config", "reload_config",
           "config_sources"]
