"""Local OpenAI-compatible qualifier — Ollama, LM Studio, vLLM, llama.cpp.

Speaks the same Chat Completions wire format as OpenRouter, so this is a
thin subclass: different base URL, no API key required, reasoning-effort
omitted by default (local servers ignore or reject OpenRouter extensions).
`chat_turn` is inherited, so agent mode works with any local model that
supports tool calling (qwen3, llama3.3, mistral-small, ...).

Zero token cost — use it for bulk qualification and keep a stronger hosted
model for drafting, where output quality is what gets the interview:

    QUAL_PROVIDER=local LOCAL_LLM_MODEL=qwen3:32b python -m ingestion.cli qualify
    python -m ingestion.cli draft 123 --provider openrouter

Defaults target Ollama (`http://localhost:11434/v1`). Point
LOCAL_LLM_BASE_URL at LM Studio (`http://localhost:1234/v1`) or any other
OpenAI-compatible server.
"""
from __future__ import annotations

import os

from .openrouter import OpenRouterQualifier

DEFAULT_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
DEFAULT_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen3:32b")
# Local inference is slower than hosted; be generous by default.
DEFAULT_TIMEOUT = int(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_TOKENS = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "8000"))


class LocalQualifier(OpenRouterQualifier):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        super().__init__(
            model=model,
            # Local servers don't authenticate; some (LM Studio) still want
            # a Bearer header present, so send a placeholder.
            api_key=os.getenv("LOCAL_LLM_API_KEY", "local"),
            base_url=base_url,
            reasoning_effort="",  # omit OpenRouter's reasoning extension
            max_tokens=max_tokens,
            timeout=timeout,
        )
