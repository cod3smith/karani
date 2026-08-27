"""Pick a qualifier based on env.

QUAL_PROVIDER=openrouter (default) → OpenRouterQualifier
QUAL_PROVIDER=anthropic            → AnthropicQualifier
QUAL_PROVIDER=local                → LocalQualifier (Ollama/LM Studio/vLLM)
"""
from __future__ import annotations

import os

from .client import QualifierClient


def get_qualifier(
    provider: str | None = None, model: str | None = None,
) -> QualifierClient:
    provider = (provider or os.getenv("QUAL_PROVIDER", "openrouter")).lower()

    if provider == "openrouter":
        from .openrouter import OpenRouterQualifier, DEFAULT_MODEL
        return OpenRouterQualifier(model=model or DEFAULT_MODEL)
    if provider == "anthropic":
        from .anthropic import AnthropicQualifier, DEFAULT_MODEL
        return AnthropicQualifier(model=model or DEFAULT_MODEL)
    if provider == "local":
        from .local import LocalQualifier, DEFAULT_MODEL
        return LocalQualifier(model=model or DEFAULT_MODEL)

    raise ValueError(
        f"unknown QUAL_PROVIDER={provider!r}. "
        f"Use 'openrouter', 'anthropic', or 'local'."
    )
