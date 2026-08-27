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


import pytest


@pytest.fixture
def sample_job():
    from ingestion.models import Job, RemoteStatus, Source
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
    from ingestion.resume import ResumeProfile
    return ResumeProfile(
        raw_markdown="# Kelyn\n8y Python + ML infra. Led global teams.",
        hints=["EAT timezone"],
    )
