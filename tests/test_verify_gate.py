"""Agent verify-before-draft: the graph's first conditional branch.

Single-turn qualification reads only the JD. With `[autopilot] verify`
on, an agent double-checks each candidate's geo/visa/comp claims before
the pack budget (up to three billed calls) is spent (ADR 0016).
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("langgraph")

from karani.config import reload_config  # noqa: E402
from karani.ingestion.filters import pre_filter  # noqa: E402
from karani.ingestion.models import Job, RemoteStatus, Source  # noqa: E402
from karani.ingestion.profile import DEFAULT_PROFILE  # noqa: E402
from karani.ingestion.resume import ResumeProfile  # noqa: E402
from karani.ingestion.storage import Storage  # noqa: E402
from karani.orchestration.graph import HuntDeps, build_hunt_graph  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    reload_config()


@pytest.fixture(autouse=True)
def _fake_upstream_nodes(monkeypatch):
    """Stub ingest and qualify: these tests exercise the verify ->
    autopilot path only, and the real nodes would hit the network (the
    no-network rule) and churn the seeded candidate."""
    import karani.ingestion.orchestrator as ing
    import karani.qualification as qual

    class FakeIngest:
        fetched = inserted = passed_prefilter = 0
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


class AgentLLM:
    """chat_turn-capable fake: returns the final verdict immediately."""
    model_name = "fake-agent"

    def __init__(self, verdict: str = "qualified", fit: int = 90):
        self.payload = json.dumps({
            "fit_score": fit, "verdict": verdict, "strengths": [],
            "gaps": [], "red_flags": [], "why_apply": "y", "why_skip": "",
            "recommended_positioning": "p",
        })
        self.calls = 0

    async def chat_turn(self, messages, tools=None):
        self.calls += 1
        return {"content": self.payload, "reasoning": "", "tool_calls": [],
                "raw_tool_calls": [], "finish_reason": "stop", "usage": {}}

    async def complete(self, system, user):
        self.calls += 1
        return self.payload


class DraftLLM:
    model_name = "fake-draft"

    async def complete(self, system, user):
        return json.dumps({
            "cover_letter": "Dear team, Python and Kafka work.",
            "tone_note": "", "tailored_bullets": [],
            "application_answers": [], "subject_line": "",
            "positioning_summary": "",
        })


class StubSlack:
    posts: list = []

    async def post_message(self, channel, text, blocks=None, **kw):
        StubSlack.posts.append(text)
        return {"ok": True}


async def _seed(storage: Storage, source_id: str = "1", fit: int = 90) -> int:
    job = Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title=f"Senior Backend Engineer {source_id}",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. "
                          "Salary: $180,000 - $220,000. "
                          "Same pay regardless of location."),
        apply_url=f"https://example.com/{source_id}",
    ).finalize()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(verdict="qualified",
                                                 fit_score=fit)
    return result["id"]


def _deps(storage, agent) -> HuntDeps:
    StubSlack.posts = []
    return HuntDeps(
        storage=storage,
        make_qualifier=lambda: DraftLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# Kelyn\nPython."),
        slack_factory=StubSlack, channel="D1",
        make_agent_qualifier=lambda: agent,
    )


def _enable_verify(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "karani.toml"
    cfg.write_text("version = 1\n[autopilot]\nverify = true\nmin_fit = 85\n")
    reload_config(cfg)


@pytest.mark.asyncio
async def test_refuted_candidate_is_never_drafted(tmp_path, monkeypatch):
    _enable_verify(tmp_path, monkeypatch)
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage)
    agent = AgentLLM(verdict="skip", fit=40)      # agent refutes the claim

    state = await build_hunt_graph(_deps(storage, agent)).ainvoke({})

    assert agent.calls == 1
    assert state["verified"] == {job_id: False}
    assert state["autopilot"]["drafted"] == 0
    # Budget untouched: the job never entered the state machine.
    assert (await storage.get_job(job_id)).get("application_status") is None
    assert StubSlack.posts == []


@pytest.mark.asyncio
async def test_confirmed_candidate_proceeds_to_draft(tmp_path, monkeypatch):
    _enable_verify(tmp_path, monkeypatch)
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage)
    agent = AgentLLM(verdict="qualified", fit=92)

    state = await build_hunt_graph(_deps(storage, agent)).ainvoke({})

    assert state["verified"] == {job_id: True}
    assert state["autopilot"]["drafted"] == 1
    assert (await storage.get_job(job_id))["application_status"] == "drafting"


@pytest.mark.asyncio
async def test_verify_disabled_by_default_skips_the_node(tmp_path,
                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)          # no karani.toml -> defaults
    reload_config()
    storage = Storage("")
    await storage.connect()
    await _seed(storage)
    agent = AgentLLM()

    state = await build_hunt_graph(_deps(storage, agent)).ainvoke({})

    assert agent.calls == 0              # no billed agent call
    assert "verified" not in state
    assert state["autopilot"]["drafted"] == 1


@pytest.mark.asyncio
async def test_verifier_failure_allows_candidate_through(tmp_path,
                                                         monkeypatch):
    """A broken verifier must degrade to today's behavior, never block
    the hunt outright."""
    _enable_verify(tmp_path, monkeypatch)
    storage = Storage("")
    await storage.connect()
    job_id = await _seed(storage)

    class BrokenAgent:
        model_name = "broken"
        calls = 0

        async def chat_turn(self, messages, tools=None):
            raise RuntimeError("provider down")

        async def complete(self, system, user):
            raise RuntimeError("provider down")

    state = await build_hunt_graph(_deps(storage, BrokenAgent())).ainvoke({})
    assert state["verified"] == {job_id: True}
    assert state["autopilot"]["drafted"] == 1


@pytest.mark.asyncio
async def test_allowed_ids_filters_candidates_in_the_runner(tmp_path,
                                                            monkeypatch):
    from karani.autopilot.runner import run_autopilot

    monkeypatch.chdir(tmp_path)
    storage = Storage("")
    await storage.connect()
    allowed = await _seed(storage, "1")
    blocked = await _seed(storage, "2")

    stats = await run_autopilot(
        storage, slack=StubSlack(), channel="D1",
        make_qualifier=lambda: DraftLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        allowed_ids={allowed},
    )
    assert stats.drafted == 1
    assert (await storage.get_job(allowed))["application_status"] == "drafting"
    assert (await storage.get_job(blocked)).get("application_status") is None
