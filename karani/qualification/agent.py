"""Agent loop: model → tool_calls → execute → append → repeat → final JSON.

Only OpenRouter is wired for tool-use today. The loop takes any object that
exposes `chat_turn(messages, tools) -> {content, tool_calls, finish_reason}`
(matches `OpenRouterQualifier.chat_turn`), so a fake can be swapped in for
tests without extra infrastructure.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from .client import _extract_json
from .models import QualificationResult
from .prompts import AGENT_PROMPT_VERSION, AGENT_SYSTEM_PROMPT, build_user_prompt
from .tools import ToolRegistry, default_registry

log = logging.getLogger(__name__)


class AgentCapableClient(Protocol):
    model_name: str

    async def chat_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


@dataclass
class AgentRunLog:
    tool_calls: list[str] = field(default_factory=list)
    iterations: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


async def qualify_one_agent(
    client: AgentCapableClient,
    *,
    resume: str,
    resume_hash: str,
    hints: list[str],
    job_row: dict,
    past_verdicts: list[dict] | None = None,
    memories: list[str] | None = None,
    registry: ToolRegistry | None = None,
    max_iterations: int = 6,
    max_tool_calls: int = 10,
) -> QualificationResult:
    """Agent-mode qualification. Returns a QualificationResult with evidence trail."""
    registry = registry or default_registry()
    user = build_user_prompt(
        resume=resume, hints=hints, job_row=job_row,
        past_verdicts=past_verdicts, memories=memories,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    run = AgentRunLog()
    final_content = ""

    for _ in range(max_iterations):
        run.iterations += 1
        resp = await client.chat_turn(messages, tools=registry.schemas())
        usage = resp.get("usage") or {}
        run.total_prompt_tokens += usage.get("prompt_tokens", 0) or 0
        run.total_completion_tokens += usage.get("completion_tokens", 0) or 0

        tool_calls = resp.get("tool_calls") or []
        content = resp.get("content") or ""

        # If the model both spoke and requested tools, prefer tool calls this turn.
        if tool_calls:
            # Cap: refuse further tool calls once we've hit max_tool_calls.
            if len(run.tool_calls) >= max_tool_calls:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have used all your tool budget. Return the FINAL "
                        "JSON verdict now, based on evidence gathered so far."
                    ),
                })
                continue

            # Echo the assistant message verbatim (OpenAI convention requires
            # the same tool_calls block back in the transcript).
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": resp.get("raw_tool_calls") or [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                result = await registry.execute(tc["name"], tc["arguments"])
                run.tool_calls.append(
                    f"{tc['name']}({_short_args(tc['arguments'])}) → "
                    f"{_short_result(result)}"
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            continue

        # No tool calls → this is (should be) the final answer.
        if content:
            final_content = content
            break
        # No content and no tools — nudge and retry once.
        messages.append({
            "role": "user",
            "content": "Return the final JSON verdict now.",
        })

    if not final_content:
        # Ran out of iterations without a final answer.
        return QualificationResult(
            fit_score=0,
            verdict="maybe",
            why_apply="",
            why_skip=(
                f"Agent loop exhausted after {run.iterations} iterations "
                f"without a final verdict."
            ),
            recommended_positioning="",
            model=getattr(client, "model_name", "unknown"),
            prompt_version=AGENT_PROMPT_VERSION,
            resume_hash=resume_hash,
            evidence_gathered=run.tool_calls,
            tool_calls_made=len(run.tool_calls),
            agent_iterations=run.iterations,
        )

    try:
        data = _extract_json(final_content)
        result = QualificationResult.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("agent returned malformed JSON: %s -- raw: %r",
                    e, final_content[:400])
        result = QualificationResult(
            fit_score=0,
            verdict="maybe",
            why_apply="",
            why_skip="Agent returned malformed JSON; needs manual review.",
            recommended_positioning="",
        )
    result.model = getattr(client, "model_name", "unknown")
    result.prompt_version = AGENT_PROMPT_VERSION
    result.resume_hash = resume_hash
    result.evidence_gathered = run.tool_calls
    result.tool_calls_made = len(run.tool_calls)
    result.agent_iterations = run.iterations
    return result


def _short_args(arguments_json: str, limit: int = 60) -> str:
    if not arguments_json:
        return ""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        args = arguments_json
    s = str(args)
    return s if len(s) <= limit else s[:limit] + "…"


def _short_result(result: str, limit: int = 80) -> str:
    r = result.replace("\n", " ").strip()
    return r if len(r) <= limit else r[:limit] + "…"
