"""Agent loop: tool execution, iteration cap, tool-call cap."""
from __future__ import annotations

import json

import pytest

from karani.qualification.agent import qualify_one_agent
from karani.qualification.tools import Tool, ToolRegistry


class ScriptedAgentClient:
    """Emits the given sequence of chat_turn responses."""
    model_name = "test"
    def __init__(self, turns):
        self.turns = list(turns)
        self.i = 0
    async def chat_turn(self, messages, tools=None):
        resp = self.turns[self.i]
        self.i += 1
        return resp


def _tool_call_turn(name, args):
    return {
        "content": "",
        "tool_calls": [{"id": "c1", "name": name, "arguments": json.dumps(args)}],
        "raw_tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}],
        "finish_reason": "tool_calls", "usage": {},
    }


def _final_turn(payload):
    return {
        "content": json.dumps(payload), "tool_calls": [],
        "raw_tool_calls": [], "finish_reason": "stop", "usage": {},
    }


@pytest.fixture
def registry():
    async def fake(**kw): return "OK"
    return ToolRegistry([
        Tool(name="web_search", description="s",
             parameters={"type": "object",
                          "properties": {"query": {"type": "string"}},
                          "required": ["query"]},
             fn=fake),
    ])


@pytest.mark.asyncio
async def test_agent_loop_calls_tool_then_returns_json(registry):
    client = ScriptedAgentClient([
        _tool_call_turn("web_search", {"query": "GitLab"}),
        _final_turn({
            "fit_score": 90, "verdict": "qualified",
            "strengths": [], "gaps": [], "red_flags": [],
            "why_apply": "Yes.", "why_skip": "",
            "recommended_positioning": "Lead with X.",
        }),
    ])
    r = await qualify_one_agent(client, resume="R", resume_hash="h", hints=[],
                                 job_row={"id": 1, "company_display": "GL",
                                          "title": "SWE",
                                          "description_text": ""},
                                 registry=registry)
    assert r.verdict == "qualified"
    assert r.fit_score == 90
    assert r.tool_calls_made == 1
    assert r.agent_iterations == 2
    assert r.evidence_gathered
    assert "web_search" in r.evidence_gathered[0]


@pytest.mark.asyncio
async def test_agent_cap_respected(registry):
    # 5 tool_call turns, cap = 2 → loop nudges to final, and only 2 tool calls fire.
    client = ScriptedAgentClient([
        _tool_call_turn("web_search", {"query": f"q{i}"}) for i in range(5)
    ] + [
        _final_turn({
            "fit_score": 50, "verdict": "maybe",
            "strengths": [], "gaps": [], "red_flags": [],
            "why_apply": "", "why_skip": "",
            "recommended_positioning": "",
        }),
    ])
    r = await qualify_one_agent(
        client, resume="R", resume_hash="h", hints=[],
        job_row={"id": 2, "title": "SWE", "description_text": ""},
        registry=registry, max_iterations=8, max_tool_calls=2,
    )
    assert r.tool_calls_made <= 2
    assert r.verdict == "maybe"


@pytest.mark.asyncio
async def test_agent_exhausted_iterations_returns_maybe(registry):
    # Only tool_call turns, no final. Should end as `maybe` with why_skip.
    client = ScriptedAgentClient([
        _tool_call_turn("web_search", {"query": "x"}) for _ in range(20)
    ])
    r = await qualify_one_agent(
        client, resume="R", resume_hash="h", hints=[],
        job_row={"id": 3, "title": "SWE", "description_text": ""},
        registry=registry, max_iterations=3, max_tool_calls=10,
    )
    assert r.verdict == "maybe"
    assert "exhausted" in r.why_skip.lower()
