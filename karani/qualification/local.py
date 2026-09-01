"""Local OpenAI-compatible qualifier — Ollama, LM Studio, vLLM, llama.cpp.

A subclass of the generic Chat Completions client: localhost base URL,
no real API key, generous timeout (local inference is slower than
hosted). `chat_turn` is inherited, so agent mode works with any local
model that supports tool calling (qwen3, llama3.3, mistral-small, ...).

Zero token cost — use it for bulk qualification and keep a stronger
hosted model for drafting, where output quality is what gets the
interview. Route it from karani.toml:

    [llm.qualify]
    provider = "local"
    model = "qwen3:4b"

Defaults target Ollama (`http://localhost:11434/v1`). Point
LOCAL_LLM_BASE_URL (or `base_url` in the task config) at LM Studio
(`http://localhost:1234/v1`) or any other OpenAI-compatible server.
"""
from __future__ import annotations

import os

from .openai_compat import OpenAICompatQualifier

DEFAULT_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
DEFAULT_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen3:4b")
# Local inference is slower than hosted; be generous by default.
DEFAULT_TIMEOUT = int(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_TOKENS = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "8000"))


class LocalQualifier(OpenAICompatQualifier):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        super().__init__(
            model=model,
            base_url=base_url,
            # Local servers don't authenticate; some (LM Studio) still want
            # a Bearer header present, so send a placeholder.
            api_key=api_key or os.getenv("LOCAL_LLM_API_KEY", "local"),
            max_tokens=max_tokens,
            timeout=timeout,
        )
