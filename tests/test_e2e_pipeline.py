"""End-to-end pipeline integration test.

Canned Greenhouse payload -> real fetcher -> pre-filter -> in-memory storage
-> fake-LLM qualification -> digest -> drafting -> state machine -> feedback
loop. No network, no clock, no Postgres — the HTTP layer is an httpx
MockTransport and both LLM calls are scripted fakes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from ingestion import orchestrator
from ingestion.digest import render as render_digest
from ingestion.models import Source
from ingestion.resume import ResumeProfile
from ingestion.storage import Storage
from ingestion.targets import Target
from qualification.runner import qualify_pending


GOOD_JOB = {
    "id": 101,
    "title": "Senior Backend Engineer",
    "company_name": "GitLab",
    "absolute_url": "https://boards.greenhouse.io/gitlab/jobs/101",
    "updated_at": "2026-08-20T09:00:00Z",
    "location": {"name": "Remote"},
    "departments": [{"name": "Engineering"}],
    "offices": [{"name": "Remote"}],
    "content": (
        "<p>We hire globally. Fully remote. Python and Go required. "
        "Salary: $180,000 - $220,000. Same pay regardless of location. "
        "Annual team retreat.</p>"
    ),
}

BAD_JOB = {
    "id": 102,
    "title": "Senior Backend Engineer, US",
    "company_name": "GitLab",
    "absolute_url": "https://boards.greenhouse.io/gitlab/jobs/102",
    "updated_at": "2026-08-20T09:00:00Z",
    "location": {"name": "United States"},
    "departments": [{"name": "Engineering"}],
    "offices": [{"name": "US"}],
    "content": (
        "<p>Python required. Must reside in the United States. "
        "Salary: $180,000 - $220,000.</p>"
    ),
}

GH_PAYLOAD = {"jobs": [GOOD_JOB, BAD_JOB]}

QUAL_RESPONSE = json.dumps({
    "fit_score": 85,
    "verdict": "qualified",
    "strengths": [{"claim": "Python depth",
                   "evidence_from_resume": "8y Python + ML infra"}],
    "gaps": [],
    "red_flags": [],
    "why_apply": "Pay parity, global remote, strong stack overlap.",
    "why_skip": "",
    "recommended_positioning": "Lead with data-platform scale.",
})

DRAFT_RESPONSE = json.dumps({
    "cover_letter": "Dear GitLab team, " + "word " * 250,
    "tone_note": "direct",
    "tailored_bullets": [{"original_role": "DataQRL", "text": "Led ML infra."}],
    "application_answers": [{"question": "Why GitLab?",
                             "answer": "Handbook-first culture.",
                             "word_count": 3}],
    "subject_line": "Senior Backend Engineer application",
    "positioning_summary": "Data-platform engineer with global-team experience.",
})


class ScriptedLLM:
    """QualifierClient fake: returns a fixed payload, records prompts."""
    model_name = "fake-model"

    def __init__(self, script: str):
        self.script = script
        self.calls = 0
        self.last_prompt = ""

    async def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_prompt = user
        return self.script


@pytest.fixture
def patched_orchestrator(monkeypatch):
    """Point the orchestrator at a MockTransport and one Greenhouse target."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=GH_PAYLOAD)
    )
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(
        orchestrator, "httpx",
        SimpleNamespace(AsyncClient=client_factory,
                        HTTPStatusError=httpx.HTTPStatusError),
    )
    monkeypatch.setattr(orchestrator, "TARGETS", [
        Target(Source.GREENHOUSE, "gitlab", "GitLab", True, True),
    ])
    monkeypatch.setattr(orchestrator, "FEED_SOURCES", [])


@pytest.fixture
def resume():
    return ResumeProfile(
        raw_markdown="# Kelyn\n8y Python + ML infra. Led global teams.",
        hints=["EAT timezone"],
    )


@pytest.mark.asyncio
async def test_full_pipeline(patched_orchestrator, resume, tmp_path):
    storage = Storage("")  # in-memory fallback
    await storage.connect()

    # --- ingest: fetch -> pre-filter -> store ---
    stats = await orchestrator.run(storage)
    assert stats.fetched == 2
    assert stats.inserted == 2
    assert stats.passed_prefilter == 1  # BAD_JOB vetoed on region restriction
    assert not stats.errors

    # --- qualify: fake LLM, idempotent per resume hash ---
    qualifier = ScriptedLLM(QUAL_RESPONSE)
    qstats = await qualify_pending(storage, qualifier, resume, limit=10)
    assert qstats.fetched == 1
    assert qstats.qualified == 1
    assert not qstats.errors

    rerun = await qualify_pending(storage, qualifier, resume, limit=10)
    assert rerun.fetched == 0  # already qualified against this resume hash

    # --- digest: all three formats render the qualified role ---
    rows = await storage.top_qualified(limit=10)
    assert len(rows) == 1
    job_id = rows[0]["id"]
    assert rows[0]["fit_score"] == 85
    for fmt in ("text", "md", "html"):
        out = render_digest(rows, fmt)
        assert "GitLab" in out
        assert "Senior Backend Engineer" in out

    # --- draft: fake LLM -> markdown file on disk ---
    from drafting import draft_for_job

    drafter = ScriptedLLM(DRAFT_RESPONSE)
    job_row = await storage.get_job(job_id)
    out_path = tmp_path / "draft.md"
    pkg, path = await draft_for_job(
        drafter, resume=resume.raw_markdown, job_row=job_row,
        qualification=job_row.get("qualification"), output_path=out_path,
    )
    assert path.exists()
    content = path.read_text()
    assert "Dear GitLab team" in content
    assert pkg.verdict_at_draft == "qualified"
    await storage.set_application_status(job_id, "drafting",
                                         draft_path=str(path))

    # --- feedback: verdict removes the row from the digest surface ---
    await storage.set_user_verdict(job_id, "apply")
    assert await storage.top_qualified(limit=10) == []

    # --- feedback loop: past verdict reaches the next qualify prompt ---
    new_resume = ResumeProfile(raw_markdown="# Kelyn v2\nStaff data platform.")
    requalifier = ScriptedLLM(QUAL_RESPONSE)
    rq = await qualify_pending(storage, requalifier, new_resume, limit=10)
    assert rq.fetched == 1  # resume changed -> re-qualify
    assert "<past_verdicts>" in requalifier.last_prompt
    assert "GitLab" in requalifier.last_prompt

    # --- state machine: applied -> stage -> outcome ---
    await storage.set_application_status(job_id, "applied")
    await storage.add_stage(job_id, "recruiter_screen", "intro call booked")
    await storage.set_outcome(job_id, "offer")
    row = await storage.get_job(job_id)
    assert row["application_status"] == "applied"
    assert row["stages"][0]["stage"] == "recruiter_screen"
    assert row["outcome"] == "offer"
    funnel = await storage.pipeline_summary()
    assert funnel == {"applied": 1}


@pytest.mark.asyncio
async def test_ingest_dedups_same_role_across_boards(
    patched_orchestrator, monkeypatch,
):
    """Two targets surfacing the same posting collapse to one row per job
    via canonical_hash (company + title + posted-week)."""
    monkeypatch.setattr(orchestrator, "TARGETS", [
        Target(Source.GREENHOUSE, "gitlab", "GitLab", True, True),
        Target(Source.GREENHOUSE, "gitlab-eu", "GitLab", True, True),
    ])
    storage = Storage("")
    await storage.connect()
    stats = await orchestrator.run(storage)
    assert stats.fetched == 4       # both boards returned both jobs
    assert stats.inserted == 2      # canonical dedup suppressed the copies
    assert stats.updated == 0


@pytest.mark.asyncio
async def test_ingest_survives_a_failing_source(
    patched_orchestrator, monkeypatch,
):
    """A 404ing board must not sink the run — other sources still land."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "deadco" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, json=GH_PAYLOAD)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        orchestrator, "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kw: real_async_client(transport=transport, **kw),
            HTTPStatusError=httpx.HTTPStatusError,
        ),
    )
    monkeypatch.setattr(orchestrator, "TARGETS", [
        Target(Source.GREENHOUSE, "gitlab", "GitLab", True, True),
        Target(Source.GREENHOUSE, "deadco", "DeadCo", False, False),
    ])
    storage = Storage("")
    await storage.connect()
    stats = await orchestrator.run(storage)
    assert stats.inserted == 2
    assert stats.per_source["greenhouse/deadco"].errors == 1
    assert stats.per_source["greenhouse/gitlab"].fetched == 2
