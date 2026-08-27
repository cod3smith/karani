"""Anthropic-backed qualifier. Kept for direct API users."""
from __future__ import annotations

import os

from tenacity import (
    AsyncRetrying, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

from .client import RetryableLLMError


DEFAULT_MODEL = os.getenv("QUAL_MODEL", "claude-haiku-4-5-20251001")


class AnthropicQualifier:
    """Async Anthropic client with retry. Import guarded so it's optional."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed. `pip install anthropic` "
                "or `uv sync --extra anthropic`."
            ) from e
        self._client = AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
        self.model_name = model

    async def complete(self, system: str, user: str) -> str:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(RetryableLLMError),
            reraise=True,
        ):
            with attempt:
                try:
                    resp = await self._client.messages.create(
                        model=self.model_name,
                        max_tokens=2000,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                    )
                    return "".join(
                        block.text for block in resp.content
                        if getattr(block, "type", None) == "text"
                    )
                except Exception as e:
                    msg = str(e).lower()
                    if any(t in msg for t in ("rate", "overload", "timeout",
                                              "connection", "server error")):
                        raise RetryableLLMError(str(e)) from e
                    raise
        raise RuntimeError("unreachable")
