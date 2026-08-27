"""Deterministic role classifier + seniority extractor.

Runs on title + tags + first 500 chars of description. Cheap, no LLM.
Order matters: more specific categories are checked first so that
"ML Platform Engineer" classifies as ML_AI, not DEVOPS_SRE.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import RoleCategory, Seniority


# --- Compiled patterns (word-boundary anchored) ---

def _wb(*terms: str) -> re.Pattern[str]:
    """Compile a word-boundary regex OR-ing the given terms (case-insensitive)."""
    escaped = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"(?i)\b(?:{escaped})\b")


# Category patterns — check in this order (most specific first).
_CATEGORY_PATTERNS: tuple[tuple[RoleCategory, re.Pattern[str]], ...] = (
    # Sales / marketing / etc. — check first so we can early-reject.
    (RoleCategory.SALES_MARKETING, _wb(
        "sales", "marketing", "growth marketer", "account executive",
        "account manager", "business development", "bdr", "sdr",
        "customer success", "customer support", "brand", "copywriter",
        "content marketer", "community manager", "recruiter", "recruiting",
        "talent partner",
    )),
    (RoleCategory.OPERATIONS, _wb(
        "operations manager", "office manager", "executive assistant",
        "people operations", "human resources", "hr business partner",
        "finance", "accountant", "controller", "paralegal", "legal counsel",
    )),
    (RoleCategory.DESIGN, _wb(
        "product designer", "ux designer", "ui designer", "brand designer",
        "visual designer", "design lead", "design manager",
    )),
    (RoleCategory.PRODUCT, _wb(
        "product manager", "product owner", "group product manager",
        "principal product manager", "senior product manager",
    )),

    # Engineering — specific first, generic last.
    (RoleCategory.ML_AI, _wb(
        "machine learning engineer", "ml engineer", "mle",
        "ai engineer", "applied scientist", "applied ai",
        "research engineer", "ml scientist", "ai researcher",
        "computer vision", "nlp engineer", "llm engineer",
        "ml infrastructure", "ml platform", "ml ops", "mlops",
        "deep learning engineer", "generative ai",
    )),
    (RoleCategory.RESEARCH, _wb(
        "research scientist", "computational biologist",
        "computational biology", "bioinformatics scientist",
        "scientific software engineer",
    )),
    (RoleCategory.DATA, _wb(
        "data engineer", "analytics engineer", "data platform",
        "data infrastructure", "data infra",
        "data scientist", "senior data scientist", "staff data scientist",
    )),
    (RoleCategory.DEVOPS_SRE, _wb(
        "sre", "site reliability", "devops", "platform engineer",
        "infrastructure engineer", "cloud engineer", "kubernetes engineer",
        "reliability engineer", "systems engineer",
    )),
    (RoleCategory.SECURITY, _wb(
        "security engineer", "application security", "appsec",
        "product security", "offensive security", "red team",
        "detection engineer", "security architect",
    )),
    (RoleCategory.ENGINEERING_LEADERSHIP, _wb(
        "engineering manager", "director of engineering", "vp engineering",
        "vp of engineering", "head of engineering", "cto",
        "engineering director", "senior engineering manager",
    )),
    # Catch-all engineering — broadest, checked last so specialists win.
    (RoleCategory.SOFTWARE_ENGINEERING, _wb(
        "software engineer", "swe", "backend engineer", "frontend engineer",
        "full stack engineer", "full-stack engineer", "fullstack engineer",
        "developer", "programmer", "web engineer", "mobile engineer",
        "ios engineer", "android engineer", "engineer",  # last resort
    )),
)


# Seniority extraction — check most-specific bands first.
_SENIORITY_PATTERNS: tuple[tuple[Seniority, re.Pattern[str]], ...] = (
    (Seniority.INTERN, _wb("intern", "internship")),
    (Seniority.JUNIOR, _wb(
        "junior", "jr", "entry level", "entry-level", "new grad", "new-grad",
        "graduate", "associate", "apprentice",
    )),
    (Seniority.PRINCIPAL, _wb("principal", "distinguished")),
    (Seniority.STAFF, _wb("staff")),
    (Seniority.SENIOR, _wb("senior", "sr", "sr.")),
    (Seniority.LEAD, _wb("lead", "tech lead", "technical lead")),
    (Seniority.MANAGER, _wb(
        "manager", "director", "head of", "vp", "vice president", "cto",
    )),
    (Seniority.MID, _wb("mid-level", "mid level", "intermediate")),
)


@dataclass
class RoleClassification:
    category: RoleCategory
    confidence: float  # 0..1
    seniority: Seniority


def classify(title: str, tags: list[str] | None = None,
             description: str | None = None) -> RoleClassification:
    """Classify a job. Bias toward title/tags — description is a tiebreaker only."""
    tags = tags or []
    tag_bag = " ".join(tags)
    # Description sample only — full descriptions add noise, e.g. "we sell..."
    desc_sample = (description or "")[:500]

    # Title carries the most weight.
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(title):
            confidence = 1.0
            return RoleClassification(
                category=category,
                confidence=confidence,
                seniority=_extract_seniority(title),
            )

    # Fall back to tags (RemoteOK, Himalayas), then a small window of desc.
    fallback_text = f"{tag_bag}\n{desc_sample}"
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(fallback_text):
            return RoleClassification(
                category=category,
                confidence=0.55,
                seniority=_extract_seniority(title),
            )

    return RoleClassification(
        category=RoleCategory.OTHER,
        confidence=0.0,
        seniority=_extract_seniority(title),
    )


def _extract_seniority(title: str) -> Seniority:
    for level, pattern in _SENIORITY_PATTERNS:
        if pattern.search(title):
            return level
    return Seniority.UNKNOWN
