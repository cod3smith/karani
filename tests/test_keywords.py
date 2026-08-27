"""ATS keyword extraction, coverage math, and the draft-runner integration."""
from __future__ import annotations

import json

import pytest

from karani.drafting.keywords import coverage, extract_keywords
from karani.drafting.runner import draft_for_job


def test_extract_keywords_word_boundary():
    jd = ("We need Python and Go engineers with Kafka and dbt experience "
          "building data pipelines on Kubernetes.")
    kws = extract_keywords(jd)
    assert "python" in kws and "go" in kws and "kafka" in kws
    assert "dbt" in kws and "kubernetes" in kws and "data pipelines" in kws
    # "go" must not match inside other words ("Google", "algorithms").
    assert extract_keywords("Google algorithms cargo") == []


def test_coverage_math():
    kws = ["python", "kafka", "terraform"]
    result = coverage(kws, "I run Python services that consume from Kafka.")
    assert result["score"] == round(2 / 3, 3)
    assert result["matched"] == ["python", "kafka"]
    assert result["missing"] == ["terraform"]
    # No recognized JD terms -> full marks, nothing to chase.
    assert coverage([], "anything")["score"] == 1.0


class CapturingLLM:
    model_name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.last_prompt = ""

    async def complete(self, system: str, user: str) -> str:
        self.last_prompt = user
        return self.response


@pytest.mark.asyncio
async def test_draft_gets_keyword_targets_and_scores_coverage(tmp_path):
    jd = "Requires Python, Kafka, and Terraform. Remote."
    draft_json = json.dumps({
        "cover_letter": "I build Python streaming systems on Kafka.",
        "tone_note": "",
        "tailored_bullets": [{"original_role": "DataQRL",
                              "text": "Provisioned infra with Terraform."}],
        "application_answers": [],
        "subject_line": "", "positioning_summary": "",
    })
    client = CapturingLLM(draft_json)
    pkg, path = await draft_for_job(
        client,
        resume="# Kelyn\nPython and Kafka experience.",  # no terraform
        job_row={"id": 1, "company_display": "Acme", "title": "SWE",
                 "description_text": jd,
                 "apply_url": "https://x.example"},
        output_path=tmp_path / "d.md",
    )
    # The resume's gap (terraform) was surfaced to the drafter...
    assert "<keyword_targets>" in client.last_prompt
    assert "terraform" in client.last_prompt
    # ...and the draft's bullet closed it: full coverage.
    assert pkg.keyword_coverage == 1.0
    assert pkg.keyword_missing == []
    assert path.exists()
