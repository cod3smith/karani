"""Qualification client + JSON extractor + few-shot injection."""
from __future__ import annotations

import json

import pytest

from qualification.client import _extract_json, qualify_one
from qualification.models import QualificationResult
from qualification.prompts import build_user_prompt


class FakeClient:
    model_name = "fake"
    def __init__(self, script): self.script = script; self.n = 0; self.last_prompt = ""
    async def complete(self, system, user):
        self.last_prompt = user
        self.n += 1
        return self.script


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_stripped_think_tag():
    raw = "<think>Reasoning...</think>\n```json\n{\"a\": 2}\n```"
    assert _extract_json(raw) == {"a": 2}


def test_extract_json_prose_prefix():
    assert _extract_json('Here is the JSON: {"a": 3}') == {"a": 3}


@pytest.mark.asyncio
async def test_qualify_one_happy():
    payload = json.dumps({
        "fit_score": 82, "verdict": "qualified",
        "strengths": [], "gaps": [], "red_flags": [],
        "why_apply": "Good fit.", "why_skip": "",
        "recommended_positioning": "Lead with Python."
    })
    client = FakeClient(payload)
    r = await qualify_one(client, resume="R", resume_hash="h1", hints=[],
                          job_row={"id": 1, "company_display": "GL",
                                    "title": "SWE",
                                    "description_text": "python"})
    assert r.fit_score == 82
    assert r.verdict == "qualified"
    assert r.model == "fake"
    assert r.resume_hash == "h1"


@pytest.mark.asyncio
async def test_qualify_one_malformed_downgrades_to_maybe():
    r = await qualify_one(FakeClient("nonsense"), resume="R", resume_hash="h1",
                          hints=[], job_row={"id": 1, "title": "SWE",
                                              "description_text": "python"})
    assert r.verdict == "maybe"
    assert r.fit_score == 0


def test_build_prompt_with_past_verdicts():
    p = build_user_prompt(
        resume="R", hints=[],
        job_row={"title": "SWE", "description_text": "x"},
        past_verdicts=[
            {"company_display": "GitLab", "title": "SWE",
             "fit_score": 85, "verdict": "qualified", "user_verdict": "applied"},
            {"company_display": "X", "title": "SDR",
             "fit_score": 20, "verdict": "skip", "user_verdict": "skip"},
        ],
    )
    assert "<past_verdicts>" in p
    assert "user chose: applied" in p
    assert "user chose: skip" in p


def test_build_prompt_without_past_verdicts_omits_block():
    p = build_user_prompt(
        resume="R", hints=[],
        job_row={"title": "SWE", "description_text": "x"},
    )
    assert "<past_verdicts>" not in p
