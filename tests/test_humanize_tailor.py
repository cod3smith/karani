"""Humanizer (tell detection + rewrite arbitration) and resume tailor."""
from __future__ import annotations

import json

import pytest

from karani.drafting.humanize import humanize_package, voice_report
from karani.drafting.models import ApplicationAnswer, DraftPackage
from karani.drafting.resume_tailor import tailor_resume


# --- deterministic tell detection ---

def test_voice_report_flags_tells():
    text = ("I am excited to apply. I leverage cutting-edge solutions and "
            "have a proven track record. Moreover, I spearheaded delivery — "
            "not only quickly but also robustly.")
    report = voice_report(text)
    assert report["score"] < 60
    assert "i am excited" in report["tells"]
    assert "leverage" in report["tells"]
    assert "proven track record" in report["tells"]
    assert report["not_only_but_also"] == 1


def test_voice_report_clean_text_scores_high():
    text = ("I spent eight years building data platforms. At DataQRL I "
            "designed the governance layer that processes millions of "
            "records daily. Short version: I ship infrastructure.")
    report = voice_report(text)
    assert report["score"] >= 95
    assert report["tells"] == []


# --- rewrite arbitration ---

AI_LETTER = ("I am excited to apply. I leverage cutting-edge tools and am "
             "passionate about your mission. Moreover, my proven track "
             "record speaks for itself.")
HUMAN_LETTER = ("Eight years of Python data platforms, most recently the "
                "governance layer at DataQRL. Your integrations role sits "
                "exactly in that lane.")


class ScriptedLLM:
    model_name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.last_system = ""

    async def complete(self, system, user):
        self.last_system = system
        return self.response


def _pkg(letter: str) -> DraftPackage:
    return DraftPackage(
        cover_letter=letter,
        application_answers=[ApplicationAnswer(
            question="Why us?", answer="I am excited to contribute.")],
    )


@pytest.mark.asyncio
async def test_humanize_keeps_improved_rewrite():
    rewrite = json.dumps({
        "cover_letter": HUMAN_LETTER,
        "application_answers": [
            {"question": "Why us?",
             "answer": "Your data platform maps to my last three years."}],
    })
    client = ScriptedLLM(rewrite)
    pkg, report = await humanize_package(client, _pkg(AI_LETTER),
                                         voice_sample="# Kelyn resume")
    assert report["kept"] == "rewrite"
    assert report["after"]["score"] > report["before"]["score"]
    assert pkg.cover_letter == HUMAN_LETTER
    assert pkg.application_answers[0].answer.startswith("Your data platform")
    assert "Kelyn resume" in client.last_system  # voice sample reached prompt


@pytest.mark.asyncio
async def test_humanize_rejects_worse_rewrite():
    worse = json.dumps({
        "cover_letter": AI_LETTER + " Furthermore, I delve seamlessly.",
        "application_answers": [],
    })
    original = _pkg(HUMAN_LETTER)
    pkg, report = await humanize_package(ScriptedLLM(worse), original)
    assert report["kept"] == "original"
    assert pkg.cover_letter == HUMAN_LETTER


@pytest.mark.asyncio
async def test_humanize_malformed_keeps_original():
    original = _pkg(AI_LETTER)
    pkg, report = await humanize_package(ScriptedLLM("no json"), original)
    assert report["kept"] == "original"
    assert pkg.cover_letter == AI_LETTER


# --- resume tailor ---

RESUME_JSON = json.dumps({
    "resume_markdown": "# Kelyn Njeri\nSenior engineer. Python, Kafka, "
                       "Terraform. Built data platforms at DataQRL.",
    "changes_summary": "- led with platform work",
})


@pytest.mark.asyncio
async def test_tailor_resume_writes_file_and_scores(tmp_path):
    client = ScriptedLLM(RESUME_JSON)
    result, path = await tailor_resume(
        client,
        resume="# Kelyn\nPython and Kafka.",  # master lacks terraform
        job_row={"id": 4, "company_display": "Acme", "title": "SWE",
                 "description_text": "Requires Python, Kafka, Terraform."},
        output_path=tmp_path / "resume.md",
    )
    assert path.exists()
    assert "DataQRL" in path.read_text()
    assert result.keyword_coverage == 1.0  # tailored version covers all 3
    assert result.prompt_version == "resume-v1"


@pytest.mark.asyncio
async def test_tailor_resume_malformed_writes_nothing(tmp_path):
    result, path = await tailor_resume(
        ScriptedLLM("garbage"), resume="# K",
        job_row={"id": 4, "company_display": "Acme", "title": "SWE",
                 "description_text": "Python."},
        output_path=tmp_path / "resume.md",
    )
    assert "TAILORING FAILED" in result.changes_summary
    assert not path.exists()
