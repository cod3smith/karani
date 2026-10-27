"""Pydantic models for the conversion ledger and the variant experiment."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Applications that carry no variant tag. A real bucket in the funnel —
# you want to see how the untagged majority is doing — but never an arm
# in the experiment, because nothing was deliberately varied.
UNASSIGNED = "unassigned"

VariantDimension = Literal["positioning", "draft_prompt", "resume_order",
                           "outreach"]


class StrategyVariant(BaseModel):
    """A named arm of the experiment.

    `guidance` is the text a drafter is meant to follow when this arm is
    assigned — it is what makes the arm a treatment rather than a label.
    An arm with empty guidance is a bookkeeping tag only, and the
    promotion report says so.
    """
    name: str
    dimension: VariantDimension = "positioning"
    description: str = ""
    guidance: str = ""
    active: bool = True
    created_at: datetime | None = None


class LedgerEntry(BaseModel):
    """One application, with everything needed to attribute its outcome.

    Written when an application is sent and amended when a reply arrives.
    `first_response_at` is deliberately separate from `outcome_at`: the
    thing karani can actually move is how fast and how often a human
    replies, not what they decide.
    """
    job_id: int
    company: str = ""
    company_display: str = ""
    title: str = ""
    source: str = ""
    fit_score: int | None = None
    comp_min_usd: int | None = None
    comp_max_usd: int | None = None

    variant: str | None = None
    positioning_angle: str | None = None
    positioning_text: str = ""

    qualify_prompt_version: str = ""
    qualify_model: str = ""
    draft_prompt_version: str = ""
    draft_model: str = ""
    documents: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    applied_at: datetime | None = None
    first_response_at: datetime | None = None
    first_response_kind: str = ""
    outcome: str | None = None
    outcome_at: datetime | None = None

    @property
    def responded(self) -> bool:
        return bool(self.first_response_at) or self.outcome in (
            "offer", "rejection", "declined")

    @property
    def days_to_first_response(self) -> float | None:
        if not (self.applied_at and self.first_response_at):
            return None
        delta = self.first_response_at - self.applied_at
        return round(max(0.0, delta.total_seconds() / 86400.0), 2)


class ArmStats(BaseModel):
    """A variant's record, always with its uncertainty attached."""
    name: str
    applied: int
    responded: int
    response_rate: float
    ci_low: float
    ci_high: float
    eligible: bool = False      # met the minimum sample
    has_guidance: bool = True   # the arm actually varies something

    @property
    def interval(self) -> tuple[float, float]:
        return (self.ci_low, self.ci_high)


class PromotionReport(BaseModel):
    """The answer to "which angle is working?", including "we can't tell"."""
    status: Literal["no_data", "insufficient_data", "no_clear_winner",
                    "promote"]
    winner: str | None = None
    arms: list[ArmStats] = Field(default_factory=list)
    min_sample_per_arm: int = 0
    headline: str = ""
    detail: str = ""


class Breakdown(BaseModel):
    """Conversion for one slice of the ledger (a source, a company, ...).

    `offers` is reported because it is asked for, not because it is
    optimizable — see the design doc, section 4. The rate the loop
    actually steers is `response_rate`.
    """
    key: str
    applied: int = 0
    responded: int = 0
    offers: int = 0
    response_rate: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 1.0
    median_days_to_response: float | None = None


class TimeToResponse(BaseModel):
    """How long a reply takes when one comes, and how much is still open."""
    n: int = 0
    awaiting: int = 0
    median_days: float | None = None
    p75_days: float | None = None
    fastest_days: float | None = None
    slowest_days: float | None = None
    note: str = ""


class FitScale(BaseModel):
    """One scorer's fit distribution.

    A fit score is a number produced by a specific model under a
    specific prompt version, and the scales do not line up: measured
    over 598 rows in 2026-10, Haiku 5.5 averaged 38 with 2 rows above
    80 while qwen3:4b averaged 47 with 31 above 80. Treating "85" as
    one quantity across scorers is how a calibrated threshold silently
    stops matching anything.
    """
    qualify_model: str
    qualify_prompt_version: str
    n: int = 0
    mean_fit: float | None = None
    min_fit: int | None = None
    max_fit: int | None = None
    bands: list[Breakdown] = Field(default_factory=list)

    @property
    def label(self) -> str:
        return (f"{self.qualify_model or 'unknown'}"
                f"@{self.qualify_prompt_version or 'unknown'}")


class ConversionReport(BaseModel):
    """The whole measurement surface over the ledger, in one object."""
    totals: Breakdown = Field(default_factory=lambda: Breakdown(key="all"))
    by_variant: list[Breakdown] = Field(default_factory=list)
    by_positioning_angle: list[Breakdown] = Field(default_factory=list)
    by_source: list[Breakdown] = Field(default_factory=list)
    by_company: list[Breakdown] = Field(default_factory=list)
    by_comp_band: list[Breakdown] = Field(default_factory=list)
    # Pooled ONLY when every application was scored on the same scale.
    # Empty, with `scale_warning` set, when more than one scorer is in
    # the data — see FitScale.
    by_fit_band: list[Breakdown] = Field(default_factory=list)
    fit_scales: list[FitScale] = Field(default_factory=list)
    scale_warning: str = ""

    by_qualify_model: list[Breakdown] = Field(default_factory=list)
    by_qualify_prompt: list[Breakdown] = Field(default_factory=list)
    by_draft_model: list[Breakdown] = Field(default_factory=list)
    by_draft_prompt: list[Breakdown] = Field(default_factory=list)

    time_to_first_response: TimeToResponse = Field(
        default_factory=TimeToResponse)
    recurring_gaps: list[tuple[str, int]] = Field(default_factory=list)
    promotion: PromotionReport = Field(
        default_factory=lambda: PromotionReport(status="no_data"))
    # Things that varied alongside the arms, so a difference between
    # arms cannot be attributed to the arms.
    confounds: list[str] = Field(default_factory=list)
    confidence: str = ""


__all__ = ["UNASSIGNED", "VariantDimension", "StrategyVariant", "LedgerEntry",
           "ArmStats", "PromotionReport", "Breakdown", "TimeToResponse",
           "FitScale", "ConversionReport"]
