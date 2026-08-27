"""MCP server tests — every tool exercised against in-memory storage.

Tools are invoked through `server.call_tool`, which runs the full MCP tool
path (argument validation, execution, result conversion) without a transport.
LLM-backed tools get a scripted fake via the `_make_qualifier` seam; the
resume comes from a fake `_load_resume` so tests never read data/resume.md.
"""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import karani.mcp_server.server as srv
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage


QUAL_RESPONSE = json.dumps({
    "fit_score": 90,
    "verdict": "qualified",
    "strengths": [], "gaps": [], "red_flags": [],
    "why_apply": "Strong fit.", "why_skip": "",
    "recommended_positioning": "Lead with Python.",
})

DRAFT_RESPONSE = json.dumps({
    "cover_letter": "Dear team, I would like to apply.",
    "tone_note": "direct",
    "tailored_bullets": [],
    "application_answers": [],
    "subject_line": "Application",
    "positioning_summary": "",
})


class ScriptedLLM:
    model_name = "fake-model"

    def __init__(self, script: str):
        self.script = script

    async def complete(self, system: str, user: str) -> str:
        return self.script


def _sample_job(source_id: str = "1") -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title="Senior Backend Engineer",
        location_raw="Remote",
        remote_status=RemoteStatus.REMOTE,
        description_text=(
            "We hire globally. Python and Go required. "
            "Salary: $180,000 - $220,000. Same pay regardless of location."
        ),
        apply_url="https://example.com/apply/1",
    ).finalize()


@pytest.fixture
async def storage(monkeypatch):
    s = Storage("")
    await s.connect()
    srv.use_storage(s)
    monkeypatch.setattr(
        srv, "_load_resume",
        lambda path: ResumeProfile(raw_markdown="# Kelyn\nPython + ML infra."),
    )
    yield s
    srv.use_storage(None)


async def _seed(storage: Storage) -> int:
    job = _sample_job()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    return result["id"]


async def _call(name: str, args: dict | None = None) -> dict | str:
    result = await srv.app.call_tool(name, args or {})
    assert not result.is_error
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@pytest.mark.asyncio
async def test_tool_listing():
    tools = {t.name for t in await srv.app.list_tools()}
    assert tools == {
        "ingest", "sweep", "discover", "qualify", "digest", "shortlist",
        "get_job", "pipeline_stats", "next_actions", "funnel_stats",
        "draft", "prep", "draft_followup", "company_intel", "warm_paths",
        "notify_slack", "notion_sync", "autopilot", "record_verdict",
        "set_status",
        "add_stage", "record_outcome", "record_question", "remember",
        "recall",
    }


@pytest.mark.asyncio
async def test_full_flow_through_tools(storage, monkeypatch, tmp_path):
    job_id = await _seed(storage)

    monkeypatch.setattr(srv, "_make_qualifier",
                        lambda p, m: ScriptedLLM(QUAL_RESPONSE))
    qstats = await _call("qualify", {"limit": 10})
    assert qstats["fetched"] == 1
    assert qstats["qualified"] == 1

    short = await _call("shortlist", {"limit": 10})
    assert short["count"] == 1
    assert short["jobs"][0]["company"] == "GitLab"
    assert short["jobs"][0]["fit_score"] == 90

    detail = await _call("get_job", {"job_id": job_id,
                                     "include_description": True})
    assert detail["qualification"]["verdict"] == "qualified"
    assert "hire globally" in detail["description_text"]

    md = await _call("digest", {"limit": 5, "format": "md"})
    assert "GitLab" in md

    monkeypatch.setattr(srv, "_make_qualifier",
                        lambda p, m: ScriptedLLM(DRAFT_RESPONSE))
    out = str(tmp_path / "draft.md")
    drafted = await _call("draft", {"job_id": job_id, "output_path": out})
    assert drafted["path"] == out
    assert (tmp_path / "draft.md").exists()
    assert (await storage.get_job(job_id))["application_status"] == "drafting"

    await _call("record_verdict", {"job_id": job_id, "verdict": "apply"})
    assert (await _call("shortlist", {}))["count"] == 0

    await _call("set_status", {"job_id": job_id, "status": "applied"})
    await _call("add_stage", {"job_id": job_id, "stage": "recruiter_screen",
                              "notes": "intro call"})
    await _call("record_outcome", {"job_id": job_id, "outcome": "offer"})

    stats = await _call("pipeline_stats", {})
    assert stats["funnel"] == {"applied": 1}
    assert stats["counts"]["qualified"] == 1

    # Draft provenance must have landed for funnel A/B by prompt version.
    row = await storage.get_job(job_id)
    assert row["draft_model"] == "fake-model"
    funnel = await _call("funnel_stats", {})
    assert funnel["totals"]["applied"] == 1
    assert funnel["totals"]["offers"] == 1
    assert row["draft_prompt_version"] in funnel["by_draft_prompt"]

    swept = await _call("sweep", {"days": 5})
    assert swept["threshold_days"] == 5


@pytest.mark.asyncio
async def test_ingest_tool_reports_run_stats(storage, monkeypatch):
    from karani.ingestion.orchestrator import RunStats, SourceOutcome

    async def fake_run(_storage, profile=None):
        stats = RunStats(fetched=3, inserted=2, updated=1, passed_prefilter=1)
        stats.per_source = {"greenhouse/gitlab": SourceOutcome(fetched=3)}
        stats.dropped_by_reason["region"] = 2
        return stats

    import karani.ingestion.orchestrator
    monkeypatch.setattr(karani.ingestion.orchestrator, "run", fake_run)
    result = await _call("ingest", {})
    assert result["fetched"] == 3
    assert result["per_source"]["greenhouse/gitlab"]["fetched"] == 3
    assert result["dropped_by_reason"] == {"region": 2}


@pytest.mark.asyncio
async def test_remember_and_recall_tools(storage):
    stored = await _call("remember", {
        "content": "GitLab's recruiter replied within three days",
        "kind": "company", "company": "GitLab",
    })
    assert stored["deduped"] is False

    found = await _call("recall", {"query": "GitLab recruiter reply time"})
    assert found["count"] == 1
    assert "three days" in found["memories"][0]["content"]

    with pytest.raises(ToolError, match="kind must be one of"):
        await srv.app.call_tool("remember", {"content": "x", "kind": "vibes"})


@pytest.mark.asyncio
async def test_set_status_warm_path_and_record_question(storage):
    job_id = await _seed(storage)
    await _call("set_status", {"job_id": job_id, "status": "applied",
                               "warm_path": True})
    assert (await storage.get_job(job_id))["warm_path_used"] is True
    funnel = await _call("funnel_stats", {})
    assert funnel["by_warm_path"]["warm"]["applied"] == 1

    banked = await _call("record_question", {
        "job_id": job_id, "question": "Why staff-level now?",
        "stage": "screen",
    })
    assert banked["company"] == "GitLab"
    recalled = await _call("recall", {"query": "GitLab interview staff",
                                      "kind": "question"})
    assert recalled["count"] == 1

    with pytest.raises(ToolError, match="no job with id=999"):
        await srv.app.call_tool("record_question",
                                {"job_id": 999, "question": "x"})


@pytest.mark.asyncio
async def test_record_verdict_writes_memory(storage):
    job_id = await _seed(storage)
    await _call("record_verdict", {"job_id": job_id, "verdict": "apply"})
    found = await _call("recall", {"query": "GitLab Senior Backend Engineer"})
    assert found["count"] == 1
    assert "verdict 'apply'" in found["memories"][0]["content"]


@pytest.fixture
def fake_intel(monkeypatch):
    async def fake_get(storage, company, **kw):
        return {"company_display": company, "cached": True,
                "fetched_at": None,
                "payload": {"wikipedia": f"{company} background.",
                            "github": "repos",
                            "warm_candidates": [
                                {"login": "alice",
                                 "url": "https://github.com/alice",
                                 "source": "github_org_member"}]}}

    import karani.intel.service
    monkeypatch.setattr(karani.intel.service, "get_company_intel", fake_get)
    monkeypatch.setattr("karani.intel.get_company_intel", fake_get)


@pytest.mark.asyncio
async def test_company_intel_and_warm_paths_tools(storage, fake_intel):
    dossier = await _call("company_intel", {"company": "GitLab"})
    assert dossier["cached"] is True
    assert "GitLab background" in dossier["dossier"]
    assert dossier["warm_candidates"][0]["login"] == "alice"

    paths = await _call("warm_paths", {"company": "GitLab"})
    assert paths["count"] == 1
    assert paths["candidates"][0]["login"] == "alice"


@pytest.mark.asyncio
async def test_prep_and_followup_tools(storage, fake_intel, monkeypatch,
                                       tmp_path):
    import os
    monkeypatch.chdir(tmp_path)  # drafts/ lands in tmp
    job_id = await _seed(storage)

    prep_json = json.dumps({
        "company_brief": "brief", "likely_questions": [],
        "questions_to_ask": [{"question": "About the alice repo?",
                              "source_basis": "github"}],
        "warm_openers": [], "positioning_reminder": "",
    })
    monkeypatch.setattr(srv, "_make_qualifier",
                        lambda p, m: ScriptedLLM(prep_json))
    prep = await _call("prep", {"job_id": job_id})
    assert prep["questions_to_ask"] == ["About the alice repo?"]
    assert os.path.exists(prep["path"])

    fu_json = json.dumps({"note": "Saw the release land.",
                          "subject_line": "s", "hook_used": "release"})
    monkeypatch.setattr(srv, "_make_qualifier",
                        lambda p, m: ScriptedLLM(fu_json))
    fu = await _call("draft_followup", {"job_id": job_id})
    assert fu["hook_used"] == "release"
    assert os.path.exists(fu["path"])


@pytest.mark.asyncio
async def test_notify_slack_tool(storage, monkeypatch):
    posts = []

    class StubSlack:
        def __init__(self, *a, **kw):
            pass

        async def post_message(self, channel, text, blocks=None, **kw):
            posts.append({"channel": channel, "text": text, "blocks": blocks})
            return {"ok": True}

    import karani.slackbridge as slackbridge
    monkeypatch.setattr(slackbridge, "SlackClient", StubSlack)
    monkeypatch.setenv("SLACK_CHANNEL", "D42")
    result = await _call("notify_slack", {"kind": "actions"})
    assert result["channel"] == "D42"
    assert posts and posts[0]["blocks"]

    monkeypatch.delenv("SLACK_CHANNEL")
    with pytest.raises(ToolError, match="SLACK_CHANNEL not set"):
        await srv.app.call_tool("notify_slack", {"kind": "digest"})


@pytest.mark.asyncio
async def test_autopilot_tool(storage, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLACK_CHANNEL", "D9")
    posts = []

    class StubSlack:
        def __init__(self, *a, **kw):
            pass

        async def post_message(self, channel, text, blocks=None, **kw):
            posts.append(channel)
            return {"ok": True}

    import karani.slackbridge as slackbridge
    monkeypatch.setattr(slackbridge, "SlackClient", StubSlack)
    monkeypatch.setattr(srv, "_make_qualifier",
                        lambda p, m: ScriptedLLM(DRAFT_RESPONSE))
    job_id = await _seed(storage)
    row = await storage.get_job(job_id)
    row.update(verdict="qualified", fit_score=92)

    result = await _call("autopilot", {})
    assert result["drafted"] == 1 and result["delivered"] == 1
    assert posts == ["D9"]
    assert (await storage.get_job(job_id))["application_status"] == "drafting"

    monkeypatch.delenv("SLACK_CHANNEL")
    with pytest.raises(ToolError, match="SLACK_CHANNEL not set"):
        await srv.app.call_tool("autopilot", {})


@pytest.mark.asyncio
async def test_notion_sync_tool(storage, monkeypatch):
    calls = []

    async def fake_sync(_storage, _client, database_id):
        calls.append(database_id)
        return {"tracked": 2, "created": 1, "updated": 1, "errors": 0}

    import karani.notionsync as notionsync
    monkeypatch.setattr(notionsync, "sync_jobs", fake_sync)
    monkeypatch.setattr(notionsync, "NotionClient", lambda: object())
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-42")
    result = await _call("notion_sync", {})
    assert result == {"tracked": 2, "created": 1, "updated": 1, "errors": 0}
    assert calls == ["db-42"]

    monkeypatch.delenv("NOTION_DATABASE_ID")
    with pytest.raises(ToolError, match="NOTION_DATABASE_ID not set"):
        await srv.app.call_tool("notion_sync", {})


@pytest.mark.asyncio
async def test_next_actions_tool(storage):
    job_id = await _seed(storage)
    row = await storage.get_job(job_id)
    row.update(verdict="qualified", fit_score=88)

    actions = await _call("next_actions", {})
    assert [x["id"] for x in actions["review"]] == [job_id]
    assert actions["to_draft"] == []

    await _call("record_verdict", {"job_id": job_id, "verdict": "apply"})
    actions = await _call("next_actions", {})
    assert actions["review"] == []
    assert [x["id"] for x in actions["to_draft"]] == [job_id]


@pytest.mark.asyncio
async def test_discover_tool_reports_probe_outcomes(storage, monkeypatch):
    async def fake_probe(_storage, limit=10):
        return [
            {"company": "Acme", "ats": "greenhouse", "slug": "acme"},
            {"company": "NoBoardCo", "ats": None, "slug": None},
        ]

    import karani.ingestion.discovery
    monkeypatch.setattr(karani.ingestion.discovery, "probe_unpromoted", fake_probe)
    result = await _call("discover", {"limit": 5})
    assert result["probed"] == 2
    assert result["promoted_now"] == 1
    assert result["outcomes"][0]["slug"] == "acme"
    assert result["total_promoted"] == 0  # in-memory fallback has no table


@pytest.mark.asyncio
async def test_unknown_job_id_surfaces_message(storage):
    with pytest.raises(ToolError, match="no job with id=999"):
        await srv.app.call_tool("get_job", {"job_id": 999})


@pytest.mark.asyncio
async def test_invalid_verdict_surfaces_message(storage):
    with pytest.raises(ToolError, match="user_verdict must be one of"):
        await srv.app.call_tool(
            "record_verdict", {"job_id": 1, "verdict": "meh"}
        )


@pytest.mark.asyncio
async def test_invalid_digest_format_rejected(storage):
    with pytest.raises(ToolError, match="format must be one of"):
        await srv.app.call_tool("digest", {"format": "pdf"})


@pytest.mark.asyncio
async def test_in_memory_state_persists_across_tool_calls(storage):
    """The storage singleton must carry state between calls — the in-memory
    fallback would lose everything if each tool reconnected."""
    job_id = await _seed(storage)
    await _call("set_status", {"job_id": job_id, "status": "ready"})
    detail = await _call("get_job", {"job_id": job_id})
    assert detail["application_status"] == "ready"
