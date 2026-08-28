"""OpenRouter-backed qualifier — default provider.

Uses the OpenAI-compatible Chat Completions endpoint at
https://openrouter.ai/api/v1/chat/completions.

Default model: `moonshotai/kimi-k2-thinking` — the reasoning variant of
Kimi K2. Extended thinking is enabled by passing `reasoning.effort=high`;
OpenRouter routes it to the model's native reasoning mode when supported
and no-ops it otherwise, so setting it is safe across models.

Structured output: we ask via `response_format={"type":"json_object"}` where
the model supports it, AND the prompt itself requests strict JSON. The
JSON extractor in `client.py` handles fenced/prefixed/thinking-tagged
responses as a belt-and-suspenders fallback.

Agentic follow-up (not wired here): Kimi K2 is strong at tool-use. To
extend this into an agent that fetches company signals (Levels.fyi comp,
engineering blog, GitHub activity) before deciding, add a `tools` field
to the payload and loop on `finish_reason=="tool_calls"`. Kept out of
scope for the first pass — single-turn JSON is enough for qualification.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

from .client import RetryableLLMError

log = logging.getLogger(__name__)


DEFAULT_MODEL = os.getenv("QUAL_MODEL", "moonshotai/kimi-k2-thinking")
DEFAULT_EFFORT = os.getenv("QUAL_REASONING_EFFORT", "high")  # low | medium | high
# Reasoning tokens count toward the completion cap on OpenRouter, and a
# full draft package (letter + bullets + answers) plus high-effort
# reasoning does not fit in 8k — truncated JSON was the observed failure.
# The cap is a ceiling, not a cost; unused headroom bills nothing.
DEFAULT_MAX_TOKENS = int(os.getenv("QUAL_MAX_TOKENS", "16000"))
DEFAULT_TIMEOUT = int(os.getenv("QUAL_TIMEOUT_SECONDS", "180"))

# OpenRouter recommends these headers for attribution / rate-limit tier.
APP_NAME = os.getenv("OPENROUTER_APP_NAME", "karani")
APP_URL = os.getenv("OPENROUTER_APP_URL", "https://github.com/kelyn/karani")

BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class OpenRouterQualifier:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        reasoning_effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
        include_reasoning: bool = False,
    ):
        self.model_name = model
        self._base_url = base_url or BASE_URL
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. Add it to .env or export it."
            )
        self._reasoning_effort = reasoning_effort
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._include_reasoning = include_reasoning

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": APP_URL,
            "X-Title": APP_NAME,
        }

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        force_json: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            # When tools are provided we can't force JSON on every turn —
            # the model needs freedom to emit tool_calls first.
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        elif force_json:
            # Ask for JSON — Kimi honors it; a passthrough for models that don't.
            payload["response_format"] = {"type": "json_object"}

        if self._reasoning_effort:
            payload["reasoning"] = {"effort": self._reasoning_effort}
        if self._include_reasoning:
            payload["include_reasoning"] = True
        return payload

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(RetryableLLMError),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    try:
                        r = await client.post(
                            f"{self._base_url}/chat/completions",
                            headers=self._headers(),
                            json=payload,
                        )
                    except (httpx.TransportError, httpx.TimeoutException) as e:
                        raise RetryableLLMError(str(e)) from e

                    if r.status_code in (429, 500, 502, 503, 504):
                        raise RetryableLLMError(
                            f"openrouter {r.status_code}: {r.text[:200]}"
                        )
                    if r.status_code >= 400:
                        raise RuntimeError(
                            f"openrouter {r.status_code}: {r.text[:400]}"
                        )
                    data = r.json()
                    # Feed the run ledger's per-pass token cost.
                    from . import usage as _usage
                    _usage.record(data.get("usage"))
                    return data
        raise RuntimeError("unreachable")

    async def complete(self, system: str, user: str) -> str:
        """Single-turn completion — used by the non-agent qualifier path."""
        data = await self._post(self._payload([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]))
        try:
            choice = data["choices"][0]
            message = choice["message"]
            usage = data.get("usage") or {}
            if choice.get("finish_reason") == "length":
                log.warning(
                    "output TRUNCATED at max_tokens=%d (completion_tokens=%s)"
                    " — downstream JSON parsing will likely fail; raise "
                    "QUAL_MAX_TOKENS", self._max_tokens,
                    usage.get("completion_tokens"),
                )
            if self._include_reasoning and message.get("reasoning"):
                log.info(
                    "openrouter reasoning tokens=%s completion=%s prompt=%s",
                    usage.get("completion_tokens"),
                    usage.get("prompt_tokens"),
                    usage.get("total_tokens"),
                )
            content = message.get("content") or ""
            if not content and message.get("reasoning"):
                content = message["reasoning"]
            return content
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected openrouter response: {data}") from e

    async def chat_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """One agent-loop round-trip. Returns {content, tool_calls, finish_reason, usage}.

        `tool_calls` is a list of {id, name, arguments_json} dicts, empty
        when the model produced a final answer.
        """
        payload = self._payload(messages, tools=tools, force_json=not tools)
        data = await self._post(payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected openrouter response: {data}") from e

        raw_tool_calls = message.get("tool_calls") or []
        tool_calls = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", "") or "",
            }
            for tc in raw_tool_calls
        ]
        return {
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning") or "",
            "tool_calls": tool_calls,
            "raw_tool_calls": raw_tool_calls,  # for echoing back verbatim
            "finish_reason": choice.get("finish_reason", "stop"),
            "usage": data.get("usage") or {},
        }
