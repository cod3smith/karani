"""Notion mirror: client wire format, idempotent sync, page recreation,
and the best-effort single-job push. No network — MockTransport + fakes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

import notionsync.client as client_mod
from ingestion.filters import pre_filter
from ingestion.models import Job, RemoteStatus, Source
from ingestion.profile import DEFAULT_PROFILE
from ingestion.storage import Storage
from notionsync import NotionClient, NotionError, maybe_sync_job, sync_jobs
from notionsync.sync import (DATABASE_PROPERTIES, _page_properties,
                             init_database)

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


FULL_SCHEMA = {
    name: {"type": ("title" if "title" in spec else "select"), **spec}
    for name, spec in DATABASE_PROPERTIES.items()
}


@pytest.fixture
def notion_http(monkeypatch):
    import notionsync.sync as sync_mod
    monkeypatch.setattr(sync_mod, "_schema_cache", {})
    state = {"requests": [], "responses": [], "page_counter": 0,
             "db_properties": dict(FULL_SCHEMA)}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if state["responses"]:
            return state["responses"].pop(0)
        if (request.url.path.startswith("/v1/databases/")
                and request.method == "GET"):
            return httpx.Response(200, json={
                "id": request.url.path.split("/")[-1],
                "properties": state["db_properties"],
            })
        if (request.url.path.startswith("/v1/databases/")
                and request.method == "PATCH"):
            state["db_properties"].update(
                json.loads(request.content).get("properties", {}))
            return httpx.Response(200, json={"id": "db-1"})
        if request.url.path == "/v1/pages" and request.method == "POST":
            state["page_counter"] += 1
            return httpx.Response(200, json={"id": f"page-{state['page_counter']}"})
        if request.url.path.startswith("/v1/pages/"):
            return httpx.Response(200, json={"id": request.url.path.split("/")[-1]})
        if request.url.path == "/v1/databases":
            return httpx.Response(200, json={"id": "db-1"})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_mod, "httpx",
        SimpleNamespace(AsyncClient=lambda **kw: real(transport=transport, **kw)),
    )
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    return state


def _job(source_id="1") -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title="Senior Backend Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. "
                          "Salary: $180,000 - $220,000. "
                          "Same pay regardless of location."),
        apply_url="https://example.com/1",
    ).finalize()


async def _seed(storage: Storage, source_id="1", **overrides) -> int:
    job = _job(source_id)
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(overrides)
    return result["id"]


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(NotionError, match="NOTION_TOKEN"):
        NotionClient()


@pytest.mark.asyncio
async def test_init_database_wire_format(notion_http):
    db_id = await init_database(NotionClient(), "parent-page")
    assert db_id == "db-1"
    req = notion_http["requests"][0]
    assert req.headers["authorization"] == "Bearer ntn_test"
    assert req.headers["notion-version"]
    payload = json.loads(req.content)
    assert payload["parent"] == {"type": "page_id", "page_id": "parent-page"}
    assert "Status" in payload["properties"]
    assert "Application" in payload["properties"]


def test_page_properties_shape():
    props = _page_properties({
        "id": 7, "company_display": "GitLab", "title": "Senior BE",
        "application_status": "applied", "user_verdict": "apply",
        "fit_score": 88, "applied_at": NOW, "warm_path_used": True,
        "draft_keyword_coverage": 0.91, "apply_url": "https://x.example",
    })
    assert props["Application"]["title"][0]["text"]["content"] == \
        "GitLab — Senior BE"
    assert props["Status"] == {"select": {"name": "applied"}}
    assert props["Applied"] == {"date": {"start": "2026-08-27"}}
    assert props["Warm path"] == {"checkbox": True}
    assert props["Keyword coverage"] == {"number": 0.91}
    assert props["Job ID"] == {"number": 7}
    # Sparse rows only carry what they have.
    sparse = _page_properties({"id": 1, "company_display": "X", "title": "T"})
    assert "Status" not in sparse and "Applied" not in sparse


@pytest.mark.asyncio
async def test_sync_creates_then_updates(notion_http):
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", user_verdict="apply")
    await _seed(storage, "2")  # untracked: no verdict, no status

    first = await sync_jobs(storage, NotionClient(), "db-1")
    assert first == {"tracked": 1, "created": 1, "updated": 0, "errors": 0}
    assert (await storage.get_job(job_id))["notion_page_id"] == "page-1"

    second = await sync_jobs(storage, NotionClient(), "db-1")
    assert second == {"tracked": 1, "created": 0, "updated": 1, "errors": 0}
    patch = notion_http["requests"][-1]
    assert patch.method == "PATCH"
    assert patch.url.path == "/v1/pages/page-1"


@pytest.mark.asyncio
async def test_sync_recreates_deleted_page(notion_http):
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", user_verdict="apply",
                         notion_page_id="page-gone")
    # Queue: schema GET succeeds, then the page PATCH 404s -> recreate.
    notion_http["responses"] = [
        httpx.Response(200, json={"id": "db-1",
                                  "properties": dict(FULL_SCHEMA)}),
        httpx.Response(404, json={}),
    ]
    result = await sync_jobs(storage, NotionClient(), "db-1")
    assert result["created"] == 1 and result["errors"] == 0
    assert (await storage.get_job(job_id))["notion_page_id"] == "page-1"


@pytest.mark.asyncio
async def test_sync_adopts_foreign_database(notion_http):
    """A hand-made board (different title prop, none of our properties)
    gets adopted: missing properties PATCHed in, its own title prop used."""
    notion_http["db_properties"] = {"Name": {"type": "title", "title": {}}}
    storage = Storage("")
    await storage.connect()
    await _seed(storage, "1", user_verdict="apply")

    result = await sync_jobs(storage, NotionClient(), "db-1")
    assert result["created"] == 1 and result["errors"] == 0
    # Missing properties were added to the database...
    patches = [r for r in notion_http["requests"]
               if r.method == "PATCH" and "/databases/" in r.url.path]
    assert len(patches) == 1
    patched = json.loads(patches[0].content)["properties"]
    assert "Status" in patched and "Job ID" in patched
    assert "Application" not in patched  # never inject a second title prop
    # ...and the page used the board's own title property.
    create = [r for r in notion_http["requests"]
              if r.method == "POST" and r.url.path == "/v1/pages"][0]
    page_props = json.loads(create.content)["properties"]
    assert "Name" in page_props and "Application" not in page_props


@pytest.mark.asyncio
async def test_maybe_sync_job_noops_when_unconfigured(monkeypatch):
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", user_verdict="apply")
    assert await maybe_sync_job(storage, job_id) is False


@pytest.mark.asyncio
async def test_maybe_sync_job_pushes_when_configured(notion_http, monkeypatch):
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-1")
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", user_verdict="apply")
    assert await maybe_sync_job(storage, job_id) is True
    assert (await storage.get_job(job_id))["notion_page_id"] == "page-1"
    # Untracked jobs are never pushed.
    other = await _seed(storage, "2")
    assert await maybe_sync_job(storage, other) is False


@pytest.mark.asyncio
async def test_maybe_sync_job_never_raises(monkeypatch):
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-1")
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")

    def boom(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(client_mod, "httpx", SimpleNamespace(AsyncClient=boom))
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage, "1", user_verdict="apply")
    assert await maybe_sync_job(storage, job_id) is False  # logged, not raised
