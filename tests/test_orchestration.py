"""The LangGraph hunt graph: wiring, error containment, retry, alerting.

All pipeline functions are faked via monkeypatch — the graph orchestrates,
so the tests verify orchestration semantics, not the runners (which have
their own suites).
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from karani.ingestion.resume import ResumeProfile  # noqa: E402
from karani.ingestion.storage import Storage  # noqa: E402
from karani.orchestration.graph import HuntDeps, build_hunt_graph  # noqa: E402


class StubSlack:
    posts: list[dict] = []

    async def post_message(self, channel, text, blocks=None, **kw):
        StubSlack.posts.append({"channel": channel, "text": text})
        return {"ok": True}


def _deps(storage, **overrides) -> HuntDeps:
    defaults = dict(
        storage=storage,
        make_qualifier=lambda: None,
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        slack_factory=StubSlack,
        channel="D1",
    )
    defaults.update(overrides)
    return HuntDeps(**defaults)


@pytest.fixture(autouse=True)
def _fake_pipeline(monkeypatch):
    """Fake every runner the nodes wrap; individual tests override."""
    StubSlack.posts = []

    class FakeIngestStats:
        fetched, inserted, passed_prefilter = 10, 4, 2
        per_source: dict = {}

    async def fake_ingest(storage, profile=None):
        return FakeIngestStats()

    class FakeQualifyStats:
        fetched, qualified, maybe, skipped = 2, 1, 1, 0
        errors: list = []

    async def fake_qualify(storage, client, resume, **kw):
        return FakeQualifyStats()

    class FakeAutopilotStats:
        candidates, drafted, delivered, budget_left = 1, 1, 1, 4
        errors: list = []

    async def fake_autopilot(storage, **kw):
        return FakeAutopilotStats()

    import karani.ingestion.orchestrator as ingestion_orchestrator
    import karani.qualification as qualification
    import karani.autopilot as autopilot_pkg
    monkeypatch.setattr(ingestion_orchestrator, "run", fake_ingest)
    monkeypatch.setattr(qualification, "qualify_pending", fake_qualify)
    monkeypatch.setattr(autopilot_pkg, "run_autopilot", fake_autopilot)


@pytest.mark.asyncio
async def test_happy_path_runs_all_nodes():
    storage = Storage("")
    await storage.connect()
    graph = build_hunt_graph(_deps(storage))
    state = await graph.ainvoke({})
    assert state["ingest"]["fetched"] == 10
    assert state["qualify"]["qualified"] == 1
    assert state["autopilot"]["delivered"] == 1
    assert state["notion"] == {"skipped": "notion not configured"}
    assert state.get("errors", []) == []
    assert state["alerted"] is False
    assert StubSlack.posts == []  # no errors -> no alert


@pytest.mark.asyncio
async def test_node_failure_is_contained_and_alerted(monkeypatch):
    async def broken_qualify(storage, client, resume, **kw):
        raise RuntimeError("provider down")

    import karani.qualification as qualification
    monkeypatch.setattr(qualification, "qualify_pending", broken_qualify)

    storage = Storage("")
    await storage.connect()
    state = await build_hunt_graph(_deps(storage)).ainvoke({})
    # qualify failed after retry...
    assert any("qualify" in e and "provider down" in e
               for e in state["errors"])
    # ...but the rest of the pass still ran,
    assert state["ingest"]["fetched"] == 10
    assert state["autopilot"]["delivered"] == 1
    # and the failure was alerted to Slack.
    assert state["alerted"] is True
    assert "provider down" in StubSlack.posts[0]["text"]


@pytest.mark.asyncio
async def test_node_retries_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class Stats:
        fetched, qualified, maybe, skipped = 1, 1, 0, 0
        errors: list = []

    async def flaky_qualify(storage, client, resume, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return Stats()

    import karani.qualification as qualification
    monkeypatch.setattr(qualification, "qualify_pending", flaky_qualify)

    storage = Storage("")
    await storage.connect()
    state = await build_hunt_graph(_deps(storage)).ainvoke({})
    assert calls["n"] == 2
    assert state["qualify"]["qualified"] == 1
    assert state.get("errors", []) == []


@pytest.mark.asyncio
async def test_autopilot_skipped_without_slack():
    storage = Storage("")
    await storage.connect()
    graph = build_hunt_graph(_deps(storage, slack_factory=None, channel=""))
    state = await graph.ainvoke({})
    assert state["autopilot"] == {"skipped": "slack not configured"}
    assert state.get("errors", []) == []


def test_graph_shape():
    graph = build_hunt_graph(_deps(None))
    mermaid = graph.get_graph().draw_mermaid()
    for node in ("ingest", "qualify", "autopilot", "notion", "report"):
        assert node in mermaid
