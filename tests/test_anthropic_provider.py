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


class FakeMessages:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append(kw)
        return FakeResponse()


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
