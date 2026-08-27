"""Autopilot — the continuous hunt (ADR 0012).

Drafts application packs for top-fit qualified roles without waiting for
a verdict, and delivers them to Slack with review buttons. Guardrails:
fit floor (AUTOPILOT_MIN_FIT), per-run draft cap (AUTOPILOT_MAX_DRAFTS),
and the hard non-goal — karani never submits an application.
"""
from __future__ import annotations

from .runner import run_autopilot

__all__ = ["run_autopilot"]
