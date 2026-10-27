"""The two CLI verbs that bracket a submission session."""
from __future__ import annotations

import pytest

from karani import cli
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage


def _job() -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id="1", company="acme",
        company_display="Acme", title="Staff Backend Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. Salary: "
                          "$180,000 - $220,000. Same pay regardless of "
                          "location."),
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
    ).finalize()


@pytest.fixture
async def seeded(monkeypatch):
    storage = Storage("")
    await storage.connect()
    job = _job()
    job_id = (await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE)))["id"]
    (await storage.get_job(job_id))["application_status"] = "ready"
    monkeypatch.setattr(cli, "Storage", lambda dsn: storage)
    return storage, job_id


@pytest.mark.asyncio
async def test_console_writes_the_file_and_says_where(seeded, tmp_path, capsys):
    out = tmp_path / "console.html"
    assert await cli._console(50, str(out), False) == 0
    assert out.exists()
    assert "Staff Backend Engineer" in out.read_text()
    assert str(out) in capsys.readouterr().out


@pytest.mark.asyncio
async def test_submitted_records_the_send_and_reports_unknown_ids(
        seeded, capsys):
    storage, job_id = seeded

    assert await cli._submitted([job_id], None, "console") == 0
    row = await storage.get_job(job_id)
    assert row["application_status"] == "applied"
    assert row["submitted_via"] == "console"

    assert await cli._submitted([999999], None, "console") == 1
    assert "999999" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_submitted_can_mark_a_warm_path(seeded):
    storage, job_id = seeded
    await cli._submitted([job_id], True, "referral")
    row = await storage.get_job(job_id)
    assert row["warm_path_used"] is True
    assert row["submitted_via"] == "referral"


def test_both_verbs_are_wired_into_the_parser(monkeypatch):
    monkeypatch.setattr("sys.argv", ["karani", "console", "--limit", "5"])
    calls: list[tuple] = []
    monkeypatch.setattr(cli.asyncio, "run", lambda coro: calls.append(coro)
                        or coro.close() or 0)
    with pytest.raises(SystemExit):
        cli.main()
    assert calls

    monkeypatch.setattr("sys.argv", ["karani", "submitted", "12", "34"])
    with pytest.raises(SystemExit):
        cli.main()
    assert len(calls) == 2
