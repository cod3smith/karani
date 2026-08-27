"""In-memory Storage covers state machine, feedback, discovered companies,
and the DB-fallback path."""
from __future__ import annotations

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.storage import Storage
from karani.qualification.models import QualificationResult


def _make_job(source_id="g1", title="Senior Backend Engineer", **kwargs):
    defaults = dict(
        source=Source.GREENHOUSE, source_id=source_id,
        company="c", company_display="Co", title=title,
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=(
            "Python + Go. Salary $200,000 - $260,000. Hire globally. "
            "Same pay regardless of location."
        ),
        apply_url="https://x/1",
    )
    defaults.update(kwargs)
    return Job(**defaults).finalize()


@pytest.mark.asyncio
async def test_storage_falls_back_when_db_is_unavailable(monkeypatch):
    async def fail_create_pool(*args, **kwargs):
        raise OSError("database unavailable")
    import karani.ingestion.storage as storage_module
    monkeypatch.setattr(storage_module.asyncpg, "create_pool", fail_create_pool)

    storage = Storage("postgresql://localhost/jobs")
    await storage.connect()
    assert storage.pool is None  # fell back to in-memory

    job = _make_job()
    result = await storage.upsert(job, pre_filter(job))
    stats = await storage.stats()
    assert result["inserted"] is True
    assert stats["total"] == 1
    assert stats["passed"] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_upsert_then_pending_then_qualify():
    storage = Storage("")
    await storage.connect()
    job = _make_job()
    pf = pre_filter(job)
    r = await storage.upsert(job, pf)
    assert r["inserted"]

    pending = await storage.pending_qualification(limit=10, resume_hash="h1")
    assert len(pending) == 1

    result = QualificationResult(fit_score=85, verdict="qualified",
                                  resume_hash="h1")
    await storage.store_qualification(pending[0]["id"], result)

    top = await storage.top_qualified(limit=10)
    assert len(top) == 1
    assert top[0]["verdict"] == "qualified"

    # Same resume hash → already qualified, nothing pending.
    again = await storage.pending_qualification(limit=10, resume_hash="h1")
    assert len(again) == 0


@pytest.mark.asyncio
async def test_state_machine_transitions():
    storage = Storage("")
    await storage.connect()
    job = _make_job()
    await storage.upsert(job, pre_filter(job))

    row = await _first_row(storage)
    jid = row["id"]

    await storage.set_application_status(jid, "drafting",
                                          draft_path="drafts/foo.md")
    row = await _first_row(storage)
    assert row["application_status"] == "drafting"
    assert row["draft_path"] == "drafts/foo.md"

    await storage.set_application_status(jid, "applied")
    row = await _first_row(storage)
    assert row["application_status"] == "applied"

    await storage.add_stage(jid, "recruiter_screen", notes="30-min chat")
    row = await _first_row(storage)
    assert row["stages"][-1]["stage"] == "recruiter_screen"

    await storage.set_outcome(jid, "offer")
    row = await _first_row(storage)
    assert row["outcome"] == "offer"


@pytest.mark.asyncio
async def test_pipeline_summary():
    storage = Storage("")
    await storage.connect()
    job1 = _make_job(source_id="g1")
    job2 = _make_job(source_id="g2", title="Staff Backend Engineer")
    for j in (job1, job2):
        await storage.upsert(j, pre_filter(j))
    rows = list(storage._memory.values())
    await storage.set_application_status(rows[0]["id"], "applied")
    await storage.set_application_status(rows[1]["id"], "drafting")

    summary = await storage.pipeline_summary()
    assert summary["applied"] == 1
    assert summary["drafting"] == 1


@pytest.mark.asyncio
async def test_bad_status_rejected():
    storage = Storage("")
    await storage.connect()
    with pytest.raises(ValueError):
        await storage.set_application_status(1, "not_a_status")


@pytest.mark.asyncio
async def test_recent_user_verdicts_memory_fallback():
    storage = Storage("")
    await storage.connect()
    job = _make_job()
    await storage.upsert(job, pre_filter(job))
    row = await _first_row(storage)
    await storage.set_user_verdict(row["id"], "apply")
    recents = await storage.recent_user_verdicts(limit=10)
    assert len(recents) == 1
    assert recents[0]["user_verdict"] == "apply"


async def _first_row(storage: Storage) -> dict:
    return next(iter(storage._memory.values()))
