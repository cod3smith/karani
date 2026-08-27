"""Structured output from the qualification LLM call.

Strict JSON schema — the prompt asks the model to conform, and we validate
via pydantic here so garbage responses are rejected before hitting the DB.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QualStrength(BaseModel):
    claim: str = Field(..., description="Short assertion about fit.")
    evidence_from_resume: str = Field(
        ..., description="Direct quote or paraphrase from the resume."
    )


class QualGap(BaseModel):
    requirement: str = Field(..., description="The JD requirement not matched.")
    mitigation: str = Field(
        default="", description="How to bridge or reframe. May be empty."
    )
    severity: Literal["low", "medium", "high"] = "medium"


class QualificationResult(BaseModel):
    fit_score: int = Field(..., ge=0, le=100)
    verdict: Literal["qualified", "maybe", "skip"]
    strengths: list[QualStrength] = Field(default_factory=list)
    gaps: list[QualGap] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    why_apply: str = ""
    why_skip: str = ""
    recommended_positioning: str = ""
    # Provenance
    model: str = ""
    prompt_version: str = ""
    resume_hash: str = ""
    # Agent-mode provenance — empty in single-turn mode.
    evidence_gathered: list[str] = Field(
        default_factory=list,
        description="Brief log of tool calls the agent made, e.g. "
                    "'web_search(gitlab pay bands) → 5 results'",
    )
    tool_calls_made: int = 0
    agent_iterations: int = 0
