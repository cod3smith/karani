"""The merged candidate profile (Phase 1 of the career-runner design).

One record of the career, assembled from every source the candidate
has, with the resume winning conflicts because it is the document they
curate. The merged profile renders into the STABLE half of the prompt
(ADR 0019) alongside the career transcript, so it is cached and costs
~0.1x of input on every row after the first.

Sources are all optional and all degrade to nothing: no LinkedIn
export, no portfolio URL, or an unreachable site means thinner
tailoring, never a failed hunt.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .model import Education, Position, Profile, Project
from .portfolio import fetch_portfolio, html_to_text
from .resume_source import parse_resume

log = logging.getLogger(__name__)

__all__ = ["Profile", "Position", "Project", "Education", "load_profile",
           "profile_context", "fetch_portfolio", "html_to_text",
           "parse_resume", "linkedin_export_path"]


def linkedin_export_path() -> Path | None:
    """`[profile] linkedin_export` in karani.toml, or data/linkedin/."""
    from karani.config import get_config
    configured = os.getenv("KARANI_LINKEDIN_EXPORT") or \
        getattr(get_config().profile_sources, "linkedin_export", "")
    if configured:
        path = Path(configured)
        return path if path.exists() else None
    for guess in (Path("data/linkedin"), Path("data/linkedin-export.zip")):
        if guess.exists():
            return guess
    return None


def load_profile(resume_markdown: str = "") -> Profile:
    """Merge every available source. Resume first: it wins conflicts."""
    profile = parse_resume(resume_markdown) if resume_markdown else Profile()

    export = linkedin_export_path()
    if export:
        try:
            from .linkedin import parse_export
            profile = profile.merged_with(parse_export(export))
        except Exception:
            log.warning("LinkedIn export at %s could not be read", export,
                        exc_info=True)
    return profile


def profile_context(profile: Profile, portfolio_text: str = "") -> str:
    """Prompt-ready text for the cached prefix. Empty when there is
    nothing beyond the resume — no point paying for an empty block."""
    if not profile.positions and not profile.skills and not portfolio_text:
        return ""

    parts: list[str] = []
    if profile.headline:
        parts.append(f"Headline: {profile.headline}")
    if profile.skills:
        parts.append("Skills claimed on LinkedIn: "
                     + ", ".join(profile.skills[:60]))
    if profile.positions:
        lines = []
        for p in profile.positions:
            span = " - ".join(x for x in (p.started, p.ended or "present") if x)
            head = f"- {p.title or 'role'} at {p.company}"
            lines.append(f"{head} ({span})" if span else head)
            if p.description:
                lines.append(f"    {p.description[:600]}")
        parts.append("Positions on record:\n" + "\n".join(lines))
    if profile.projects:
        parts.append("Projects:\n" + "\n".join(
            f"- {p.name}: {p.description[:300]}" for p in profile.projects))
    if portfolio_text:
        parts.append("From the candidate's own site:\n"
                     + portfolio_text[:6000])

    return ("<profile>\nMerged from " + ", ".join(profile.sources or ("resume",))
            + ". Supplements the resume with detail it compressed away; "
              "never contradict the resume's dates or titles.\n\n"
            + "\n\n".join(parts) + "\n</profile>")
