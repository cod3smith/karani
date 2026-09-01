"""Run ledger, token usage, and heartbeat staleness (roadmap 0.4).

A scheduler that silently stops is invisible otherwise: the in-pass
error alert cannot fire for a pass that never runs. Every pass writes a
ledger row; the twice-daily push alerts when the newest row goes stale.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.ingestion.storage import Storage
from karani.qualification import usage

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


# --- token counters ---

def test_usage_counters_accumulate_as_deltas():
    before = usage.snapshot()
    usage.record({"prompt_tokens": 100, "completion_tokens": 40})
    usage.record({"prompt_tokens": 10})        # partial payload tolerated
    usage.record(None)                         # missing payload still a call
    after = usage.snapshot()
    d = usage.delta(before, after)
    assert d == {"prompt_tokens": 110, "completion_tokens": 40, "calls": 3}


def test_openrouter_records_usage_on_response(monkeypatch):
    """The provider must feed the counters — otherwise every ledger row
    reports zero cost."""
    from types import SimpleNamespace

    import httpx

    import karani.qualification.openai_compat as compat_mod
    import karani.qualification.openrouter as mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        })

    real = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(compat_mod, "httpx", SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=transport, **kw),
        TransportError=httpx.TransportError,
        TimeoutException=httpx.TimeoutException))

    import asyncio

    before = usage.snapshot()
    client = mod.OpenRouterQualifier(model="m", api_key="k")
    asyncio.run(client.complete("s", "u"))
    d = usage.delta(before, usage.snapshot())
    assert d["prompt_tokens"] == 7 and d["completion_tokens"] == 3


# --- ledger ---

@pytest.mark.asyncio
async def test_record_run_and_last_run_at():
    storage = Storage("")
    await storage.connect()
    assert await storage.last_run_at("hourly") is None

    await storage.record_run(
        "hourly", started_at=NOW - timedelta(minutes=2), finished_at=NOW,
        state={"qualify": {"fetched": 3}}, tokens={"calls": 3}, errors=0)
    assert await storage.last_run_at("hourly") == NOW
    assert await storage.last_run_at("daily") is None      # kinds isolated

    latest = NOW + timedelta(hours=1)
    await storage.record_run("hourly", started_at=NOW, finished_at=latest,
                             state={}, tokens={}, errors=1)
    assert await storage.last_run_at("hourly") == latest
    runs = await storage.recent_runs("hourly", limit=5)
    assert len(runs) == 2 and runs[0]["errors"] == 1


# --- heartbeat ---

@pytest.mark.asyncio
async def test_heartbeat_alerts_when_never_run():
    from karani.orchestration.graph import heartbeat_alert

    storage = Storage("")
    await storage.connect()
    msg = await heartbeat_alert(storage, now=NOW)
    assert msg is not None and "no hourly pass" in msg


@pytest.mark.asyncio
async def test_heartbeat_silent_when_fresh_alerts_when_stale():
    from karani.orchestration.graph import heartbeat_alert

    storage = Storage("")
    await storage.connect()
    await storage.record_run("hourly", started_at=NOW, finished_at=NOW,
                             state={}, tokens={}, errors=0)

    assert await heartbeat_alert(storage, now=NOW + timedelta(hours=1)) is None
    stale = await heartbeat_alert(storage, now=NOW + timedelta(hours=4))
    assert stale is not None
    assert "4.0h ago" in stale and "may be dead" in stale


@pytest.mark.asyncio
async def test_heartbeat_threshold_is_configurable(monkeypatch):
    from karani.orchestration.graph import heartbeat_alert

    monkeypatch.setenv("KARANI_HEARTBEAT_MAX_AGE_H", "12")
    storage = Storage("")
    await storage.connect()
    await storage.record_run("hourly", started_at=NOW, finished_at=NOW,
                             state={}, tokens={}, errors=0)
    assert await heartbeat_alert(storage, now=NOW + timedelta(hours=6)) is None
    assert await heartbeat_alert(storage,
                                 now=NOW + timedelta(hours=13)) is not None


# --- graph integration ---

@pytest.mark.asyncio
async def test_hunt_pass_writes_a_ledger_row(monkeypatch, tmp_path):
    """Every scheduler pass must leave a ledger row — that row IS the
    heartbeat, so a missing write means silent-death detection fails."""
    pytest.importorskip("langgraph")
    monkeypatch.chdir(tmp_path)

    import karani.ingestion.orchestrator as ing
    import karani.ingestion.resume as resume_mod
    import karani.orchestration.graph as graph_mod
    import karani.qualification as qual

    captured: dict = {}
    original = Storage.record_run

    async def spy(self, kind, **kw):
        captured.update({"kind": kind, **kw})
        await original(self, kind, **kw)

    # run_hunt_once imports Storage/ResumeProfile locally, so patch the
    # class and classmethod at their source rather than the graph module.
    monkeypatch.setattr(Storage, "record_run", spy)
    monkeypatch.setattr(
        resume_mod.ResumeProfile, "from_file",
        classmethod(lambda cls, path=None: cls(raw_markdown="# K")))

    class FakeIngest:
        fetched, inserted, passed_prefilter = 4, 2, 1
        per_source: dict = {}

    async def fake_ingest(storage, profile=None):
        return FakeIngest()

    class FakeQualify:
        fetched = qualified = maybe = skipped = 0
        errors: list = []
        lock_skipped = False

    async def fake_qualify(storage, client, resume, **kw):
        return FakeQualify()

    monkeypatch.setattr(ing, "run", fake_ingest)
    monkeypatch.setattr(qual, "qualify_pending", fake_qualify)

    state = await graph_mod.run_hunt_once()
    assert state.get("run_recorded") is True
    assert captured["kind"] == "hourly"
    assert captured["state"]["ingest"]["fetched"] == 4
    assert "calls" in captured["tokens"]          # token delta attached
    assert captured["errors"] == 0
    assert captured["finished_at"] >= captured["started_at"]


@pytest.mark.asyncio
async def test_ledger_failure_never_sinks_a_pass(monkeypatch, tmp_path):
    """Bookkeeping is best-effort: a ledger write blowing up must not
    fail the hunt that already did its work."""
    pytest.importorskip("langgraph")
    monkeypatch.chdir(tmp_path)

    import karani.ingestion.orchestrator as ing
    import karani.ingestion.resume as resume_mod
    import karani.orchestration.graph as graph_mod
    import karani.qualification as qual

    async def boom(self, kind, **kw):
        raise RuntimeError("ledger table is gone")

    monkeypatch.setattr(Storage, "record_run", boom)
    monkeypatch.setattr(
        resume_mod.ResumeProfile, "from_file",
        classmethod(lambda cls, path=None: cls(raw_markdown="# K")))

    class FakeIngest:
        fetched, inserted, passed_prefilter = 1, 1, 1
        per_source: dict = {}

    async def fake_ingest(storage, profile=None):
        return FakeIngest()

    class FakeQualify:
        fetched = qualified = maybe = skipped = 0
        errors: list = []
        lock_skipped = False

    async def fake_qualify(storage, client, resume, **kw):
        return FakeQualify()

    monkeypatch.setattr(ing, "run", fake_ingest)
    monkeypatch.setattr(qual, "qualify_pending", fake_qualify)

    state = await graph_mod.run_hunt_once()      # must not raise
    assert state["ingest"]["fetched"] == 1
    assert state.get("run_recorded") is not True
