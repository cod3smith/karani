"""Shared qualification plumbing — provider-agnostic.

Providers live in `qualification/{openrouter,openai,local,anthropic}.py`
and expose the `QualifierClient` protocol below (ADR 0017). Use
`qualification.factory.get_qualifier()` to pick one from config/env, or
`register_provider()` to plug in your own.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from pydantic import ValidationError

from .models import QualificationResult
from .prompts import PROMPT_VERSION, build_user_prompt, system_prompt

log = logging.getLogger(__name__)


class QualifierClient(Protocol):
    """A callable that takes (system, user) prompts and returns raw text."""
    model_name: str

    async def complete(self, system: str, user: str) -> str: ...


class RetryableLLMError(Exception):
    """Signal to tenacity that a call should be retried."""


# --- JSON extraction: strip reasoning traces + code fences + prose prefixes ---

_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_REASONING_TAG = re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> dict:
    """Pull the intended JSON object out of arbitrary model output.

    Thinking models emit reasoning prose around (and containing) braces,
    so naive first-brace or non-greedy-fence extraction can grab a nested
    fragment — e.g. a single tailored_bullet object — and "succeed" with
    the wrong payload. Strategy: try every '{' as a decode start with
    raw_decode (which tolerates trailing text) and return the decoded
    dict with the MOST keys — the full payload always beats any of its
    own sub-objects.
    """
    text = text.strip()
    # Strip inline reasoning traces some thinking models emit.
    text = _THINK_TAG.sub("", text)
    text = _REASONING_TAG.sub("", text)
    text = text.strip()

    # Fast path: the whole thing is JSON.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    best: dict | None = None
    idx = text.find("{")
    while idx != -1:
        try:
            candidate, _ = decoder.raw_decode(text, idx)
            if isinstance(candidate, dict) and (
                best is None or len(candidate) > len(best)
            ):
                best = candidate
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    if best is None:
        raise json.JSONDecodeError("no JSON object found", text[:200], 0)
    return best


# --- Public helper the runner calls ---

async def qualify_one(
    client: QualifierClient,
    *,
    resume: str,
    resume_hash: str,
    hints: list[str],
    job_row: dict,
    past_verdicts: list[dict] | None = None,
    memories: list[str] | None = None,
    model_name: str | None = None,
) -> QualificationResult:
    user = build_user_prompt(
        resume=resume, hints=hints, job_row=job_row,
        past_verdicts=past_verdicts, memories=memories,
    )
    raw = await client.complete(system_prompt(), user)
    try:
        data = _extract_json(raw)
        result = QualificationResult.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("qualification returned malformed JSON: %s -- raw: %r",
                    e, raw[:400])
        result = QualificationResult(
            fit_score=0,
            verdict="maybe",
            why_apply="",
            why_skip="LLM returned malformed JSON; needs manual review.",
            recommended_positioning="",
        )
    result.model = model_name or getattr(client, "model_name", "unknown")
    result.prompt_version = PROMPT_VERSION
    result.resume_hash = resume_hash
    return result
