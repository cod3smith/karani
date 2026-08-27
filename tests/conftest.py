"""Pytest fixtures shared across the suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the project root is importable even when pytest is invoked from /tests.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Neutralize env vars that would trip the OpenRouter constructor during tests.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

# HARD ISOLATION: strip every external-service credential before any karani
# module loads. The developer's .env holds REAL Slack/Notion/DB credentials,
# and best-effort integrations (maybe_sync_job, mem0) read them from the
# environment at call time — with them present, "deterministic" tests write
# junk to the developer's real Notion board / vector store. Tests that need
# these vars set fakes via monkeypatch.setenv (auto-reverted per test).
# NOTE: ingestion.config runs load_dotenv() at import, which RE-POPULATES
# anything stripped here — the autouse fixture below is the enforcement
# that actually holds per test. This module-scope pass only covers the
# window before first import.
_STRIPPED_VARS = (
    "DATABASE_URL",
    "NOTION_TOKEN", "NOTION_DATABASE_ID",
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL",
    "LOCAL_LLM_BASE_URL", "MEM0_LLM_MODEL",
    "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ENDPOINT",
    # Knobs from the developer's .env that would shadow config-precedence
    # tests (env deliberately outranks karani.toml).
    "QUAL_PROVIDER", "QUAL_MODEL", "QUAL_REASONING_EFFORT",
    "QUAL_MAX_TOKENS", "AUTOPILOT_MIN_FIT", "AUTOPILOT_MAX_DRAFTS",
    "AUTOPILOT_MAX_DRAFTS_PER_DAY", "MIN_COMP_USD", "TARGET_COMP_USD",
    "KARANI_HOME",
)
for _var in _STRIPPED_VARS:
    os.environ.pop(_var, None)
os.environ["KARANI_MEMORY"] = "basic"
# mem0 defaults point at localhost infra that may genuinely be running on
# a dev machine — force unreachable endpoints so mem0-mode tests exercise
# the degradation path instead of touching a real vector store.
os.environ["MEM0_PG_URL"] = "postgresql://x:x@127.0.0.1:1/x"
os.environ["MEM0_OLLAMA_URL"] = "http://127.0.0.1:1"
# Never read the developer's real karani.toml — tests run on defaults.
os.environ["KARANI_CONFIG"] = "/nonexistent/karani.toml"
import karani.config as _kc  # noqa: E402
_kc.reload_config()


import pytest  # noqa: E402  (env isolation must precede imports)


@pytest.fixture(autouse=True)
def _no_external_creds(monkeypatch):
    """The enforcement that holds: load_dotenv() at import re-populated
    the developer's .env values, so every test re-strips the full list.
    Tests that need one of these set fakes via monkeypatch.setenv."""
    for var in _STRIPPED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KARANI_MEMORY", "basic")


@pytest.fixture
def sample_job():
    from karani.ingestion.models import Job, RemoteStatus, Source
    return Job(
        source=Source.GREENHOUSE, source_id="1",
        company="gitlab", company_display="GitLab",
        title="Senior Backend Engineer",
        location_raw="Remote",
        remote_status=RemoteStatus.REMOTE,
        description_text=(
            "We hire globally. Python and Go required. "
            "Salary: $180,000 - $220,000. Same pay regardless of location. "
            "Annual team retreat."
        ),
        apply_url="https://example.com/apply/1",
    ).finalize()


@pytest.fixture
def sample_resume():
    from karani.ingestion.resume import ResumeProfile
    return ResumeProfile(
        raw_markdown="# Kelyn\n8y Python + ML infra. Led global teams.",
        hints=["EAT timezone"],
    )
