"""Real-Postgres coverage for the SQL the in-memory fallback hides.

Every other test runs on `Storage("")`, so the production statements —
the UPSERT's conditional qualification reset, the resume-hash pending
gate, funnel FILTERs, next_actions, canonical dedup, the memory ledger,
the run ledger, and session-scoped advisory locks — had zero coverage
(roadmap 0.3). These run against a throwaway database on the compose
Postgres: `make test-pg` / `pytest -m pg`.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage

pytestmark = pytest.mark.pg

ADMIN_URL = os.getenv(
    "KARANI_TEST_PG_URL",
    "postgresql://karani:karani@localhost:5433/postgres")

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def pg():
    """A pristine database per test — no shared state, no cleanup order
    dependencies. Skips (never fails) when Postgres is unreachable."""
    try:
        import asyncpg
        admin = await asyncio.wait_for(asyncpg.connect(ADMIN_URL), timeout=5)
    except Exception as exc:                      # pragma: no cover
        pytest.skip(f"postgres unreachable at {ADMIN_URL}: {exc}")

    dbname = f"karani_test_{uuid.uuid4().hex[:10]}"
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    storage = Storage(ADMIN_URL.rsplit("/", 1)[0] + f"/{dbname}")
    await storage.connect()
    assert storage.pool is not None, "fell back to memory — check the DSN"
    try:
        yield storage
    finally:
        await storage.close()
        await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
        await admin.close()


def _job(source_id: str, *, source: Source = Source.GREENHOUSE,
         title: str = "Senior Backend Engineer", extra: str = "") -> Job:
    return Job(
        source=source, source_id=source_id,
        company="gitlab", company_display="GitLab", title=title,
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python and Go required. "
                          "Salary: $180,000 - $220,000. Same pay "
                          "regardless of location. " + extra),
        apply_url=f"https://example.com/{source_id}",
        posted_at=NOW - timedelta(days=2),
    ).finalize()


async def _seed(pg: Storage, source_id: str = "1", **kw) -> int:
    job = _job(source_id, **kw)
    return (await pg.upsert(job, pre_filter(job, DEFAULT_PROFILE)))["id"]


class _Qual:
    """Minimal QualificationResult stand-in for store_qualification."""

    def __init__(self, verdict="qualified", fit=90, resume_hash="rh1"):
        self.verdict, self.fit_score, self.resume_hash = (
            verdict, fit, resume_hash)

    def model_dump_json(self):
        return json.dumps({"verdict": self.verdict,
                           "fit_score": self.fit_score})


# --- the UPSERT: the most conditional SQL in the codebase ---

@pytest.mark.asyncio
async def test_upsert_inserts_then_updates_without_duplicating(pg):
    job_id = await _seed(pg, "1")
    again = await pg.upsert(_job("1"), pre_filter(_job("1"), DEFAULT_PROFILE))
    assert again["id"] == job_id and again["inserted"] is False
    assert (await pg.stats())["total"] == 1


@pytest.mark.asyncio
async def test_unchanged_content_keeps_qualification_changed_resets_it(pg):
    job_id = await _seed(pg, "1")
    await pg.store_qualification(job_id, _Qual())
    assert (await pg.get_job(job_id))["verdict"] == "qualified"

    same = _job("1")
    await pg.upsert(same, pre_filter(same, DEFAULT_PROFILE))
    assert (await pg.get_job(job_id))["verdict"] == "qualified", \
        "cosmetic re-ingest must not throw away billed qualification"

    changed = _job("1", extra="Now also requires Rust.")
    await pg.upsert(changed, pre_filter(changed, DEFAULT_PROFILE))
    assert (await pg.get_job(job_id))["verdict"] is None, \
        "materially changed JD must re-qualify"


@pytest.mark.asyncio
async def test_pending_qualification_gates_on_resume_hash(pg):
    job_id = await _seed(pg, "1")
    pending = await pg.pending_qualification(limit=10, resume_hash="rh1")
    assert [r["id"] for r in pending] == [job_id]

    await pg.store_qualification(job_id, _Qual(resume_hash="rh1"))
    assert await pg.pending_qualification(limit=10, resume_hash="rh1") == []
    # New resume -> everything is pending again.
    assert [r["id"] for r in await pg.pending_qualification(
        limit=10, resume_hash="rh2")] == [job_id]


# --- review surfaces ---

@pytest.mark.asyncio
async def test_top_qualified_dedupes_and_respects_user_verdict(pg):
    ats = await _seed(pg, "1")
    await pg.store_qualification(ats, _Qual(fit=90))
    feed = await _seed(pg, "2", source=Source.REMOTEOK)   # same canonical
    await pg.store_qualification(feed, _Qual(fit=95))

    rows = await pg.top_qualified(limit=10)
    assert [r["id"] for r in rows] == [ats], "ATS copy wins the dedup"

    await pg.set_user_verdict(ats, "apply")
    assert await pg.top_qualified(limit=10) == []


@pytest.mark.asyncio
async def test_autopilot_candidates_and_next_actions_sql(pg):
    job_id = await _seed(pg, "1")
    await pg.store_qualification(job_id, _Qual(fit=92))

    assert [r["id"] for r in await pg.autopilot_candidates(
        min_fit=85, limit=5)] == [job_id]
    assert [x["id"] for x in (await pg.next_actions())["review"]] == [job_id]

    await pg.set_user_verdict(job_id, "apply")
    buckets = await pg.next_actions()
    assert buckets["review"] == []
    assert [x["id"] for x in buckets["to_draft"]] == [job_id]
    assert await pg.autopilot_candidates(min_fit=85, limit=5) == []


# --- state machine + funnel aggregates ---

@pytest.mark.asyncio
async def test_state_machine_and_funnel_filters(pg):
    job_id = await _seed(pg, "1")
    await pg.store_qualification(job_id, _Qual(fit=91))
    await pg.record_draft(job_id, "drafts/x.md", prompt_version="draft-v3",
                          model="m", keyword_coverage=0.8)
    await pg.set_application_status(job_id, "applied", warm_path=True)
    await pg.add_stage(job_id, "screen", "intro call")
    await pg.set_outcome(job_id, "offer")

    row = await pg.get_job(job_id)
    assert row["warm_path_used"] is True
    assert row["application_status"] == "applied"
    assert row["outcome"] == "offer"
    assert row["stages"] and json.loads(row["stages"])[0]["stage"] == "screen" \
        if isinstance(row["stages"], str) else row["stages"][0]["stage"] == "screen"

    f = await pg.funnel_stats()
    assert f["totals"]["applied"] == 1
    assert f["totals"]["offers"] == 1
    assert f["by_warm_path"]["warm"]["applied"] == 1
    assert f["by_draft_prompt"]["draft-v3"]["applied"] == 1
    assert f["autopsy"]["keyword_coverage"]["responded_avg"] == 0.8


@pytest.mark.asyncio
async def test_sweep_stale_closes_old_rows(pg):
    job_id = await _seed(pg, "1")
    async with pg.pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET last_seen_at = NOW() - INTERVAL '30 days' "
            "WHERE id = $1", job_id)
    assert await pg.sweep_stale(10) == 1
    assert (await pg.get_job(job_id))["active"] is False


# --- memory + intel + run ledger ---

@pytest.mark.asyncio
async def test_memory_ledger_dedup_and_recall(pg):
    first = await pg.add_memory("GitLab replied within three days",
                                "company", company="GitLab")
    assert first["deduped"] is False
    again = await pg.add_memory("GitLab replied within three days",
                                "company", company="GitLab")
    assert again["deduped"] is True and again["id"] == first["id"]

    hits = await pg.recall_memories("GitLab reply speed", company="GitLab")
    assert hits and "three days" in hits[0]["content"]
    assert await pg.deactivate_memory(first["id"]) is True
    assert await pg.recall_memories("GitLab reply speed") == []


@pytest.mark.asyncio
async def test_company_intel_roundtrip_with_injected_timestamp(pg):
    await pg.save_company_intel("GitLab", {"github": "repos"}, now=NOW)
    cached = await pg.get_company_intel("GitLab")
    assert cached["payload"]["github"] == "repos"
    assert cached["fetched_at"] == NOW


@pytest.mark.asyncio
async def test_run_ledger_roundtrip(pg):
    assert await pg.last_run_at("hourly") is None
    await pg.record_run("hourly", started_at=NOW - timedelta(minutes=1),
                        finished_at=NOW, state={"ingest": {"fetched": 2}},
                        tokens={"calls": 4}, errors=0)
    assert await pg.last_run_at("hourly") == NOW
    runs = await pg.recent_runs("hourly")
    assert runs[0]["errors"] == 0


# --- advisory locks: the semantics only Postgres can prove ---

@pytest.mark.asyncio
async def test_advisory_lock_blocks_a_separate_connection(pg):
    """The in-memory fallback can only prove same-process reentrancy.
    This proves the real cross-process claim: a second Storage with its
    own pool (i.e. another karani process) cannot take the lock."""
    other = Storage(pg.dsn)
    await other.connect()
    try:
        async with pg.run_lock("qualify") as first:
            assert first is True
            async with other.run_lock("qualify") as second:
                assert second is False
        # Released — the other process can now take it.
        async with other.run_lock("qualify") as third:
            assert third is True
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_advisory_lock_released_after_exception(pg):
    with pytest.raises(RuntimeError):
        async with pg.run_lock("autopilot"):
            raise RuntimeError("boom")
    async with pg.run_lock("autopilot") as got:
        assert got is True
