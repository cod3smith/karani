"""Measurement layer: the conversion ledger, variant experiment and coach.

The honest form of "karani improves itself": an evaluation harness over
recorded outcomes, not an agent that rewrites its own prompts. It
optimizes what is controllable — which roles are targeted, which angle
the pack leads with, reply rate, time to first response — and says so
about the rest. See `docs/adrs/0022-conversion-ledger-and-variant-
promotion.md` and section 4 of the career-runner design doc.
"""
from __future__ import annotations

from .funnel import comp_band, conversion_report
from .ledger import ConversionLedger, entry_from_job
from .models import (UNASSIGNED, ArmStats, Breakdown, ConversionReport,
                     FitScale, LedgerEntry, PromotionReport, StrategyVariant,
                     TimeToResponse)
from .registry import VariantRegistry
from .review import CoachReview, build_review, render_markdown, weekly_review
from .stats import median, proportion, wilson_interval
from .variants import MIN_SAMPLE_PER_ARM, promotion_report

__all__ = [
    "UNASSIGNED", "MIN_SAMPLE_PER_ARM",
    "ArmStats", "Breakdown", "CoachReview", "ConversionLedger",
    "ConversionReport", "FitScale", "LedgerEntry", "PromotionReport",
    "StrategyVariant", "TimeToResponse", "VariantRegistry",
    "build_review", "comp_band", "conversion_report", "entry_from_job",
    "median", "promotion_report", "proportion", "render_markdown",
    "weekly_review", "wilson_interval",
]
