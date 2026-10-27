"""The submission queue and the write-back that moves `applied` off zero."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage

NOW = datetime(2026, 10, 27, 9, 0, 0, tzinfo=timezone.utc)


def _job(source_id: str, title: str = "Staff Backend Engineer") -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab", title=title,
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
async def test_submission_queue_holds_exactly_what_is_waiting_to_be_sent():
    storage = Storage("")
    await storage.connect()

    ready = await _seed(storage, "1", verdict="qualified", fit_score=91,
                        application_status="ready")
    drafting = await _seed(storage, "2", verdict="qualified", fit_score=95,
                           application_status="drafting")
    await _seed(storage, "3", verdict="qualified", fit_score=99,
                application_status="applied")
    await _seed(storage, "4", verdict="qualified", fit_score=99,
                application_status="ready", outcome="rejection")
    await _seed(storage, "5", verdict="qualified", fit_score=99,
                application_status="ready", active=False)

    queue = await storage.submission_queue()
    assert [r["id"] for r in queue] == [drafting, ready]  # fit desc


@pytest.mark.asyncio
async def test_mark_submitted_records_status_time_and_artifact_version():
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", application_status="ready",
                         artifacts={"resume.pdf": "00001-gitlab/resume.pdf"})

    assert await storage.mark_submitted(job_id, via="console") is True

    row = await storage.get_job(job_id)
    assert row["application_status"] == "applied"
    assert isinstance(row["applied_at"], datetime)
    assert row["submitted_via"] == "console"
    # The artifact version that was actually sent, frozen at send time —
    # a later re-draft must not rewrite history.
    assert row["submitted_artifacts"] == {
        "resume.pdf": "00001-gitlab/resume.pdf"}
    assert [s["stage"] for s in row["stages"]] == ["submitted"]

    await storage.set_artifacts(job_id, {"resume.pdf": "00001-gitlab/v2.pdf"})
    row = await storage.get_job(job_id)
    assert row["submitted_artifacts"] == {
        "resume.pdf": "00001-gitlab/resume.pdf"}


@pytest.mark.asyncio
async def test_mark_submitted_can_flag_a_warm_path_and_an_unknown_job():
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", application_status="ready")

    await storage.mark_submitted(job_id, warm_path=True, notes="referred by A")
    row = await storage.get_job(job_id)
    assert row["warm_path_used"] is True
    assert row["stages"][0]["notes"] == "referred by A"

    assert await storage.mark_submitted(999999) is False


@pytest.mark.asyncio
async def test_question_sets_are_stored_per_job():
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1")

    payload = {"ats": "greenhouse", "introspected": True,
               "questions": [{"key": "q1", "label": "Why us?",
                              "kind": "textarea"}]}
    await storage.set_ats_questions(job_id, payload)

    row = await storage.get_job(job_id)
    assert row["ats_questions"] == payload
