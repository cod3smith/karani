"""Autopilot: candidate selection, the draft-and-deliver pass, review
cards, and button-click handling. No network, no slack-sdk."""
from __future__ import annotations

import json

import pytest

from karani.autopilot import run_autopilot
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage
from karani.memory import MemoryManager
from karani.slackbridge.blocks import pack_blocks
from karani.slackbridge.interactions import handle_interaction

DRAFT_JSON = json.dumps({
    "cover_letter": "Dear team, here is my Python and Kafka work.",
    "tone_note": "", "tailored_bullets": [], "application_answers": [],
    "subject_line": "", "positioning_summary": "",
})


class ScriptedLLM:
    model_name = "fake"

    async def complete(self, system, user):
        return DRAFT_JSON


class StubSlack:
    def __init__(self):
        self.posts = []

    async def post_message(self, channel, text, blocks=None, thread_ts=None):
        self.posts.append({"channel": channel, "text": text,
                           "blocks": blocks})
        return {"ok": True}


def _job(source_id: str, title: str | None = None) -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        # Distinct titles by default: same company+title+week collapses to
        # one canonical role (storage._dedupe_canonical), which is correct
        # behavior but would make multi-candidate fixtures ambiguous.
        title=title or f"Senior Backend Engineer {source_id}",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. "
                          "Salary: $180,000 - $220,000. "
                          "Same pay regardless of location."),
        apply_url=f"https://example.com/{source_id}",
    ).finalize()


async def _seed(storage: Storage, source_id: str, title: str | None = None,
                **overrides) -> int:
    job = _job(source_id, title)
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(overrides)
    return result["id"]


@pytest.fixture
async def storage():
    s = Storage("")
    await s.connect()
    return s


# --- candidate selection ---

@pytest.mark.asyncio
async def test_autopilot_candidates_filtering(storage):
    hit = await _seed(storage, "1", verdict="qualified", fit_score=90)
    await _seed(storage, "2", verdict="qualified", fit_score=70)   # low fit
    await _seed(storage, "3", verdict="maybe", fit_score=95)       # not qualified
    await _seed(storage, "4", verdict="qualified", fit_score=92,
                user_verdict="skip")                               # reviewed
    await _seed(storage, "5", verdict="qualified", fit_score=93,
                application_status="drafting")                     # in flight
    rows = await storage.autopilot_candidates(min_fit=85, limit=5)
    assert [r["id"] for r in rows] == [hit]


@pytest.mark.asyncio
async def test_autopilot_candidates_respects_limit_and_order(storage):
    await _seed(storage, "1", verdict="qualified", fit_score=86)
    top = await _seed(storage, "2", verdict="qualified", fit_score=95)
    mid = await _seed(storage, "3", verdict="qualified", fit_score=90)
    rows = await storage.autopilot_candidates(min_fit=85, limit=2)
    assert [r["id"] for r in rows] == [top, mid]


# --- the pass ---

@pytest.mark.asyncio
async def test_run_autopilot_drafts_and_delivers(storage, tmp_path,
                                                 monkeypatch):
    monkeypatch.chdir(tmp_path)  # drafts/ lands here
    job_id = await _seed(storage, "1", verdict="qualified", fit_score=90)
    slack = StubSlack()

    stats = await run_autopilot(
        storage, slack=slack, channel="D1",
        make_qualifier=lambda: ScriptedLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# Kelyn\nPython."),
        min_fit=85, max_drafts=3,
    )
    assert stats.candidates == 1
    assert stats.drafted == 1
    assert stats.delivered == 1
    assert not stats.errors

    row = await storage.get_job(job_id)
    assert row["application_status"] == "drafting"
    assert row["draft_path"]

    blocks_text = json.dumps(slack.posts[0]["blocks"])
    assert "pack_approve" in blocks_text
    assert "Dear team" in blocks_text

    # Second pass: the job left the candidate pool — no double billing.
    again = await run_autopilot(
        storage, slack=slack, channel="D1",
        make_qualifier=lambda: ScriptedLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
    )
    assert again.candidates == 0


@pytest.mark.asyncio
async def test_daily_budget_shared_across_runs(storage, tmp_path,
                                               monkeypatch):
    """Hourly scheduling safety: 24 runs share one daily draft budget."""
    from datetime import datetime, timezone
    monkeypatch.chdir(tmp_path)
    # One draft already made today...
    await _seed(storage, "1", verdict="qualified", fit_score=95,
                application_status="drafting",
                drafted_at=datetime.now(timezone.utc))
    # ...and a fresh candidate waiting.
    waiting = await _seed(storage, "2", verdict="qualified", fit_score=90)

    stats = await run_autopilot(
        storage, slack=StubSlack(), channel="D1",
        make_qualifier=lambda: ScriptedLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        max_drafts=3, daily_cap=1,
    )
    assert stats.drafted == 0          # budget already spent today
    assert stats.budget_left == 0
    assert (await storage.get_job(waiting)).get("application_status") is None

    # Raise the cap: the waiting candidate gets its pack.
    stats = await run_autopilot(
        storage, slack=StubSlack(), channel="D1",
        make_qualifier=lambda: ScriptedLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        max_drafts=3, daily_cap=2,
    )
    assert stats.drafted == 1


@pytest.mark.asyncio
async def test_record_draft_stamps_drafted_at(storage):
    job_id = await _seed(storage, "1", verdict="qualified", fit_score=90)
    assert await storage.drafts_today() == 0
    await storage.record_draft(job_id, "drafts/x.md")
    assert await storage.drafts_today() == 1
    from datetime import datetime
    assert isinstance((await storage.get_job(job_id))["drafted_at"],
                      datetime)


@pytest.mark.asyncio
async def test_failed_draft_is_not_delivered_and_stays_eligible(
        storage, tmp_path, monkeypatch):
    """A malformed LLM draft must never ship as a pack card, and the job
    must stay in the candidate pool for the next pass."""
    monkeypatch.chdir(tmp_path)
    job_id = await _seed(storage, "1", verdict="qualified", fit_score=90)

    class GarbageLLM:
        model_name = "fake"

        async def complete(self, system, user):
            return "reasoning prose with no json at all"

    slack = StubSlack()
    stats = await run_autopilot(
        storage, slack=slack, channel="D1",
        make_qualifier=lambda: GarbageLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
    )
    assert stats.drafted == 0 and stats.delivered == 0
    assert stats.errors and "will retry" in stats.errors[0]
    assert slack.posts == []
    row = await storage.get_job(job_id)
    assert row.get("application_status") is None  # still eligible


@pytest.mark.asyncio
async def test_run_autopilot_disabled_by_zero_cap(storage):
    await _seed(storage, "1", verdict="qualified", fit_score=99)
    stats = await run_autopilot(
        storage, slack=StubSlack(), channel="D1",
        make_qualifier=lambda: ScriptedLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="x"),
        max_drafts=0,
    )
    assert stats.candidates == 0 and stats.drafted == 0


# --- review card ---

def test_pack_blocks_have_all_buttons():
    from karani.drafting.models import DraftPackage
    pkg = DraftPackage(cover_letter="Letter body.", keyword_coverage=0.8)
    blocks = pack_blocks({"id": 7, "company_display": "GitLab",
                          "title": "SBE", "fit_score": 90,
                          "apply_url": "https://x.example"}, pkg)
    actions = [b for b in blocks if b["type"] == "actions"][0]
    ids = [e["action_id"] for e in actions["elements"]]
    assert ids == ["pack_approve", "pack_skip",
                   "pack_applied_warm", "pack_applied_cold"]
    assert all(e["value"] == "7" for e in actions["elements"])
    assert "80%" in json.dumps(blocks)


# --- button clicks ---

def _click(action_id: str, value: str) -> dict:
    return {"actions": [{"action_id": action_id, "value": value}],
            "channel": {"id": "D1"}, "message": {"ts": "1.0"}}


@pytest.mark.asyncio
async def test_interaction_approve(storage):
    job_id = await _seed(storage, "1", verdict="qualified", fit_score=90)
    memory = MemoryManager(storage, mode="basic")
    reply = await handle_interaction(_click("pack_approve", str(job_id)),
                                     storage=storage, memory=memory)
    assert "approved" in reply
    row = await storage.get_job(job_id)
    assert row["application_status"] == "ready"
    assert row["user_verdict"] == "apply"
    recalled = await memory.recall("GitLab Senior Backend")
    assert any("verdict 'apply'" in r["content"] for r in recalled)


@pytest.mark.asyncio
async def test_interaction_skip_and_applied(storage):
    a = await _seed(storage, "1", verdict="qualified", fit_score=90)
    b = await _seed(storage, "2", verdict="qualified", fit_score=91)
    memory = MemoryManager(storage, mode="basic")

    reply = await handle_interaction(_click("pack_skip", str(a)),
                                     storage=storage, memory=memory)
    assert "Skipped" in reply
    assert (await storage.get_job(a))["user_verdict"] == "skip"

    reply = await handle_interaction(_click("pack_applied_warm", str(b)),
                                     storage=storage, memory=memory)
    assert "warm path" in reply
    row = await storage.get_job(b)
    assert row["application_status"] == "applied"
    assert row["warm_path_used"] is True


@pytest.mark.asyncio
async def test_interaction_ignores_unknown_and_survives_bad_input(storage):
    assert await handle_interaction({"actions": []}, storage=storage) is None
    assert await handle_interaction(_click("other_button", "1"),
                                    storage=storage) is None
    out = await handle_interaction(_click("pack_approve", "999"),
                                   storage=storage)
    assert "no longer exists" in out
    out = await handle_interaction(_click("pack_approve", "not-a-number"),
                                   storage=storage)
    assert "manually" in out
