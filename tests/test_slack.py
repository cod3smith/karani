"""Slack bridge: client wire format, block rendering, command dispatch,
and listener event filtering. No network, no slack-sdk required."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import slackbridge.client as client_mod
from ingestion.filters import pre_filter
from ingestion.models import Job, RemoteStatus, Source
from ingestion.profile import DEFAULT_PROFILE
from ingestion.resume import ResumeProfile
from ingestion.storage import Storage
from memory import MemoryManager
from slackbridge.blocks import actions_blocks, digest_blocks
from slackbridge.client import SlackClient, SlackError
from slackbridge.commands import handle_command
from slackbridge.listener import handle_event


# --- client ---

@pytest.fixture
def slack_http(monkeypatch):
    """Route SlackClient through a MockTransport; capture requests."""
    state = {"requests": [], "responses": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if state["responses"]:
            return state["responses"].pop(0)
        return httpx.Response(200, json={"ok": True, "ts": "1.0"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_mod, "httpx",
        SimpleNamespace(AsyncClient=lambda **kw: real(transport=transport, **kw)),
    )
    return state


@pytest.mark.asyncio
async def test_post_message_wire_format(slack_http):
    client = SlackClient(bot_token="xoxb-test")
    await client.post_message("C123", "hello", blocks=[{"type": "divider"}])
    req = slack_http["requests"][0]
    assert str(req.url).endswith("/chat.postMessage")
    assert req.headers["authorization"] == "Bearer xoxb-test"
    payload = json.loads(req.content)
    assert payload == {"channel": "C123", "text": "hello",
                       "blocks": [{"type": "divider"}]}


@pytest.mark.asyncio
async def test_client_retries_429_once(slack_http, monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(client_mod.asyncio, "sleep", no_sleep)
    slack_http["responses"] = [
        httpx.Response(429, headers={"Retry-After": "1"}, json={"ok": False}),
        httpx.Response(200, json={"ok": True}),
    ]
    client = SlackClient(bot_token="xoxb-test")
    result = await client.post_message("C123", "hi")
    assert result["ok"] is True
    assert len(slack_http["requests"]) == 2


@pytest.mark.asyncio
async def test_client_raises_on_slack_error(slack_http):
    slack_http["responses"] = [
        httpx.Response(200, json={"ok": False, "error": "channel_not_found"}),
    ]
    with pytest.raises(SlackError, match="channel_not_found"):
        await SlackClient(bot_token="xoxb-test").post_message("C0", "hi")


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(SlackError, match="SLACK_BOT_TOKEN"):
        SlackClient()


# --- blocks ---

def test_digest_blocks_render():
    rows = [{"id": 7, "company_display": "GitLab", "title": "Senior BE",
             "fit_score": 88, "comp_min_usd": 180_000,
             "comp_max_usd": 220_000, "apply_url": "https://x.example"}]
    blocks = digest_blocks(rows)
    text = json.dumps(blocks)
    assert "GitLab" in text and "verdict 7 apply" in text
    assert digest_blocks([])[0]["text"]["text"].startswith("No qualified")


def test_actions_blocks_flag_fast_lane():
    buckets = {"review": [{"id": 1, "company": "GitLab", "title": "BE",
                           "fit_score": 90, "posted_days_ago": 1,
                           "fast_lane": True}],
               "to_draft": [], "to_submit": [], "follow_up": []}
    text = json.dumps(actions_blocks(buckets))
    assert "FAST LANE" in text


# --- command dispatch ---

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


@pytest.fixture
async def env():
    storage = Storage("")
    await storage.connect()
    job = _job()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    row = await storage.get_job(result["id"])
    row.update(verdict="qualified", fit_score=88)
    return storage, MemoryManager(storage, mode="basic"), result["id"]


async def _cmd(env, text, **kw):
    storage, memory, _ = env
    return await handle_command(text, storage=storage, memory=memory, **kw)


@pytest.mark.asyncio
async def test_help_and_unknown(env):
    assert "karani commands" in await _cmd(env, "help")
    assert "Unknown command" in await _cmd(env, "frobnicate")
    assert "karani commands" in await _cmd(env, "")


@pytest.mark.asyncio
async def test_actions_and_job_detail(env):
    _, _, job_id = env
    out = await _cmd(env, "actions")
    assert "GitLab" in out and str(job_id) in out
    detail = await _cmd(env, f"job {job_id}")
    assert "Senior Backend Engineer" in detail
    assert "No job with id=999" in await _cmd(env, "job 999")


@pytest.mark.asyncio
async def test_verdict_writes_state_and_memory(env):
    storage, memory, job_id = env
    out = await _cmd(env, f"verdict {job_id} apply")
    assert "apply" in out
    assert (await storage.get_job(job_id))["user_verdict"] == "apply"
    recalled = await memory.recall("GitLab Senior Backend Engineer")
    assert any("verdict 'apply'" in r["content"] for r in recalled)


@pytest.mark.asyncio
async def test_status_stage_outcome_flow(env):
    storage, _, job_id = env
    await _cmd(env, f"status {job_id} applied")
    await _cmd(env, f"stage {job_id} recruiter_screen intro call")
    await _cmd(env, f"outcome {job_id} offer")
    row = await storage.get_job(job_id)
    assert row["application_status"] == "applied"
    assert row["stages"][0]["stage"] == "recruiter_screen"
    assert row["outcome"] == "offer"


@pytest.mark.asyncio
async def test_qualify_command_uses_seams(env):
    class FakeLLM:
        model_name = "fake"

        async def complete(self, system, user):
            return json.dumps({
                "fit_score": 90, "verdict": "qualified", "strengths": [],
                "gaps": [], "red_flags": [], "why_apply": "y",
                "why_skip": "", "recommended_positioning": "p",
            })

    out = await _cmd(
        env, "qualify 5",
        make_qualifier=lambda provider=None, model=None: FakeLLM(),
        load_resume=lambda path=None: ResumeProfile(raw_markdown="# K"),
    )
    assert "1 qualified" in out


@pytest.mark.asyncio
async def test_memory_commands(env):
    assert "Stored" in await _cmd(env, "remember GitLab responds fast")
    assert "GitLab responds fast" in await _cmd(env, "recall GitLab response")


@pytest.mark.asyncio
async def test_command_errors_never_raise(env):
    # Non-numeric id and internal errors come back as messages, not raises.
    out = await _cmd(env, "verdict abc apply")
    assert "failed" in out.lower() or "expected a numeric" in out.lower()


# --- listener event handling ---

class StubSlack:
    def __init__(self):
        self.posts = []

    async def post_message(self, channel, text, blocks=None, thread_ts=None):
        self.posts.append({"channel": channel, "text": text,
                           "thread_ts": thread_ts})
        return {"ok": True}


@pytest.mark.asyncio
async def test_handle_event_replies_in_channel(env):
    storage, memory, _ = env
    slack = StubSlack()
    reply = await handle_event(
        {"type": "message", "text": "help", "channel": "D1"},
        storage=storage, memory=memory, slack=slack,
    )
    assert "karani commands" in reply
    assert slack.posts[0]["channel"] == "D1"


@pytest.mark.asyncio
async def test_handle_event_ignores_bots_and_other_channels(env):
    storage, memory, _ = env
    slack = StubSlack()
    ignored = [
        {"type": "message", "text": "help", "channel": "D1",
         "bot_id": "B1"},                                   # our own replies
        {"type": "message", "text": "help", "channel": "D9"},  # wrong channel
        {"type": "message", "text": "help", "channel": "D1",
         "subtype": "message_changed"},                     # edits
        {"type": "reaction_added", "channel": "D1"},        # non-messages
    ]
    for event in ignored:
        result = await handle_event(event, storage=storage, memory=memory,
                                    slack=slack, channel_filter="D1")
        assert result is None
    assert slack.posts == []
