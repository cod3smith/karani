"""Normalized data model. Every source produces Job objects."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Source(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    REMOTEOK = "remoteok"
    HIMALAYAS = "himalayas"
    WEWORKREMOTELY = "weworkremotely"
    REMOTIVE = "remotive"
    WORKABLE = "workable"
    AIJOBS = "aijobs"


class RemoteStatus(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class RoleCategory(str, Enum):
    SOFTWARE_ENGINEERING = "software_engineering"
    ML_AI = "ml_ai"
    DATA = "data"
    DEVOPS_SRE = "devops_sre"
    SECURITY = "security"
    RESEARCH = "research"
    ENGINEERING_LEADERSHIP = "engineering_leadership"
    PRODUCT = "product"
    DESIGN = "design"
    SALES_MARKETING = "sales_marketing"
    OPERATIONS = "operations"
    OTHER = "other"


class Seniority(str, Enum):
    INTERN = "intern"
    JUNIOR = "junior"        # entry / new-grad / associate / L3
    MID = "mid"              # L4
    SENIOR = "senior"        # L5 / sr.
    STAFF = "staff"          # L6+
    PRINCIPAL = "principal"  # L7+ / distinguished
    LEAD = "lead"            # people-lead / tech lead
    MANAGER = "manager"      # EM / director / VP
    UNKNOWN = "unknown"


# Normalization for canonical hash / dedup — collapse whitespace, drop
# non-content punctuation, lowercase.
_NORMALIZE_WHITESPACE = re.compile(r"\s+")
_STRIP_NONCONTENT = re.compile(r"[^\w\s]+")


def _normalize_for_hash(text: str) -> str:
    text = text.lower()
    text = _STRIP_NONCONTENT.sub(" ", text)
    text = _NORMALIZE_WHITESPACE.sub(" ", text)
    return text.strip()


class Job(BaseModel):
    """Normalized job posting. Every source funnels into this."""

    source: Source
    source_id: str
    company: str  # board slug or normalized handle
    company_display: str  # human-readable

    title: str
    department: str | None = None
    team: str | None = None

    location_raw: str = ""
    location_normalized: list[str] = Field(
        default_factory=list, description="Lowercased country/region tokens"
    )
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN

    employment_type: str | None = None  # full_time, contract, etc.

    description_html: str = ""
    description_text: str = ""  # stripped, for LLM

    apply_url: str

    posted_at: datetime | None = None

    comp_min_usd: int | None = None
    comp_max_usd: int | None = None
    comp_currency_original: str | None = None
    comp_disclosed: bool = False

    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    # Meaningful, normalized hash. Cosmetic edits (typos, whitespace) don't
    # trigger LLM re-qualification.
    content_hash: str = ""
    # Cross-source dedup key: (company, title, week). Same slot across boards
    # → we can suppress duplicate qualification work.
    canonical_hash: str = ""

    def compute_hash(self) -> str:
        payload = (
            f"{_normalize_for_hash(self.title)}|"
            f"{_normalize_for_hash(self.description_text)}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def compute_canonical_hash(self) -> str:
        """Company + title + posted-week bucket. Cross-source dedup key."""
        week_bucket = ""
        if self.posted_at:
            iso = self.posted_at.isocalendar()
            week_bucket = f"{iso.year}W{iso.week:02d}"
        payload = (
            f"{_normalize_for_hash(self.company_display or self.company)}|"
            f"{_normalize_for_hash(self.title)}|"
            f"{week_bucket}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def finalize(self) -> "Job":
        if not self.content_hash:
            self.content_hash = self.compute_hash()
        if not self.canonical_hash:
            self.canonical_hash = self.compute_canonical_hash()
        return self


class PreFilterResult(BaseModel):
    """Cheap deterministic pre-filter output. Runs before any LLM call."""

    # Role
    role_category: RoleCategory
    role_category_confidence: float = 0.0  # 0..1
    seniority: Seniority = Seniority.UNKNOWN

    # Geo / hire eligibility
    global_hire_eligible: Literal["likely_yes", "likely_no", "unclear"]
    hire_evidence: str = ""

    # Remote
    fully_remote: Literal["yes", "hybrid", "no", "unclear"]

    # Comp
    comp_disclosed: bool
    comp_min_usd: int | None = None
    meets_comp_floor: bool | None = None
    meets_target_comp: bool | None = None

    # Pay parity — the SF-band-regardless-of-location signal
    pay_parity: Literal["likely_yes", "unclear"] = "unclear"
    pay_parity_evidence: str = ""

    # Relocation/visa sponsorship — softens geo and onsite vetos (EU and
    # Japan destinations especially attractive)
    relocation_support: Literal["likely_yes", "unclear"] = "unclear"
    relocation_evidence: str = ""

    # Culture (nice-to-have; never vetos)
    travel_benefits: Literal["likely_yes", "likely_no", "unclear"] = "unclear"
    travel_evidence: str = ""

    # Skill/profile match — % of user must-haves observed in description
    skill_overlap: float = 0.0
    matched_must_haves: list[str] = Field(default_factory=list)

    # Overall
    pass_hard_filters: bool
    reasons_failed: list[str] = Field(default_factory=list)
    # Integer score for ranking rows that pass hard gates.
    score: int = 0
