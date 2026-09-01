"""Generic OpenAI-compatible Chat Completions client — the provider base.

Every provider that speaks the Chat Completions wire format (OpenAI,
OpenRouter, Groq, Together, and every local server: Ollama, LM Studio,
vLLM, llama.cpp) is a thin subclass of `OpenAICompatQualifier`: pick a
base URL, an auth story, and optionally extend the payload via
`_extra_payload()` (OpenRouter's `reasoning.effort` lives there, not
here). The subclasses in this package:

    openrouter.py  OpenRouterQualifier   attribution headers + reasoning
    openai.py      OpenAIQualifier       api.openai.com, key required
    local.py       LocalQualifier        localhost, no key, long timeout

Structured output: we ask via `response_format={"type":"json_object"}`
where the server supports it, AND the prompt itself requests strict
JSON. The extractor in `client.py` handles fenced/prefixed/
thinking-tagged responses as a belt-and-suspenders fallback.

`chat_turn` implements one agent-loop round-trip (tool calling), so any
subclass whose model supports tools gets agent mode for free.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

from .client import RetryableLLMError

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16000
DEFAULT_TIMEOUT = 180


class OpenAICompatQualifier:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.model_name = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout = timeout

    # --- subclass hooks ---

    def _extra_headers(self) -> dict[str, str]:
        return {}

    def _extra_payload(self) -> dict[str, Any]:
        return {}

    # --- request plumbing ---

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers())
        return headers

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
            payload["response_format"] = {"type": "json_object"}
        payload.update(self._extra_payload())
        return payload

    @property
    def _host(self) -> str:
        return urlparse(self._base_url).netloc or self._base_url

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
                            f"{self._host} {r.status_code}: {r.text[:200]}"
                        )
                    if r.status_code >= 400:
                        raise RuntimeError(
                            f"{self._host} {r.status_code}: {r.text[:400]}"
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
                    "the task's max_tokens", self._max_tokens,
                    usage.get("completion_tokens"),
                )
            content = message.get("content") or ""
            # Thinking models that ran out of budget mid-reasoning return
            # the trace in `reasoning` with empty content — better than
            # nothing for the JSON extractor.
            if not content and message.get("reasoning"):
                content = message["reasoning"]
            return content
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected {self._host} response: {data}") from e

    async def chat_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """One agent-loop round-trip. Returns {content, tool_calls, finish_reason, usage}.

        `tool_calls` is a list of {id, name, arguments} dicts, empty when
        the model produced a final answer.
        """
        payload = self._payload(messages, tools=tools, force_json=not tools)
        data = await self._post(payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected {self._host} response: {data}") from e

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
