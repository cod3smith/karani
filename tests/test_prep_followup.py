"""Prep pack + follow-up drafting (fake LLMs, no network)."""
from __future__ import annotations

import json

import pytest

from karani.drafting.followup import FOLLOWUP_PROMPT_VERSION, followup_for_job
from karani.drafting.prep import PREP_PROMPT_VERSION, prep_for_job

PREP_JSON = json.dumps({
    "company_brief": "GitLab is a devops platform, fully remote.",
    "likely_questions": [
        {"question": "Tell me about running production Rust.",
         "why": "gap: no Rust experience",
         "suggested_answer": "Systems Go at DataQRL..."},
    ],
    "questions_to_ask": [
        {"question": "How did the ClickHouse migration from your March post land?",
         "source_basis": "engineering blog"},
    ],
    "warm_openers": [
        {"contact": "alice", "opener": "Your stream-processing repo overlaps "
                                       "with my Kafka work."},
    ],
    "positioning_reminder": "Lead with data-platform scale.",
})

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
async def test_prep_pack(tmp_path):
    client = CapturingLLM(PREP_JSON)
    pkg, path = await prep_for_job(
        client, resume="# Kelyn", job_row=JOB,
        qualification={"gaps": [{"requirement": "Rust"}]},
        dossier="## Background\nGitLab is a devops company.",
        question_bank=["Asked about incident ownership (2025 screen)"],
        output_path=tmp_path / "prep.md",
    )
    # All the context reached the prompt.
    assert "Rust" in client.last_prompt
    assert "GitLab is a devops company" in client.last_prompt
    assert "incident ownership" in client.last_prompt
    # Structured output landed and rendered.
    assert pkg.prompt_version == PREP_PROMPT_VERSION
    assert len(pkg.likely_questions) == 1
    content = path.read_text()
    assert "ClickHouse migration" in content
    assert "Warm-path openers" in content


@pytest.mark.asyncio
async def test_prep_malformed_downgrades(tmp_path):
    pkg, path = await prep_for_job(
        CapturingLLM("not json"), resume="R", job_row=JOB,
        output_path=tmp_path / "prep.md",
    )
    assert "PREP FAILED" in pkg.company_brief
    assert path.exists()


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
