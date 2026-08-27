"""Structured output for the drafting LLM call."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TailoredBullet(BaseModel):
    original_role: str = Field(..., description="Company/title the bullet comes from")
    text: str = Field(..., description="The bullet as it should appear on the tailored resume")
    why_this_bullet: str = Field(default="", description="1-line rationale for surfacing this bullet")


class ApplicationAnswer(BaseModel):
    question: str
    answer: str  # 80–250 words typical
    word_count: int = 0


class DraftPackage(BaseModel):
    cover_letter: str = Field(..., description="Full cover letter, ~250-350 words")
    tone_note: str = Field(default="", description="1 line on the voice used")
    tailored_bullets: list[TailoredBullet] = Field(default_factory=list)
    application_answers: list[ApplicationAnswer] = Field(default_factory=list)
    subject_line: str = Field(default="", description="For cold emails / follow-ups")
    positioning_summary: str = Field(
        default="", description="1-paragraph positioning to lead the app with"
    )
    # Provenance
    model: str = ""
    prompt_version: str = ""
    job_id: int = 0
    verdict_at_draft: Literal["qualified", "maybe", "skip", "unknown"] = "unknown"
    # ATS keyword coverage of the final materials (see keywords.py).
    keyword_coverage: float | None = None
    keyword_missing: list[str] = Field(default_factory=list)
