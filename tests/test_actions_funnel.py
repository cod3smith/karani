"""next_actions bucketing + funnel_stats aggregation (in-memory Storage)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ingestion.filters import pre_filter
from ingestion.models import Job, RemoteStatus, Source
from ingestion.profile import DEFAULT_PROFILE
from ingestion.storage import Storage

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _job(source_id: str, title: str = "Senior Backend Engineer") -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title=title,
        location_raw="Remote",
        remote_status=RemoteStatus.REMOTE,
        description_text=(
            "We hire globally. Python required. "
            "Salary: $180,000 - $220,000. Same pay regardless of location."
        ),
        apply_url=f"https://example.com/apply/{source_id}",
        posted_at=NOW - timedelta(days=2),
    ).finalize()


async def _seed(storage: Storage, source_id: str, **overrides) -> int:
    job = _job(source_id)
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    row = await storage.get_job(result["id"])
    row.update(overrides)
    return result["id"]


@pytest.mark.asyncio
async def test_next_actions_buckets():
    storage = Storage("")
    await storage.connect()

    a = await _seed(storage, "1", verdict="qualified", fit_score=92)
    b = await _seed(storage, "2", verdict="qualified", fit_score=80,
                    user_verdict="apply")
    c = await _seed(storage, "3", verdict="qualified", fit_score=75,
                    user_verdict="apply", application_status="drafting")
    d = await _seed(storage, "4", verdict="qualified", fit_score=88,
                    user_verdict="apply", application_status="applied",
                    applied_at=NOW - timedelta(days=10))
    # Fresh application — not yet due for follow-up.
    await _seed(storage, "5", verdict="qualified", fit_score=85,
                user_verdict="apply", application_status="applied",
                applied_at=NOW - timedelta(days=2))
    # Closed out — must appear nowhere.
    await _seed(storage, "6", verdict="qualified", fit_score=95,
                outcome="rejection")

    buckets = await storage.next_actions(follow_up_days=7, now=NOW)
    assert [x["id"] for x in buckets["review"]] == [a]
    assert [x["id"] for x in buckets["to_draft"]] == [b]
    assert [x["id"] for x in buckets["to_submit"]] == [c]
    assert [x["id"] for x in buckets["follow_up"]] == [d]
    assert buckets["follow_up"][0]["applied_days_ago"] == 10
    assert buckets["review"][0]["posted_days_ago"] == 2


@pytest.mark.asyncio
async def test_next_actions_review_ranked_by_fit_then_freshness():
    storage = Storage("")
    await storage.connect()
    low = await _seed(storage, "1", verdict="maybe", fit_score=70)
    high = await _seed(storage, "2", verdict="qualified", fit_score=95)
    buckets = await storage.next_actions(now=NOW)
    assert [x["id"] for x in buckets["review"]] == [high, low]


@pytest.mark.asyncio
async def test_funnel_stats_rates_and_splits():
    storage = Storage("")
    await storage.connect()

    # Applied, no response yet.
    await _seed(storage, "1", fit_score=92, application_status="applied",
                applied_at=NOW, draft_prompt_version="draft-v1")
    # Applied -> interview -> offer.
    await _seed(storage, "2", fit_score=91, application_status="offer",
                applied_at=NOW, outcome="offer",
                draft_prompt_version="draft-v2")
    # Applied -> ghosted.
    await _seed(storage, "3", fit_score=72, application_status="applied",
                applied_at=NOW, outcome="ghosted",
                draft_prompt_version="draft-v1")
    # Never applied — must not count.
    await _seed(storage, "4", fit_score=88)

    f = await storage.funnel_stats()
    t = f["totals"]
    assert t["applied"] == 3
    assert t["responded"] == 1
    assert t["interviewed"] == 1
    assert t["offers"] == 1
    assert t["ghosted"] == 1
    assert t["response_rate"] == round(1 / 3, 3)

    assert f["by_fit_band"]["90+"] == {
        "applied": 2, "responded": 1, "response_rate": 0.5,
    }
    assert f["by_fit_band"]["70-79"]["responded"] == 0
    assert f["by_draft_prompt"]["draft-v2"]["response_rate"] == 1.0
    assert f["by_draft_prompt"]["draft-v1"]["response_rate"] == 0.0
    assert f["by_source"]["greenhouse"]["applied"] == 3


@pytest.mark.asyncio
async def test_record_draft_persists_provenance():
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", verdict="qualified")
    await storage.record_draft(job_id, "drafts/x.md",
                               prompt_version="draft-v1", model="fake-model")
    row = await storage.get_job(job_id)
    assert row["application_status"] == "drafting"
    assert row["draft_path"] == "drafts/x.md"
    assert row["draft_prompt_version"] == "draft-v1"
    assert row["draft_model"] == "fake-model"
