"""OpenAI provider — and, via `base_url`, any OpenAI-compatible cloud.

A subclass of the generic Chat Completions client pointed at
api.openai.com with a required key. Because `base_url` and the key's
env var are both configurable, this same provider covers Groq,
Together, Mistral's La Plateforme, DeepSeek, or any other hosted
OpenAI-compatible endpoint:

    [llm.qualify]
    provider = "openai"
    model = "llama-3.3-70b-versatile"
    base_url = "https://api.groq.com/openai/v1"
    api_key_env = "GROQ_API_KEY"      # name of the env var, never the key
"""
from __future__ import annotations

import os

from .openai_compat import OpenAICompatQualifier

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MAX_TOKENS = int(os.getenv("QUAL_MAX_TOKENS", "16000"))
DEFAULT_TIMEOUT = int(os.getenv("QUAL_TIMEOUT_SECONDS", "180"))


class OpenAIQualifier(OpenAICompatQualifier):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        api_key = api_key or os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{api_key_env} not set. Add it to .env or export it."
            )
        super().__init__(
            model=model,
            base_url=base_url or BASE_URL,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
        )
