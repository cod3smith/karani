"""LocalQualifier — OpenAI-compatible local endpoint (Ollama/LM Studio/vLLM)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import karani.qualification.openai_compat as compat_mod
from karani.qualification.factory import get_qualifier
from karani.qualification.local import LocalQualifier


def test_factory_dispatches_local():
    client = get_qualifier(provider="local")
    assert isinstance(client, LocalQualifier)


def test_local_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = LocalQualifier(model="qwen3:8b")
    assert client.model_name == "qwen3:8b"


def test_local_inherits_chat_turn():
    # Tool-calling agent mode must work against local models too.
    assert hasattr(LocalQualifier(), "chat_turn")


@pytest.fixture
def mock_llm_http(monkeypatch):
    """Route the provider's HTTP through a MockTransport; capture requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'},
                         "finish_reason": "stop"}],
            "usage": {"completion_tokens": 5},
        })

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        compat_mod, "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kw: real_client(transport=transport, **kw),
            TransportError=httpx.TransportError,
            TimeoutException=httpx.TimeoutException,
        ),
    )
    return seen


@pytest.mark.asyncio
async def test_local_complete_hits_local_base_url(mock_llm_http):
    client = LocalQualifier(model="qwen3:8b",
                            base_url="http://localhost:11434/v1")
    out = await client.complete("sys", "user")
    assert out == '{"ok": true}'

    request = mock_llm_http[0]
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["model"] == "qwen3:8b"
    # OpenRouter's reasoning extension must be omitted for local servers.
    assert "reasoning" not in payload
    assert payload["response_format"] == {"type": "json_object"}
