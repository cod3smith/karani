"""Shared qualification plumbing — provider-agnostic.

Providers live in `qualification/openrouter.py` and `qualification/anthropic.py`
and expose the `QualifierClient` protocol below. Use `qualification.factory.
get_qualifier()` to pick one based on env.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from pydantic import ValidationError

from .models import QualificationResult
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt

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
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip inline reasoning traces some thinking models emit.
    text = _THINK_TAG.sub("", text)
    text = _REASONING_TAG.sub("", text)
    text = text.strip()

    # Prefer fenced JSON if present — most reliable.
    m = _JSON_FENCE.search(text)
    if m:
        return json.loads(m.group(1))

    # Otherwise trim to the first `{` (drops "Here is the JSON:" prefixes).
    first_brace = text.find("{")
    if first_brace > 0:
        text = text[first_brace:]
    return json.loads(text)


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
    raw = await client.complete(SYSTEM_PROMPT, user)
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
