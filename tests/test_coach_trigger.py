"""Prep fires on the state change, not on being remembered.

The trigger hangs off `Storage.set_application_status`, so it works
whatever moved the application — the CLI, the MCP tool, a Slack
button, or the email watcher. Nothing here may reach a model or the
network: the LLM is scripted and Slack is a stub.
"""
from __future__ import annotations

import json

import pytest

from karani.coach import trigger as coach_trigger
from karani.ingestion.storage import Storage

RESUME = """# Kelyn Njeri
kelyn@example.com

## Experience

### DataQRL — Staff Engineer
*2022 - 2025*

- Cut p99 ingest latency from 1.4s to 180ms across 7 pipelines
"""

STORY_JSON = json.dumps({"stories": [
    {"title": "Cutting ingest latency", "situation": "Missing SLA",
     "task": "Own p99", "action": "Rewrote the batcher",
     "result": "1.4s to 180ms", "source_ids": ["p0b0"]}]})

PREP_JSON = json.dumps({
    "company_brief": "GitLab builds a devops platform.",
    "likely_questions": [
        {"question": "Walk me through a latency problem you owned.",
         "why": "gap: Rust", "story_title": "Cutting ingest latency",
         "answer_angle": "Lead with the batcher rewrite."}],
    "questions_to_ask": [{"question": "How did runner-v2 land?",
                          "source_basis": "their blog"}],
    "comp_notes": "Anchor at the top of the band.",
    "red_flags": [], "warm_openers": [],
    "positioning_reminder": "Lead with data-platform scale.",
})

JOB = {"id": 42, "company": "gitlab", "company_display": "GitLab",
       "title": "Staff Backend Engineer",
       "description_text": "Distributed systems, Kafka, latency.",
       "location_raw": "Remote", "remote_status": "remote",
       "comp_min_usd": 200_000, "comp_max_usd": 240_000,
       "role_category": "software_engineering", "seniority": "staff",
       "active": True}


class SequencedLLM:
    """Scripted in call order: story mining, then the pack."""
    model_name = "fake-sonnet"

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class StubSlack:
    def __init__(self):
        self.posts: list[dict] = []

    async def post_message(self, channel, text, blocks=None, thread_ts=None):
        self.posts.append({"channel": channel, "text": text,
                           "blocks": blocks})
        return {"ok": True}


def _storage() -> Storage:
    s = Storage("")
    s._memory[("greenhouse", "1")] = dict(JOB)
    return s


def _fire(storage, status, *, slack=None, **kw):
    return coach_trigger.on_status_change(
        storage, JOB["id"], status, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, PREP_JSON]),
        slack=slack, channel="#jobs", **kw)


@pytest.mark.asyncio
async def test_a_screen_transition_builds_and_delivers_a_pack(
        tmp_path, monkeypatch):
    monkeypatch.setenv("KARANI_AUTO_PREP", "on")
    monkeypatch.chdir(tmp_path)
    storage = _storage()
    slack = StubSlack()

    result = await _fire(storage, "screen", slack=slack)

    assert result is not None
    assert result.pack.stage == "screen"
    assert result.path.exists()
    assert slack.posts and "GitLab" in slack.posts[0]["text"]


@pytest.mark.asyncio
async def test_statuses_that_are_not_interviews_do_nothing(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("KARANI_AUTO_PREP", "on")
    monkeypatch.chdir(tmp_path)
    for status in ("applied", "ready", "rejected", "offer"):
        assert await _fire(_storage(), status) is None


@pytest.mark.asyncio
async def test_the_same_stage_never_preps_twice(tmp_path, monkeypatch):
    """Statuses get re-set — a Slack button, then the watcher reading
    the same email. The second pass must not spend a second pack."""
    monkeypatch.setenv("KARANI_AUTO_PREP", "on")
    monkeypatch.chdir(tmp_path)
    storage = _storage()

    assert await _fire(storage, "screen") is not None
    assert await _fire(storage, "screen") is None
    # A different stage is a different interview, so it does prep.
    assert await _fire(storage, "interview") is not None


@pytest.mark.asyncio
async def test_the_kill_switch_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("KARANI_AUTO_PREP", "off")
    monkeypatch.chdir(tmp_path)
    assert await _fire(_storage(), "screen") is None


@pytest.mark.asyncio
async def test_a_scheduled_date_in_the_stage_log_reaches_the_card(
        tmp_path, monkeypatch):
    monkeypatch.setenv("KARANI_AUTO_PREP", "on")
    monkeypatch.chdir(tmp_path)
    storage = _storage()
    await storage.add_stage(JOB["id"], "screen",
                            notes="scheduled 2026-11-12 at 15:00 EAT")
    slack = StubSlack()

    result = await _fire(storage, "screen", slack=slack)

    assert result.pack.scheduled_at is not None
    assert result.pack.scheduled_at.date().isoformat() == "2026-11-12"
    rendered = json.dumps(slack.posts[0]["blocks"])
    assert "2026-11-12" in rendered


@pytest.mark.asyncio
async def test_a_failing_prep_never_breaks_the_status_write(tmp_path,
                                                            monkeypatch):
    """The state machine is the system of record. A coach that throws
    must not take a status transition down with it."""
    monkeypatch.setenv("KARANI_AUTO_PREP", "on")
    monkeypatch.chdir(tmp_path)

    async def boom(*a, **kw):
        raise RuntimeError("the model is on fire")

    monkeypatch.setattr("karani.coach.pipeline.build_prep_pack", boom)
    storage = _storage()
    await storage.set_application_status(JOB["id"], "screen")
    assert storage._memory[("greenhouse", "1")]["application_status"] \
        == "screen"


@pytest.mark.asyncio
async def test_storage_dispatches_the_transition(tmp_path, monkeypatch):
    """The integration that matters: the hook is in storage, so the
    email watcher gets prep for free without importing the coach."""
    monkeypatch.chdir(tmp_path)
    seen: list[tuple[int, str]] = []

    async def spy(storage, job_id, status, **kw):
        seen.append((job_id, status))

    monkeypatch.setattr("karani.coach.notify_status_change", spy)
    storage = _storage()
    await storage.set_application_status(JOB["id"], "interview")
    assert seen == [(JOB["id"], "interview")]
