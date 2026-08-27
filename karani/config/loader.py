"""Config discovery, parsing, and application.

Search order for karani.toml: $KARANI_CONFIG (explicit path) >
./karani.toml (repo mode) > ~/.karani/karani.toml (installed mode).
Missing file = pure defaults, which reproduce pre-config behavior.

`load_config()` also APPLIES profile/targets/gates onto the legacy
ingestion settings/targets modules — the bridge that lets the whole
deterministic tier consume config without rewriting its call graph.
Secrets never live here: providers/endpoints in the file, keys in env.
"""
from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

from .schema import KaraniConfig

log = logging.getLogger(__name__)

_config: KaraniConfig | None = None
_source_path: Path | None = None


def karani_home() -> Path:
    return Path(os.getenv("KARANI_HOME", Path.home() / ".karani"))


def find_config_file() -> Path | None:
    explicit = os.getenv("KARANI_CONFIG")
    if explicit:
        return Path(explicit)
    for candidate in (Path("karani.toml"), karani_home() / "karani.toml"):
        if candidate.exists():
            return candidate
    return None


def load_config(path: Path | None = None) -> KaraniConfig:
    """Parse + validate + apply. Idempotent per process (cached)."""
    global _config, _source_path
    if _config is not None and path is None:
        return _config
    file = path or find_config_file()
    if file and file.exists():
        with open(file, "rb") as f:
            raw = tomllib.load(f)
        # [[shape]] / [[target]] read naturally as lists of tables.
        raw.setdefault("shapes", raw.pop("shape", []))
        raw.setdefault("targets", raw.pop("target", []))
        cfg = KaraniConfig.model_validate(raw)
        _source_path = file
    else:
        cfg = KaraniConfig()
        _source_path = None
    _apply(cfg)
    _config = cfg
    return cfg


def get_config() -> KaraniConfig:
    return load_config()


def reload_config(path: Path | None = None) -> KaraniConfig:
    global _config
    _config = None
    return load_config(path)


def config_sources() -> dict:
    """Where the effective config came from — `karani config check`."""
    return {"file": str(_source_path) if _source_path else None,
            "home": str(karani_home())}


def _apply(cfg: KaraniConfig) -> None:
    """Bridge config onto the deterministic tier's existing modules.

    Env still outranks the file for scalar knobs (12-factor), which the
    `or`-chains below implement: env-derived settings values were already
    read; the file only fills what env didn't set explicitly.
    """
    from karani.ingestion import orchestrator, profile, targets
    from karani.ingestion.config import settings
    from karani.ingestion.models import RoleCategory, Seniority

    # --- profile ---
    built = profile.UserProfile(
        seniority_bands=tuple(Seniority(s) for s in cfg.profile.seniority),
        target_categories=tuple(RoleCategory(r) for r in cfg.profile.roles),
        must_have_any=tuple(cfg.profile.must_have_any),
        nice_to_have=(tuple(cfg.profile.nice_to_have)
                      or profile.UserProfile().nice_to_have),
        min_skill_overlap=cfg.profile.min_skill_overlap,
    )
    profile.DEFAULT_PROFILE = built

    # --- gates ---
    if cfg.profile.exclude_titles:
        extra = tuple(t for t in cfg.profile.exclude_titles
                      if t not in settings.excluded_title_terms)
        settings.excluded_title_terms = settings.excluded_title_terms + extra
    floors = [s.comp_floor_usd for s in cfg.shapes if s.comp_floor_usd]
    if cfg.hunt.min_comp_usd is not None and "MIN_COMP_USD" not in os.environ:
        settings.min_comp_usd = cfg.hunt.min_comp_usd
    elif floors and "MIN_COMP_USD" not in os.environ:
        settings.min_comp_usd = min(floors)
    if (cfg.hunt.target_comp_usd is not None
            and "TARGET_COMP_USD" not in os.environ):
        settings.target_comp_usd = cfg.hunt.target_comp_usd

    # --- targets ---
    if cfg.targets:
        from karani.ingestion.models import Source
        built_targets = [
            targets.Target(Source(t.source), t.slug,
                           t.display_name or t.slug, notes=t.notes)
            for t in cfg.targets
        ]
        targets.TARGETS[:] = built_targets
        orchestrator.TARGETS = targets.TARGETS

    log.debug("config applied (file=%s)", _source_path)


def resolve(env_name: str, file_value, default):
    """Standard precedence for one knob: env > file > default."""
    env = os.getenv(env_name)
    if env not in (None, ""):
        return env
    if file_value is not None:
        return file_value
    return default
