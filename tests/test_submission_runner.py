"""Queue -> question sets -> answer sheets -> one HTML file."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage
from karani.submission.runner import build_console

RESUME = """# Kelyn Njeri
Nairobi, Kenya · kelyn@example.com · +254 700 000 000

## Experience

### Acme - Staff Engineer
- Built a thing.
"""

GREENHOUSE_DETAIL = {"questions": [
    {"label": "First Name", "required": True,
     "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
    {"label": "Why do you want to work here?", "required": True,
     "fields": [{"name": "question_900", "type": "textarea", "values": []}]},
]}


def _job(source_id: str, title: str) -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="acme", company_display="Acme", title=title,
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. Salary: "
                          "$180,000 - $220,000. Same pay regardless of "
                          "location."),
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{source_id}",
    ).finalize()


async def _seed(storage: Storage, source_id: str, title: str,
                **overrides) -> int:
    job = _job(source_id, title)
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    row = await storage.get_job(result["id"])
    row.update(overrides)
    return result["id"]


@pytest.mark.asyncio
async def test_the_console_covers_the_queue_and_caches_what_it_learned(
        tmp_path, monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "on")
    storage = Storage("")
    await storage.connect()
    first = await _seed(storage, "1", "Staff Backend Engineer",
                        application_status="ready", fit_score=91)
    await _seed(storage, "2", "Research Engineer",
                application_status="drafting", fit_score=88)
    await _seed(storage, "3", "Already Sent", application_status="applied")

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=GREENHOUSE_DETAIL)

    out = tmp_path / "console.html"
    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        result = await build_console(storage, output=out, client=client,
                                     resume_markdown=RESUME)

    assert result.path == out
    html = out.read_text()
    assert "Staff Backend Engineer" in html
    assert "Research Engineer" in html
    assert "Already Sent" not in html          # already submitted
    assert len(calls) == 2                      # one per queued job
    assert "Kelyn" in html                      # identity from the resume

    # The form was learned once and stored, so the next pass is free.
    row = await storage.get_job(first)
    assert row["ats_questions"]["introspected"] is True
    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        await build_console(storage, output=out, client=client,
                            resume_markdown=RESUME)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_no_client_no_network_and_still_a_usable_console(tmp_path):
    storage = Storage("")
    await storage.connect()
    await _seed(storage, "1", "Staff Backend Engineer",
                application_status="ready")

    result = await build_console(storage, output=tmp_path / "c.html",
                                 resume_markdown=RESUME)

    assert result.sheets[0].introspected is False
    assert any(a.key == "work_authorization" for a in result.sheets[0].answers)
    assert result.path.exists()


@pytest.mark.asyncio
async def test_the_drafted_pack_is_read_off_disk(tmp_path):
    storage = Storage("")
    await storage.connect()
    draft = tmp_path / "pack.md"
    draft.write_text("## Cover letter\n\nDear Acme, I built the thing.\n\n"
                     "## Application question answers\n\n"
                     "### Why do you want to work here?\n\n"
                     "Because the platform is the product.\n")
    await _seed(storage, "1", "Staff Backend Engineer",
                application_status="ready", draft_path=str(draft))

    result = await build_console(storage, output=tmp_path / "c.html",
                                 resume_markdown=RESUME)
    html = result.path.read_text()
    assert "Because the platform is the product." in html


@pytest.mark.asyncio
async def test_an_empty_queue_still_writes_a_page(tmp_path):
    storage = Storage("")
    await storage.connect()
    result = await build_console(storage, output=tmp_path / "c.html",
                                 resume_markdown=RESUME)
    assert result.sheets == []
    assert Path(result.path).exists()
