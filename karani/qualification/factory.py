"""Provider registry with per-task routing (ADRs 0015, 0017).

Resolution per knob: explicit argument > env (QUAL_PROVIDER/QUAL_MODEL)
> karani.toml `[llm.<task>]` (falling back to `[llm.default]`) > built-in
default. Tasks: qualify, draft, humanize, tailor, prep, followup, agent —
so a user can run bulk qualification on a local model and keep a strong
hosted model for drafting, from config alone.

Providers are pluggable: `register_provider("mine", builder)` makes
`provider = "mine"` valid everywhere (CLI flags, env, karani.toml). A
builder takes the resolved `ProviderSpec` and returns any object
satisfying the `QualifierClient` protocol. The built-ins:

    openrouter  hosted, many models, OPENROUTER_API_KEY
    openai      api.openai.com or any OpenAI-compatible cloud
                (Groq/Together/...) via base_url + api_key_env
    anthropic   direct Anthropic API (optional `anthropic` extra)
    local       Ollama / LM Studio / vLLM / llama.cpp — no key, no cost
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from .client import QualifierClient


@dataclass(frozen=True)
class ProviderSpec:
    """Everything a builder may need, already merged across sources.
    All fields optional — builders fall back to their own defaults."""
    model: str | None = None
    effort: str | None = None
    max_tokens: int | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    timeout: int | None = None

    def api_key(self) -> str | None:
        """The key named by `api_key_env`, if configured. Secrets stay in
        env — karani.toml only ever carries the variable's *name*."""
        return os.getenv(self.api_key_env) if self.api_key_env else None


ProviderBuilder = Callable[[ProviderSpec], QualifierClient]

_REGISTRY: dict[str, ProviderBuilder] = {}

_BUILTIN_PROVIDER = "openrouter"


def register_provider(name: str, builder: ProviderBuilder) -> None:
    _REGISTRY[name.lower()] = builder


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


# --- built-in builders (imports deferred so optional deps stay optional) ---

def _drop_none(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


def _build_openrouter(spec: ProviderSpec) -> QualifierClient:
    from .openrouter import DEFAULT_MODEL, OpenRouterQualifier
    return OpenRouterQualifier(model=spec.model or DEFAULT_MODEL, **_drop_none(
        api_key=spec.api_key(), base_url=spec.base_url,
        reasoning_effort=spec.effort, max_tokens=spec.max_tokens,
        timeout=spec.timeout,
    ))


def _build_openai(spec: ProviderSpec) -> QualifierClient:
    from .openai import DEFAULT_MODEL, OpenAIQualifier
    return OpenAIQualifier(model=spec.model or DEFAULT_MODEL, **_drop_none(
        base_url=spec.base_url, api_key_env=spec.api_key_env,
        max_tokens=spec.max_tokens, timeout=spec.timeout,
    ))


def _build_anthropic(spec: ProviderSpec) -> QualifierClient:
    from .anthropic import DEFAULT_MODEL, AnthropicQualifier
    return AnthropicQualifier(model=spec.model or DEFAULT_MODEL, **_drop_none(
        api_key=spec.api_key(), max_tokens=spec.max_tokens,
    ))


def _build_local(spec: ProviderSpec) -> QualifierClient:
    from .local import DEFAULT_MODEL, LocalQualifier
    return LocalQualifier(model=spec.model or DEFAULT_MODEL, **_drop_none(
        base_url=spec.base_url, api_key=spec.api_key(),
        max_tokens=spec.max_tokens, timeout=spec.timeout,
    ))


register_provider("openrouter", _build_openrouter)
register_provider("openai", _build_openai)
register_provider("anthropic", _build_anthropic)
register_provider("local", _build_local)


# --- resolution ---

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

    builder = _REGISTRY.get(provider)
    if builder is None:
        raise ValueError(
            f"unknown provider {provider!r}. "
            f"Available: {', '.join(available_providers())}."
        )

    # Env beats config per knob (same precedence as provider/model).
    spec = ProviderSpec(
        model=model or os.getenv("QUAL_MODEL") or cfg.model,
        effort=os.getenv("QUAL_REASONING_EFFORT") or cfg.effort,
        max_tokens=(int(os.environ["QUAL_MAX_TOKENS"])
                    if "QUAL_MAX_TOKENS" in os.environ else cfg.max_tokens),
        base_url=cfg.base_url,
        api_key_env=cfg.api_key_env,
        timeout=cfg.timeout,
    )
    return builder(spec)
