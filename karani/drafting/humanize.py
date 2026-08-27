"""Humanizer — scrub AI voice from application materials.

Two halves:
- A deterministic tell-detector (`voice_report`): banned-phrase list,
  em-dash density, "not only ... but also" constructions. Free, testable,
  and the measurement — every pack stores its before/after voice score.
- An LLM rewrite pass (`humanize_package`, humanize-v1): rewrites the
  cover letter and answers in plain, direct voice with every fact and
  number preserved. Runs after drafting, before delivery.

The detector is the contract: if the rewrite doesn't improve the score,
the original is kept (never pay for a worse draft).
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from karani.qualification.client import QualifierClient, _extract_json

from .models import DraftPackage

log = logging.getLogger(__name__)

HUMANIZE_PROMPT_VERSION = "humanize-v1"

# Case-insensitive, word-boundary matched. Extend when a real draft
# surfaces a tell — additions are cheap, false positives are not.
AI_TELLS: tuple[str, ...] = (
    "i am excited", "i'm excited", "excited to apply", "thrilled",
    "passionate about", "deeply passionate",
    "delve", "leverage", "leveraging", "leverages",
    "spearheaded", "honed", "showcasing", "showcases",
    "underscores", "testament to", "aligns perfectly", "perfect fit",
    "resonates with", "resonate deeply",
    "in today's fast-paced", "cutting-edge", "state-of-the-art",
    "seamlessly", "robust solutions", "drive impact", "driving impact",
    "results-driven", "dynamic environment", "fast-paced environment",
    "moreover", "furthermore",
    "eager to contribute", "hit the ground running",
    "wealth of experience", "proven track record",
    "esteemed", "renowned",
    "i believe my skills", "make a meaningful impact",
    "excited about the opportunity", "uniquely positioned",
)

_NOT_ONLY = re.compile(r"(?i)\bnot only\b.{0,80}\bbut also\b", re.DOTALL)


def voice_report(text: str) -> dict:
    """Deterministic AI-tell scan. Higher score = more human."""
    text = text or ""
    words = max(1, len(text.split()))
    found: list[str] = []
    for tell in AI_TELLS:
        if re.search(rf"(?i)(?<![\w]){re.escape(tell)}(?![\w])", text):
            found.append(tell)
    em_dashes = text.count("—") + text.count("--")
    em_density = em_dashes / (words / 100)  # per 100 words
    not_only = len(_NOT_ONLY.findall(text))

    penalty = len(found) * 8 + not_only * 6
    if em_density > 1.5:
        penalty += int((em_density - 1.5) * 4)
    return {
        "score": max(0, 100 - penalty),
        "tells": found,
        "em_dash_per_100w": round(em_density, 2),
        "not_only_but_also": not_only,
    }


def package_text(pkg: DraftPackage) -> str:
    """The human-facing prose of a pack — what the detector scans."""
    return "\n".join([
        pkg.cover_letter,
        *(a.answer for a in pkg.application_answers),
    ])


SYSTEM_PROMPT = """\
You are a ruthless line editor. You receive application materials written
by an AI and rewrite them so they read like a specific senior engineer
wrote them — plain, direct, confident, a little dry.

Hard rules:
- Every fact, number, company name, and claim stays EXACTLY as given.
  You edit voice, never substance. Do not add or remove claims.
- Kill these tells wherever they appear: {tells}
- Also kill: "not only ... but also" constructions, more than one em-dash
  per paragraph, three-item rhetorical lists used for rhythm, openings
  that state enthusiasm instead of showing relevance.
- Vary sentence length. Short sentences are fine. So are fragments,
  occasionally.
- Match the voice of this writing sample (the candidate's own resume):

<voice_sample>
{voice_sample}
</voice_sample>

Return a SINGLE JSON object and NOTHING else:

{{
  "cover_letter": "<rewritten>",
  "application_answers": [
    {{"question": "<unchanged>", "answer": "<rewritten>"}}
  ]
}}
"""


class _RewrittenAnswer(BaseModel):
    question: str
    answer: str


class _Rewrite(BaseModel):
    cover_letter: str
    application_answers: list[_RewrittenAnswer] = []


async def humanize_package(
    client: QualifierClient,
    pkg: DraftPackage,
    *,
    voice_sample: str = "",
) -> tuple[DraftPackage, dict]:
    """Rewrite the pack's prose; keep the original if it doesn't improve.

    Returns (package, report) where report has before/after scores and
    which version was kept.
    """
    before = voice_report(package_text(pkg))
    system = SYSTEM_PROMPT.format(
        tells=", ".join(AI_TELLS[:24]),
        voice_sample=(voice_sample or "")[:2500],
    )
    user = json.dumps({
        "cover_letter": pkg.cover_letter,
        "application_answers": [
            {"question": a.question, "answer": a.answer}
            for a in pkg.application_answers
        ],
    }, indent=2)

    try:
        raw = await client.complete(system, user)
        rewrite = _Rewrite.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("humanize returned malformed JSON: %s", e)
        return pkg, {"before": before, "after": before, "kept": "original",
                     "note": "rewrite failed"}

    candidate = pkg.model_copy(deep=True)
    candidate.cover_letter = rewrite.cover_letter
    by_q = {a.question: a.answer for a in rewrite.application_answers}
    for a in candidate.application_answers:
        if a.question in by_q:
            a.answer = by_q[a.question]
            a.word_count = len(a.answer.split())

    after = voice_report(package_text(candidate))
    if after["score"] < before["score"]:
        return pkg, {"before": before, "after": after, "kept": "original",
                     "note": "rewrite scored worse"}
    return candidate, {"before": before, "after": after, "kept": "rewrite"}
