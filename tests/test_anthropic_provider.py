"""The Anthropic provider: caching, usage accounting, agent mode.

It was a stub kept "for direct API users" — a date-suffixed model id, a
2000-token ceiling, no usage reporting (so spend was invisible to the
run ledger) and no chat_turn (so agent mode could not use it). Routing
real money through it means all four have to work.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from karani.qualification import usage  # noqa: E402
from karani.qualification.anthropic import (  # noqa: E402
    DEFAULT_MODEL, AnthropicQualifier,
)


class FakeUsage:
    input_tokens = 1200
    output_tokens = 340
    cache_read_input_tokens = 2900
    cache_creation_input_tokens = 0


class FakeBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class FakeResponse:
    content = [FakeBlock('{"fit_score": 80}')]
    usage = FakeUsage()
    stop_reason = "end_turn"
    stop_details = None


class _FakeStream:
    def __init__(self, calls, kwargs):
        self._calls, self._kwargs = calls, kwargs

    async def __aenter__(self):
        self._calls.append({**self._kwargs, "_streamed": True})
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return FakeResponse()


class FakeMessages:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append(kw)
        return FakeResponse()

    def stream(self, **kw):
        return _FakeStream(self.calls, kw)


class FakeClient:
    def __init__(self, *a, **kw):
        self.messages = FakeMessages()


@pytest.fixture
def provider(monkeypatch):
    import anthropic as sdk
    monkeypatch.setattr(sdk, "AsyncAnthropic", FakeClient)
    usage.reset()
    return AnthropicQualifier(api_key="test-key")


# --- model id ---

def test_default_model_is_current_and_undated():
    # "never append date suffixes"; and Haiku 4.5 needs a 4096-token
    # prefix to cache at all, which our ~3k stable prefix misses.
    assert "2025" not in DEFAULT_MODEL and "2026" not in DEFAULT_MODEL
    assert DEFAULT_MODEL != "claude-haiku-4-5"


def test_hosted_provider_fans_out():
    assert AnthropicQualifier.max_parallel > 1


# --- prompt caching ---

@pytest.mark.asyncio
async def test_system_prompt_is_cached(provider):
    # Must clear the model's 512-token minimum, or the breakpoint is a
    # no-op that still bills a 1.25x write.
    stable = "STABLE SYSTEM + RESUME " * 400
    await provider.complete(stable, "per-row job")
    sent = provider._client.messages.calls[0]
    assert isinstance(sent["system"], list), "system must be blocks to cache"
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["system"][0]["text"] == stable
    # The volatile half must stay out of the cached prefix.
    assert "per-row job" not in str(sent["system"])


@pytest.mark.asyncio
async def test_short_system_is_not_cached(provider):
    """Below the model minimum a breakpoint silently does nothing, and a
    cache write costs 1.25x — don't pay for an entry that can't be read."""
    await provider.complete("tiny", "job")
    sent = provider._client.messages.calls[0]
    assert "cache_control" not in str(sent["system"])


# --- usage accounting ---

@pytest.mark.asyncio
async def test_usage_is_recorded_including_cache_reads(provider):
    await provider.complete("x" * 4000, "job")
    totals = usage.snapshot()
    assert totals["calls"] == 1
    # Cached reads are still input tokens — they are billed (at ~0.1x).
    assert totals["prompt_tokens"] == 1200 + 2900
    assert totals["completion_tokens"] == 340
    assert totals["cache_read_tokens"] == 2900


# --- effort / ceilings ---

@pytest.mark.asyncio
async def test_effort_and_max_tokens_are_configurable(monkeypatch):
    import anthropic as sdk
    monkeypatch.setattr(sdk, "AsyncAnthropic", FakeClient)
    p = AnthropicQualifier(api_key="k", effort="low", max_tokens=4096)
    await p.complete("s", "u")
    sent = p._client.messages.calls[0]
    assert sent["output_config"] == {"effort": "low"}
    assert sent["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_no_effort_means_no_output_config(provider):
    await provider.complete("s", "u")
    assert "output_config" not in provider._client.messages.calls[0]


# --- agent mode ---

@pytest.mark.asyncio
async def test_chat_turn_normalizes_tool_calls(monkeypatch):
    """Agent mode (the verify gate) needs the same shape every provider
    returns, not the raw SDK objects."""
    import anthropic as sdk

    class ToolBlock:
        type, id, name = "tool_use", "tu_1", "fetch_page"
        input = {"url": "https://example.com"}

    class ToolResponse(FakeResponse):
        content = [FakeBlock("checking"), ToolBlock()]
        stop_reason = "tool_use"

    class ToolMessages(FakeMessages):
        async def create(self, **kw):
            self.calls.append(kw)
            return ToolResponse()

        def stream(self, **kw):
            messages = self

            class S:
                async def __aenter__(s):
                    messages.calls.append(kw)
                    return s

                async def __aexit__(s, *exc):
                    return False

                async def get_final_message(s):
                    return ToolResponse()
            return S()

    class ToolClient(FakeClient):
        def __init__(self, *a, **kw):
            self.messages = ToolMessages()

    monkeypatch.setattr(sdk, "AsyncAnthropic", ToolClient)
    p = AnthropicQualifier(api_key="k")
    out = await p.chat_turn([{"role": "user", "content": "hi"}],
                            tools=[{"name": "fetch_page",
                                    "description": "d",
                                    "input_schema": {"type": "object"}}])
    assert out["finish_reason"] == "tool_use"
    assert out["content"] == "checking"
    assert out["tool_calls"] == [
        {"id": "tu_1", "name": "fetch_page",
         "arguments": '{"url": "https://example.com"}'},
    ]


# --- agent mode against the REAL tool registry ---

@pytest.mark.asyncio
async def test_real_registry_tools_are_translated(provider):
    """The bug this file missed: my first chat_turn test hand-wrote an
    Anthropic-shaped tool, while karani's registry emits OpenAI shape
    ({"type": "function", ...}). Production got
    400 invalid_request_error on every verify. Always test with what
    the codebase actually produces."""
    from karani.qualification.tools import default_registry

    await provider.chat_turn([{"role": "user", "content": "hi"}],
                             tools=default_registry().schemas())
    sent = provider._client.messages.calls[0]["tools"]
    assert sent, "tools dropped"
    for tool in sent:
        assert "function" not in tool, "OpenAI envelope leaked to Anthropic"
        assert set(tool) >= {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_openai_style_transcript_is_translated(provider):
    """The agent echoes assistant tool_calls and role='tool' results in
    OpenAI form; the Messages API takes tool_use / tool_result blocks."""
    await provider.chat_turn([
        {"role": "user", "content": "check gitlab"},
        {"role": "assistant", "content": "looking",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "fetch_page",
                                      "arguments": '{"url": "https://x"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "200 OK"},
    ])
    msgs = provider._client.messages.calls[0]["messages"]
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    blocks = {b["type"] for b in assistant["content"]}
    assert "tool_use" in blocks
    use = [b for b in assistant["content"] if b["type"] == "tool_use"][0]
    assert use["id"] == "call_1" and use["input"] == {"url": "https://x"}
    # The result comes back as a user turn carrying a tool_result block.
    result_blocks = [b for m in msgs if m["role"] == "user"
                     for b in (m["content"] if isinstance(m["content"], list) else [])
                     if b.get("type") == "tool_result"]
    assert result_blocks and result_blocks[0]["tool_use_id"] == "call_1"
    assert "no tool_calls key" not in str(msgs)
    assert all("tool_calls" not in m for m in msgs)


# --- long outputs must stream ---

@pytest.mark.asyncio
async def test_large_max_tokens_uses_streaming(monkeypatch):
    """The SDK raises ValueError on non-streaming requests whose
    max_tokens could run past 10 minutes — which is every draft once
    the pack ceiling went to 32k."""
    import anthropic as sdk
    monkeypatch.setattr(sdk, "AsyncAnthropic", FakeClient)
    p = AnthropicQualifier(api_key="k", max_tokens=32000)
    out = await p.complete("sys", "user")
    assert out == '{"fit_score": 80}'
    assert p._client.messages.calls[0]["_streamed"] is True


@pytest.mark.asyncio
async def test_small_max_tokens_stays_non_streaming(monkeypatch):
    import anthropic as sdk
    monkeypatch.setattr(sdk, "AsyncAnthropic", FakeClient)
    p = AnthropicQualifier(api_key="k", max_tokens=4000)
    await p.complete("sys", "user")
    assert "_streamed" not in p._client.messages.calls[0]
