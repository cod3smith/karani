"""Memory layer: ledger, deterministic recall, manager modes, and the
qualification integration (recalled memories reach the prompt)."""
from __future__ import annotations

import json

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage
from karani.memory import MemoryManager


@pytest.fixture
async def storage():
    s = Storage("")
    await s.connect()
    return s


# --- ledger ---

@pytest.mark.asyncio
async def test_add_memory_and_dedup(storage):
    r1 = await storage.add_memory("Kelyn skips crypto companies",
                                  "preference")
    assert r1["deduped"] is False
    r2 = await storage.add_memory("Kelyn skips crypto companies",
                                  "preference")
    assert r2["deduped"] is True
    assert r2["id"] == r1["id"]


@pytest.mark.asyncio
async def test_add_memory_validates(storage):
    with pytest.raises(ValueError, match="kind must be one of"):
        await storage.add_memory("x", "vibes")
    with pytest.raises(ValueError, match="non-empty"):
        await storage.add_memory("   ", "preference")


@pytest.mark.asyncio
async def test_deactivate_memory(storage):
    r = await storage.add_memory("Old fact", "company", company="Acme")
    assert await storage.deactivate_memory(r["id"]) is True
    assert await storage.deactivate_memory(r["id"]) is False  # already gone
    assert await storage.recall_memories("old fact") == []


# --- deterministic recall ---

@pytest.mark.asyncio
async def test_recall_scoping_and_ranking(storage):
    await storage.add_memory(
        "GitLab responded within five days last time", "company",
        company="GitLab",
    )
    await storage.add_memory(
        "Coinbase ghosted after the recruiter screen", "company",
        company="Coinbase",
    )
    await storage.add_memory(
        "Prefers data-platform roles over pure ML", "preference",
    )

    # Company scoping: GitLab query gets GitLab + global, not Coinbase.
    rows = await storage.recall_memories(
        "GitLab Senior Backend Engineer data platform", company="GitLab",
    )
    contents = [r["content"] for r in rows]
    assert any("GitLab responded" in c for c in contents)
    assert any("data-platform roles" in c for c in contents)
    assert not any("Coinbase" in c for c in contents)

    # Kind filter.
    prefs = await storage.recall_memories("data platform roles",
                                          kind="preference")
    assert len(prefs) == 1

    # No token overlap -> no recall.
    assert await storage.recall_memories("quantum blockchain golf") == []


# --- manager ---

@pytest.mark.asyncio
async def test_manager_off_mode(storage):
    m = MemoryManager(storage, mode="off")
    result = await m.remember("anything", "preference")
    assert result["id"] is None
    assert await m.recall("anything") == []


def test_manager_rejects_bad_mode(storage):
    with pytest.raises(ValueError, match="off, basic, or mem0"):
        MemoryManager(storage, mode="turbo")


@pytest.mark.asyncio
async def test_manager_mem0_degrades_to_basic(storage):
    # mem0ai is not installed in the test env: mode must fall back, and the
    # ledger must keep working — an outage degrades recall, never data.
    m = MemoryManager(storage, mode="mem0")
    assert m.mode == "basic"
    await m.remember("Degradation still stores facts", "strategy")
    rows = await m.recall("degradation stores facts")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_remember_verdict_and_outcome_compose_facts(storage):
    m = MemoryManager(storage, mode="basic")
    row = {"id": 7, "company_display": "GitLab",
           "title": "Senior Backend Engineer", "fit_score": 85}
    await m.remember_verdict(row, "apply")
    await m.remember_outcome(row, "offer")
    contents = [r["content"] for r in
                await m.recall("GitLab Senior Backend Engineer", limit=10)]
    assert any("verdict 'apply'" in c and "fit_score=85" in c
               for c in contents)
    assert any("outcome 'offer'" in c for c in contents)


@pytest.mark.asyncio
async def test_remember_question_feeds_company_bank(storage):
    m = MemoryManager(storage, mode="basic")
    row = {"id": 3, "company_display": "PostHog", "title": "Senior BE"}
    await m.remember_question(row, "How do you own an incident end to end?",
                              stage="screen")
    bank = await m.recall("PostHog interview questions", kind="question",
                          company="PostHog")
    assert len(bank) == 1
    assert "screen" in bank[0]["content"]
    assert "own an incident" in bank[0]["content"]


# --- qualification integration ---

class PromptCapturingLLM:
    model_name = "fake"

    def __init__(self):
        self.last_prompt = ""

    async def complete(self, system: str, user: str) -> str:
        self.last_prompt = user
        return json.dumps({
            "fit_score": 80, "verdict": "qualified",
            "strengths": [], "gaps": [], "red_flags": [],
            "why_apply": "ok", "why_skip": "",
            "recommended_positioning": "x",
        })


@pytest.mark.asyncio
async def test_qualify_injects_recalled_memories(storage):
    from karani.qualification.runner import qualify_pending

    job = Job(
        source=Source.GREENHOUSE, source_id="1",
        company="gitlab", company_display="GitLab",
        title="Senior Backend Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=(
            "We hire globally. Python required. "
            "Salary: $180,000 - $220,000. Same pay regardless of location."
        ),
        apply_url="https://example.com/1",
    ).finalize()
    await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))

    memory = MemoryManager(storage, mode="basic")
    await memory.remember(
        "GitLab's hiring loop moved fast for Kelyn before", "company",
        company="GitLab",
    )

    client = PromptCapturingLLM()
    resume = ResumeProfile(raw_markdown="# Kelyn\nPython + ML infra.")
    stats = await qualify_pending(storage, client, resume, limit=5,
                                  memory=memory)
    assert stats.qualified == 1
    assert "<memories>" in client.last_prompt
    assert "GitLab's hiring loop moved fast" in client.last_prompt
