"""Provider selection with per-task routing (ADR 0015).

Resolution per knob: explicit argument > env (QUAL_PROVIDER/QUAL_MODEL)
> karani.toml `[llm.<task>]` (falling back to `[llm.default]`) > built-in
default. Tasks: qualify, draft, humanize, tailor, prep, followup, agent —
so a user can run bulk qualification on a local model and keep a strong
hosted model for drafting, from config alone.
"""
from __future__ import annotations

import os

from .client import QualifierClient

_BUILTIN_PROVIDER = "openrouter"


def _task_cfg(task: str):
    from karani.config import get_config
    return get_config().llm.for_task(task)


def get_qualifier(
    provider: str | None = None, model: str | None = None,
    *, task: str = "qualify",
) -> QualifierClient:
    cfg = _task_cfg(task)
    provider = (provider or os.getenv("QUAL_PROVIDER") or cfg.provider
                or _BUILTIN_PROVIDER).lower()
    model = model or os.getenv("QUAL_MODEL") or cfg.model

    if provider == "openrouter":
        from .openrouter import DEFAULT_MODEL, OpenRouterQualifier
        kwargs = {}
        if cfg.effort is not None and "QUAL_REASONING_EFFORT" not in os.environ:
            kwargs["reasoning_effort"] = cfg.effort
        if cfg.max_tokens is not None and "QUAL_MAX_TOKENS" not in os.environ:
            kwargs["max_tokens"] = cfg.max_tokens
        return OpenRouterQualifier(model=model or DEFAULT_MODEL, **kwargs)
    if provider == "anthropic":
        from .anthropic import DEFAULT_MODEL, AnthropicQualifier
        return AnthropicQualifier(model=model or DEFAULT_MODEL)
    if provider == "local":
        from .local import DEFAULT_MODEL, LocalQualifier
        return LocalQualifier(model=model or DEFAULT_MODEL)

    raise ValueError(
        f"unknown provider {provider!r}. "
        f"Use 'openrouter', 'anthropic', or 'local'."
    )
