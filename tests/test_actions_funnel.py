"""next_actions bucketing + funnel_stats aggregation (in-memory Storage)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage

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
async def test_fast_lane_flags_fresh_high_fit():
    storage = Storage("")
    await storage.connect()
    fresh_high = await _seed(storage, "1", verdict="qualified", fit_score=90)
    stale_high = await _seed(storage, "2", verdict="qualified", fit_score=90,
                             posted_at=NOW - timedelta(days=10))
    fresh_low = await _seed(storage, "3", verdict="qualified", fit_score=70)
    buckets = await storage.next_actions(now=NOW)
    flags = {a["id"]: a["fast_lane"] for a in buckets["review"]}
    assert flags[fresh_high] is True
    assert flags[stale_high] is False   # high fit, but response odds decayed
    assert flags[fresh_low] is False    # fresh, but not worth same-day rush


@pytest.mark.asyncio
async def test_autopsy_separates_responders_from_silence():
    storage = Storage("")
    await storage.connect()
    await _seed(storage, "1", application_status="interview", applied_at=NOW,
                seniority="senior", remote_status="remote",
                draft_keyword_coverage=0.9)
    await _seed(storage, "2", application_status="applied", applied_at=NOW,
                outcome="ghosted", seniority="staff", remote_status="remote",
                draft_keyword_coverage=0.4)
    autopsy = (await storage.funnel_stats())["autopsy"]
    assert autopsy["by_seniority"]["senior"]["response_rate"] == 1.0
    assert autopsy["by_seniority"]["staff"]["response_rate"] == 0.0
    assert autopsy["keyword_coverage"] == {"responded_avg": 0.9,
                                           "silent_avg": 0.4}


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
async def test_funnel_warm_vs_cold_and_posting_age():
    storage = Storage("")
    await storage.connect()
    # Warm application to a fresh posting -> responded.
    await _seed(storage, "1", application_status="screen",
                applied_at=NOW - timedelta(days=1),
                posted_at=NOW - timedelta(days=2),
                warm_path_used=True)
    # Cold application to a stale posting -> silence.
    await _seed(storage, "2", application_status="applied",
                applied_at=NOW,
                posted_at=NOW - timedelta(days=20),
                warm_path_used=False)
    # Unmarked application, posting date unknown.
    await _seed(storage, "3", application_status="applied", applied_at=NOW,
                posted_at=None)

    f = await storage.funnel_stats()
    assert f["by_warm_path"]["warm"] == {
        "applied": 1, "responded": 1, "response_rate": 1.0}
    assert f["by_warm_path"]["cold"]["response_rate"] == 0.0
    assert f["by_warm_path"]["unmarked"]["applied"] == 1
    # posted 2d before applying -> 0-3d band; 20d -> 15d+.
    assert f["by_posting_age"]["0-3d"]["responded"] == 1
    assert f["by_posting_age"]["15d+"]["applied"] == 1
    assert f["by_posting_age"]["0-3d"]["applied"] == 1
    assert f["by_posting_age"]["unknown"]["applied"] == 1


@pytest.mark.asyncio
async def test_set_status_warm_flag_roundtrip():
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1")
    await storage.set_application_status(job_id, "applied", warm_path=True)
    assert (await storage.get_job(job_id))["warm_path_used"] is True
    # None leaves the flag untouched on later transitions.
    await storage.set_application_status(job_id, "screen")
    assert (await storage.get_job(job_id))["warm_path_used"] is True


@pytest.mark.asyncio
async def test_update_prefilter_roundtrip():
    """`refilter` support: re-judged rows persist the new verdict."""
    from karani.ingestion.filters import pre_filter as pf_run
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1")
    row = (await storage.active_jobs())[0]
    assert row["id"] == job_id

    # Re-judge with a bio title: the row must flip to failing.
    bio = _job("1")
    bio.title = "Senior Computational Biologist"
    result = pf_run(bio)
    assert not result.pass_hard_filters
    await storage.update_prefilter(job_id, result)
    updated = await storage.get_job(job_id)
    assert updated["prefilter_passed"] is False
    assert updated["prefilter_score"] == result.score


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


# --- cross-run canonical dedup (roadmap 0.1) ---

async def _seed_feed_twin(storage: Storage, source_id: str, **overrides) -> int:
    """Same company+title+week as _job() but from a feed source, so it
    shares a canonical_hash with the ATS row — the across-runs duplicate
    the orchestrator's within-run dedup cannot catch."""
    job = _job(source_id)
    job.source = Source.REMOTEOK
    job.content_hash = ""
    job.canonical_hash = ""
    job.finalize()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(overrides)
    return result["id"]


@pytest.mark.asyncio
async def test_top_qualified_dedupes_canonical_across_sources():
    storage = Storage("")
    await storage.connect()
    ats = await _seed(storage, "1", verdict="qualified", fit_score=90)
    feed = await _seed_feed_twin(storage, "99", verdict="qualified",
                                 fit_score=95)
    assert (await storage.get_job(ats))["canonical_hash"] == \
        (await storage.get_job(feed))["canonical_hash"]

    rows = await storage.top_qualified(limit=10)
    assert [r["id"] for r in rows] == [ats]   # ATS wins despite lower fit


@pytest.mark.asyncio
async def test_autopilot_candidates_dedupe_prevents_double_packs():
    storage = Storage("")
    await storage.connect()
    ats = await _seed(storage, "1", verdict="qualified", fit_score=90)
    await _seed_feed_twin(storage, "99", verdict="qualified", fit_score=95)
    cands = await storage.autopilot_candidates(min_fit=85, limit=5)
    assert [r["id"] for r in cands] == [ats]


@pytest.mark.asyncio
async def test_dedup_keeps_distinct_roles_and_fills_limit():
    storage = Storage("")
    await storage.connect()
    a = await _seed(storage, "1", verdict="qualified", fit_score=95)
    b_job = _job("2", title="Staff Platform Engineer")
    result = await storage.upsert(b_job, pre_filter(b_job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(verdict="qualified",
                                                 fit_score=90)
    rows = await storage.top_qualified(limit=10)
    assert [r["id"] for r in rows] == [a, result["id"]]
