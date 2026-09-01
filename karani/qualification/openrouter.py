"""OpenRouter provider — hosted default.

A thin subclass of the generic Chat Completions client: OpenRouter's
base URL, required `OPENROUTER_API_KEY`, the attribution headers the
service recommends, and its `reasoning.effort` payload extension.

Default model: `moonshotai/kimi-k2-thinking` — the reasoning variant of
Kimi K2. Extended thinking is enabled by passing `reasoning.effort=high`;
OpenRouter routes it to the model's native reasoning mode when supported
and no-ops it otherwise, so setting it is safe across models.
"""
from __future__ import annotations

import os
from typing import Any

from .openai_compat import OpenAICompatQualifier

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
APP_URL = os.getenv("OPENROUTER_APP_URL", "https://github.com/cod3smith/karani")

BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class OpenRouterQualifier(OpenAICompatQualifier):
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
        api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. Add it to .env or export it."
            )
        super().__init__(
            model=model,
            base_url=base_url or BASE_URL,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        self._reasoning_effort = reasoning_effort
        self._include_reasoning = include_reasoning

    def _extra_headers(self) -> dict[str, str]:
        return {"HTTP-Referer": APP_URL, "X-Title": APP_NAME}

    def _extra_payload(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self._reasoning_effort:
            extra["reasoning"] = {"effort": self._reasoning_effort}
        if self._include_reasoning:
            extra["include_reasoning"] = True
        return extra
