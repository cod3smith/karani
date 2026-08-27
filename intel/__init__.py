"""Company intelligence — cached public-signal dossiers.

One fetch, many consumers: agent-mode qualification, interview prep,
follow-up drafting, and warm-path outreach all read the same cached
dossier instead of re-probing the web per decision.
"""
from __future__ import annotations

from .service import dossier_text, find_warm_paths, get_company_intel

__all__ = ["get_company_intel", "find_warm_paths", "dossier_text"]
