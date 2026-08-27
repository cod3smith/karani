"""karani.config: schema defaults, loader precedence, application onto
the deterministic tier, per-task LLM routing, and the wizard."""
from __future__ import annotations

import pytest

from karani.config import KaraniConfig, reload_config
from karani.config.wizard import run_wizard


@pytest.fixture(autouse=True)
def _restore_defaults():
    yield
    reload_config()  # KARANI_CONFIG=/nonexistent in conftest -> defaults


def _write(tmp_path, body: str):
    f = tmp_path / "karani.toml"
    f.write_text(body)
    return f


# --- schema ---

def test_defaults_validate_and_match_preconfig_behavior():
    cfg = KaraniConfig()
    assert cfg.version == 1
    assert "software_engineering" in cfg.profile.roles
    assert cfg.profile.min_skill_overlap == 1
    assert cfg.shapes == ()
    assert cfg.targets == ()          # () = built-in curated list
    assert "Nairobi" in cfg.positioning.based_in
    assert cfg.llm.for_task("qualify").provider is None


def test_newer_version_rejected():
    with pytest.raises(Exception, match="upgrade karani"):
        KaraniConfig(version=99)


def test_example_toml_is_valid():
    import tomllib
    from importlib import resources
    raw = tomllib.loads(
        (resources.files("karani") / "resources" / "karani.example.toml")
        .read_text())
    raw.setdefault("shapes", raw.pop("shape", []))
    raw.setdefault("targets", raw.pop("target", []))
    cfg = KaraniConfig.model_validate(raw)
    assert cfg.shapes and cfg.shapes[0].name == "global-remote"


# --- loader application ---

def test_profile_and_targets_applied(tmp_path):
    cfg_file = _write(tmp_path, """
version = 1
[profile]
roles = ["ml_ai"]
seniority = ["staff"]
must_have_any = ["rust"]
exclude_titles = ["blockchain wizard"]

[[target]]
source = "lever"
slug = "acme"
""")
    reload_config(cfg_file)
    from karani.ingestion import orchestrator, profile
    from karani.ingestion.config import settings
    from karani.ingestion.models import RoleCategory, Seniority

    assert profile.DEFAULT_PROFILE.target_categories == (RoleCategory.ML_AI,)
    assert profile.DEFAULT_PROFILE.seniority_bands == (Seniority.STAFF,)
    assert profile.DEFAULT_PROFILE.must_have_any == ("rust",)
    assert "blockchain wizard" in settings.excluded_title_terms
    assert len(orchestrator.TARGETS) == 1
    assert orchestrator.TARGETS[0].slug == "acme"


def test_env_beats_file_for_comp_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("MIN_COMP_USD", "999000")
    cfg_file = _write(tmp_path, """
version = 1
[hunt]
min_comp_usd = 100000
""")
    from karani.ingestion.config import settings
    before = settings.min_comp_usd
    settings.min_comp_usd = 999000  # what env-derived settings hold
    reload_config(cfg_file)
    assert settings.min_comp_usd == 999000  # file did NOT override env
    settings.min_comp_usd = before


def test_positioning_reaches_prompts(tmp_path):
    cfg_file = _write(tmp_path, """
version = 1
[positioning]
based_in = "Lisbon, Portugal"
candidate = "a staff platform engineer"
narrative = "hunting boring infrastructure jobs"
excluded_domains = "crypto roles"
""")
    reload_config(cfg_file)
    from karani.drafting.prompts import system_prompt as draft_sp
    from karani.qualification.prompts import system_prompt

    rendered = system_prompt()
    assert "Lisbon, Portugal" in rendered
    assert "boring infrastructure jobs" in rendered
    assert "crypto roles" in rendered
    assert "Nairobi" not in rendered
    assert "Lisbon, Portugal" in draft_sp()


def test_llm_task_routing(tmp_path):
    cfg_file = _write(tmp_path, """
version = 1
[llm.default]
provider = "openrouter"

[llm.humanize]
provider = "local"
model = "llama3.2:3b"
""")
    reload_config(cfg_file)
    from karani.qualification.factory import get_qualifier
    from karani.qualification.local import LocalQualifier
    from karani.qualification.openrouter import OpenRouterQualifier

    assert isinstance(get_qualifier(task="humanize"), LocalQualifier)
    assert get_qualifier(task="humanize").model_name == "llama3.2:3b"
    assert isinstance(get_qualifier(task="qualify"), OpenRouterQualifier)
    # Explicit argument beats config.
    assert isinstance(get_qualifier(provider="local", task="qualify"),
                      LocalQualifier)


def test_autopilot_caps_from_file(tmp_path):
    cfg_file = _write(tmp_path, """
version = 1
[autopilot]
min_fit = 70
max_drafts_per_day = 2
""")
    reload_config(cfg_file)
    from karani.autopilot.runner import _caps
    min_fit, _max, daily = _caps()
    assert min_fit == 70
    assert daily == 2


# --- wizard ---

def test_wizard_output_parses_and_applies(tmp_path):
    dest = tmp_path / "karani.toml"
    run_wizard(dest, yes=True)
    cfg = reload_config(dest)
    assert cfg.shapes[0].comp_floor_usd == 160000
    assert cfg.llm.for_task("humanize").provider == "local"
    assert cfg.autopilot.min_fit == 85


# --- slack allowlist (audit H4) ---

def test_listener_allowlist():
    pytest.importorskip("slack_sdk")
    from karani.slackbridge.listener import _is_command_message
    event = {"type": "message", "text": "draft 1", "channel": "D1",
             "user": "U_STRANGER"}
    assert _is_command_message(event, channel_filter=None) is True
    assert _is_command_message(event, channel_filter=None,
                               allowed_users=("U_KELYN",)) is False
    event["user"] = "U_KELYN"
    assert _is_command_message(event, channel_filter=None,
                               allowed_users=("U_KELYN",)) is True


def test_repo_example_matches_packaged_example():
    """Root karani.example.toml (for repo browsers) must stay identical to
    the packaged copy (for pip installs) — single source, no drift."""
    from importlib import resources
    from pathlib import Path
    packaged = (resources.files("karani") / "resources"
                / "karani.example.toml").read_text()
    assert Path("karani.example.toml").read_text() == packaged
