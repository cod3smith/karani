"""Follow-up drafting (fake LLMs, no network).

Prep moved to karani/coach/ when it stopped being one call;
its tests live in tests/test_coach_*.py.
"""
from __future__ import annotations

import json

import pytest

from karani.drafting.followup import FOLLOWUP_PROMPT_VERSION, followup_for_job

FOLLOWUP_JSON = json.dumps({
    "note": "Saw the runner-v2 release land last week. My application for "
            "the Senior Backend role leaned on exactly that kind of "
            "pipeline work. Happy to talk whenever suits.",
    "subject_line": "Senior Backend Engineer application",
    "hook_used": "runner-v2 release",
})


class CapturingLLM:
    model_name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.last_prompt = ""

    async def complete(self, system: str, user: str) -> str:
        self.last_prompt = user
        return self.response


JOB = {"id": 9, "company_display": "GitLab",
       "title": "Senior Backend Engineer",
       "description_text": "Python, Kafka, remote.",
       "applied_at": None}


@pytest.mark.asyncio
async def test_followup(tmp_path):
    client = CapturingLLM(FOLLOWUP_JSON)
    pkg, path = await followup_for_job(
        client, job_row=JOB, days_since_applied=9,
        positioning="Data-platform angle",
        dossier="Recent: runner-v2 release",
        output_path=tmp_path / "fu.md",
    )
    assert "runner-v2" in client.last_prompt
    assert "9" in client.last_prompt
    assert pkg.prompt_version == FOLLOWUP_PROMPT_VERSION
    assert pkg.hook_used == "runner-v2 release"
    assert "runner-v2 release" in path.read_text()


@pytest.mark.asyncio
async def test_followup_malformed_downgrades(tmp_path):
    pkg, _ = await followup_for_job(
        CapturingLLM("garbage"), job_row=JOB, days_since_applied=7,
        output_path=tmp_path / "fu.md",
    )
    assert "FOLLOW-UP FAILED" in pkg.note
