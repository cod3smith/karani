"""Drafting: fake LLM → DraftPackage → markdown file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting import draft_for_job
from drafting.models import DraftPackage
from drafting.writers import render_markdown, default_path
from qualification.models import QualificationResult


class DraftFakeClient:
    model_name = "fake-draft"
    async def complete(self, system, user):
        return json.dumps({
            "cover_letter": (
                "I built the entire backend for a fintech platform with sub-100ms API "
                "latency and integrated Plaid, Modern Treasury, and Stripe — the same "
                "problem set your JD describes. At DataQRL I architected a 7-module "
                "data-governance platform end-to-end, which maps directly to your "
                "platform-engineer scope. I'd like to walk through the architecture in "
                "a 30-min call."
            ),
            "tone_note": "First-person, plain, no fluff.",
            "tailored_bullets": [
                {"original_role": "Backbone Technologies · Lead SWE",
                 "text": "Built fintech backend with sub-100ms API latency; "
                         "integrated Plaid, Modern Treasury, Stripe.",
                 "why_this_bullet": "Directly matches the JD's payments requirement."},
            ],
            "application_answers": [
                {"question": "Why us?",
                 "answer": ("Your platform-engineer scope maps to what I've done at "
                            "DataQRL and Backbone: end-to-end ownership of data + "
                            "payments systems, with real production numbers."),
                 "word_count": 25},
            ],
            "subject_line": "Backend engineer — Kelyn (Backbone / DataQRL)",
            "positioning_summary": "Positioning: end-to-end backend + data platforms.",
        })


@pytest.mark.asyncio
async def test_draft_generates_and_writes_markdown(tmp_path):
    job = {
        "id": 42, "company_display": "GitLab",
        "title": "Staff Backend Engineer",
        "location_raw": "Remote", "apply_url": "https://x/42",
        "description_text": "Python + Go. Distributed systems.",
    }
    qual = QualificationResult(fit_score=88, verdict="qualified",
                                resume_hash="h")
    pkg, path = await draft_for_job(
        DraftFakeClient(), resume="Kelyn: senior Python + payments.",
        job_row=job, qualification=qual,
        output_path=tmp_path / "test_draft.md",
    )
    assert isinstance(pkg, DraftPackage)
    assert pkg.job_id == 42
    assert pkg.verdict_at_draft == "qualified"
    assert pkg.model == "fake-draft"
    assert path.exists()

    text = path.read_text()
    assert "GitLab" in text
    assert "Staff Backend Engineer" in text
    assert "sub-100ms" in text
    assert "Why us?" in text
    assert "Backbone Technologies · Lead SWE" in text


def test_default_path_generates_slug():
    p = default_path({"id": 7, "company_display": "Hugging Face",
                       "title": "Applied Scientist, LLM"})
    assert "hugging-face" in str(p)
    assert "applied-scientist-llm" in str(p)
    assert str(p).startswith("drafts/")
