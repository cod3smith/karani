"""Anthropic-backed qualifier — the hosted quality tier (ADR 0019).

Speaks the Messages API directly rather than the Chat Completions shape,
so it does not share `OpenAICompatQualifier`'s plumbing. Three things
matter for running real money through it:

**Prompt caching.** Two thirds of a qualification request is the same on
every row (instructions + resume + taste calibration), so the caller
puts the stable half in `system` and we mark it `cache_control:
ephemeral`. Reads cost ~0.1x of input, writes 1.25x, so the break-even
is two requests and a 50-row pass is pure profit. The marker is only
attached above `MIN_CACHEABLE_TOKENS` — below the model's minimum a
breakpoint silently caches nothing while still charging the 1.25x
write. The 5-minute TTL is deliberate: rows in a pass land seconds
apart and every read refreshes the timer, so the entry never goes cold
mid-pass and we skip the doubled 1h write price.

**Usage.** Every response feeds `usage.record()`, which the run ledger
turns into per-pass cost. Cache reads are billed input, so they count.

**Agent mode.** `chat_turn` normalizes Messages-API tool blocks into the
same dict the OpenAI-compatible providers return, so the verify gate
(ADR 0016) and the agent loop work here unchanged.
"""
from __future__ import annotations

import json
import os

from tenacity import (
    AsyncRetrying, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

from .client import RetryableLLMError

# Opus 5.5 is the default; karani.toml routes the bulk qualify tier to
# Haiku 5.5. Never date-suffix these — the bare id is the whole id.
DEFAULT_MODEL = os.getenv("QUAL_MODEL", "claude-opus-5-5")
DEFAULT_MAX_TOKENS = int(os.getenv("QUAL_MAX_TOKENS", "16000"))

# Minimum cacheable prefix on the current (5.x) models. Haiku 4.5 and the
# Opus 4.5/4.6 generation need 4096 — a reason not to route bulk work
# there, since karani's stable prefix is ~3k tokens.
MIN_CACHEABLE_TOKENS = 512
# Above this ceiling the SDK refuses a non-streaming request outright
# ("Streaming is required for operations that may take longer than 10
# minutes"), which is every draft since the pack ceiling went to 32k.
STREAM_ABOVE_MAX_TOKENS = 8192
_CHARS_PER_TOKEN = 3.7      # rough; only gates the cache marker


def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    """OpenAI function schemas -> Messages API tool definitions.

    karani's registry speaks the OpenAI shape because that is the
    lingua franca across its providers (ADR 0017); the adapter is this
    provider's job, not the agent loop's. Already-Anthropic tools pass
    through untouched.
    """
    out = []
    for t in tools or []:
        if "input_schema" in t:
            out.append(t)
            continue
        fn = t.get("function") or {}
        out.append({
            "name": fn.get("name") or t.get("name", ""),
            "description": fn.get("description") or t.get("description", ""),
            "input_schema": fn.get("parameters") or t.get("parameters")
                            or {"type": "object", "properties": {}},
        })
    return out


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """OpenAI transcript -> Messages API blocks.

    Assistant turns carrying `tool_calls` become text + tool_use blocks;
    `role: "tool"` results become a user turn holding tool_result. Both
    are rejected outright in their OpenAI form.
    """
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")

        if role == "tool":
            block = {"type": "tool_result",
                     "tool_use_id": m.get("tool_call_id", ""),
                     "content": content or ""}
            # Consecutive results belong in one user turn.
            if out and out[-1]["role"] == "user" and isinstance(
                    out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                raw = fn.get("arguments", tc.get("arguments", "")) or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use",
                               "id": tc.get("id", ""),
                               "name": fn.get("name") or tc.get("name", ""),
                               "input": args})
            out.append({"role": "assistant", "content": blocks})
            continue

        out.append({"role": role, "content": content or ""})
    return out


class AnthropicQualifier:
    """Async Anthropic client with retry. Import guarded so it's optional."""

    # Hosted endpoint: parallel requests are served in parallel.
    max_parallel = 4

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 *, max_tokens: int = DEFAULT_MAX_TOKENS,
                 effort: str | None = None, timeout: int | None = None):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed. `pip install anthropic` "
                "or `uv sync --extra anthropic`."
            ) from e
        kwargs = {"api_key": api_key or os.getenv("ANTHROPIC_API_KEY")}
        if timeout is not None:
            kwargs["timeout"] = float(timeout)
        self._client = AsyncAnthropic(**kwargs)
        self.model_name = model
        self._max_tokens = max_tokens
        self._effort = effort

    # --- request shaping ---

    def _system_blocks(self, system: str) -> list[dict]:
        """The stable prefix, with a cache breakpoint when it is long
        enough for the cache to actually hold it."""
        block: dict = {"type": "text", "text": system}
        if len(system) / _CHARS_PER_TOKEN >= MIN_CACHEABLE_TOKENS:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _common(self, system: str) -> dict:
        payload: dict = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "system": self._system_blocks(system),
        }
        if self._effort:
            payload["output_config"] = {"effort": self._effort}
        return payload

    def _record(self, resp) -> dict:
        """Feed the usage counters; return the normalized usage dict."""
        u = getattr(resp, "usage", None)

        def field(name: str) -> int:
            return int(getattr(u, name, 0) or 0)

        cache_read = field("cache_read_input_tokens")
        normalized = {
            # Cache reads are input tokens — discounted, not free.
            "prompt_tokens": field("input_tokens") + cache_read,
            "completion_tokens": field("output_tokens"),
            "cache_read_tokens": cache_read,
            "cache_write_tokens": field("cache_creation_input_tokens"),
            "model": self.model_name,
        }
        from . import usage as _usage
        _usage.record(normalized)
        return normalized

    async def _send(self, **kwargs):
        """One request, retried. Streams when the output ceiling is high
        enough that the SDK would reject a blocking call."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(RetryableLLMError),
            reraise=True,
        ):
            with attempt:
                try:
                    if self._max_tokens > STREAM_ABOVE_MAX_TOKENS:
                        async with self._client.messages.stream(
                                **kwargs) as stream:
                            return await stream.get_final_message()
                    return await self._client.messages.create(**kwargs)
                except Exception as e:
                    msg = str(e).lower()
                    if any(t in msg for t in ("rate", "overload", "timeout",
                                              "connection", "server error")):
                        raise RetryableLLMError(str(e)) from e
                    raise
        raise RuntimeError("unreachable")

    # --- the QualifierClient protocol ---

    async def complete(self, system: str, user: str) -> str:
        resp = await self._send(
            **self._common(system),
            messages=[{"role": "user", "content": user}],
        )
        self._record(resp)
        return "".join(
            block.text for block in resp.content
            if getattr(block, "type", None) == "text"
        )

    async def chat_turn(self, messages: list[dict],
                        tools: list[dict] | None = None) -> dict:
        """One agent-loop round-trip, in the shared provider shape.

        The caller passes OpenAI-style messages whose first entry is the
        system turn; the Messages API takes system separately.
        """
        system = ""
        convo: list[dict] = []
        for m in messages:
            if m.get("role") == "system" and not convo:
                system = m.get("content") or ""
            else:
                convo.append(m)

        kwargs = dict(self._common(system),
                      messages=_to_anthropic_messages(convo))
        if tools:
            kwargs["tools"] = _to_anthropic_tools(tools)
        resp = await self._send(**kwargs)
        usage_dict = self._record(resp)

        text, tool_calls = [], []
        for block in resp.content:
            kind = getattr(block, "type", None)
            if kind == "text":
                text.append(block.text)
            elif kind == "tool_use":
                tool_calls.append({
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    # Normalized to a JSON string, as the OpenAI-compatible
                    # providers return — the agent loop json.loads() it.
                    "arguments": json.dumps(getattr(block, "input", {}) or {}),
                })
        return {
            "content": "".join(text),
            "reasoning": "",
            "tool_calls": tool_calls,
            "raw_tool_calls": tool_calls,
            "finish_reason": getattr(resp, "stop_reason", "end_turn"),
            "usage": usage_dict,
        }
