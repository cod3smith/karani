"""The pluggable provider interface (ADR 0017): registry, the openai
provider (and via base_url, any OpenAI-compatible cloud), and the
config knobs that let users point karani at whatever LLM they have."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import karani.qualification.openai_compat as compat_mod
from karani.config import reload_config
from karani.qualification.factory import (
    available_providers, get_qualifier, register_provider,
)
from karani.qualification.openai import OpenAIQualifier


@pytest.fixture(autouse=True)
def _restore_defaults():
    yield
    reload_config()  # KARANI_CONFIG=/nonexistent in conftest -> defaults


@pytest.fixture
def mock_llm_http(monkeypatch):
    """Route provider HTTP through a MockTransport; capture requests."""
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


# --- registry ---

def test_builtin_providers_registered():
    assert {"openrouter", "openai", "anthropic", "local"} <= set(
        available_providers())


def test_unknown_provider_lists_available():
    with pytest.raises(ValueError, match="openrouter"):
        get_qualifier(provider="does-not-exist")


def test_custom_provider_registration():
    class MyClient:
        model_name = "custom"

        async def complete(self, system: str, user: str) -> str:
            return "{}"

    register_provider("mytest", lambda spec: MyClient())
    try:
        client = get_qualifier(provider="mytest")
        assert client.model_name == "custom"
    finally:
        from karani.qualification import factory
        factory._REGISTRY.pop("mytest", None)


# --- openai provider ---

def test_openai_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIQualifier()


@pytest.mark.asyncio
async def test_openai_hits_openai_with_bearer(mock_llm_http, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = get_qualifier(provider="openai", model="gpt-5-mini")
    out = await client.complete("sys", "user")
    assert out == '{"ok": true}'

    request = mock_llm_http[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    payload = json.loads(request.content)
    assert payload["model"] == "gpt-5-mini"
    # No OpenRouter extensions on a plain OpenAI-compatible endpoint.
    assert "reasoning" not in payload


@pytest.mark.asyncio
async def test_openai_compatible_cloud_via_config(tmp_path, mock_llm_http,
                                                  monkeypatch):
    """base_url + api_key_env in karani.toml cover Groq/Together/vLLM/...
    without any karani code changes — the point of the interface."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-abc")
    cfg = tmp_path / "karani.toml"
    cfg.write_text("""
version = 1
[llm.qualify]
provider = "openai"
model = "llama-3.3-70b-versatile"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
""")
    reload_config(cfg)
    client = get_qualifier(task="qualify")
    await client.complete("sys", "user")

    request = mock_llm_http[0]
    assert str(request.url).startswith("https://api.groq.com/openai/v1/")
    assert request.headers["authorization"] == "Bearer gsk-abc"


# --- config knobs reach the wire ---

@pytest.mark.asyncio
async def test_task_max_tokens_and_local_base_url(tmp_path, mock_llm_http):
    cfg = tmp_path / "karani.toml"
    cfg.write_text("""
version = 1
[llm.qualify]
provider = "local"
model = "qwen3:4b"
base_url = "http://gpu-box:11434/v1"
max_tokens = 4096
""")
    reload_config(cfg)
    client = get_qualifier(task="qualify")
    await client.complete("sys", "user")

    request = mock_llm_http[0]
    assert str(request.url) == "http://gpu-box:11434/v1/chat/completions"
    assert json.loads(request.content)["max_tokens"] == 4096


def test_openrouter_keeps_reasoning_extension(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    client = get_qualifier(provider="openrouter", model="m")
    payload = client._payload([{"role": "user", "content": "x"}])
    assert payload["reasoning"] == {"effort": "high"}
    headers = client._headers()
    assert headers["X-Title"] == "karani"
    assert headers["Authorization"] == "Bearer or-test"


def test_agent_mode_inherited_by_all_compat_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for provider in ("openai", "local"):
        assert hasattr(get_qualifier(provider=provider), "chat_turn")
