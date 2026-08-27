"""Central config for the karani ingestion pipeline.

Positioning: companies that hire *globally* at *SF pay bands* regardless of
where the candidate sits. That means:
  - Location-agnostic pay (single band worldwide, not location-adjusted)
  - Fully remote, no country restriction
  - Senior IC compensation floor ($160k+ base, ideally $200k+ TC)
  - Culture signals of retreats / travel budgets (nice-to-have, not a hard gate)

All secrets come from env vars. Never hardcode credentials here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load .env if present. Silent no-op if missing — env vars still work in prod.
load_dotenv(override=False)


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Compensation floor (Kelyn is targeting senior IC / staff bands) ---
    # Hard floor: below this we hard-fail. Roughly the low end of SF senior IC.
    min_comp_usd: int = _env_int("MIN_COMP_USD", 160_000)
    # Aspirational target — used for ranking, not a hard gate.
    target_comp_usd: int = _env_int("TARGET_COMP_USD", 220_000)

    # --- Global-hire eligibility signals ---
    # Phrases suggesting truly global hiring / anywhere-based candidates.
    # Applied with WORD BOUNDARIES in filters.py, so short substrings are safe.
    global_hire_positive_signals: tuple[str, ...] = (
        "hire globally", "hiring globally", "hire anywhere",
        "work from anywhere", "anywhere in the world",
        "remote — global", "remote - global", "remote (global)", "remote global",
        "remote worldwide", "remote — worldwide", "remote (worldwide)",
        "no location restrictions", "any location", "any country",
        "location: anywhere", "location: worldwide", "location: global",
        "fully distributed", "globally distributed",
        "we hire everywhere", "hire from anywhere",
    )
    # Phrases that VETO the posting for a Kenya-based candidate.
    regional_restriction_signals: tuple[str, ...] = (
        "us only", "u.s. only", "united states only", "usa only", "us-based only",
        "authorized to work in the us", "us work authorization",
        "must be a us citizen", "us citizens only",
        "must reside in the united states", "must be located in the united states",
        "eu only", "eu-based only", "uk only", "canada only", "canada-based only",
        "must reside in", "must be based in",
        "north america only", "americas only", "latam only",
        "eligible to work in the united states", "eligible to work in the us",
        "us employment authorization", "authorized to work in the united states",
    )
    # Location-independent pay = the single strongest positive signal.
    # If we see this, we know the SF-band thesis holds regardless of location.
    pay_parity_signals: tuple[str, ...] = (
        "same pay regardless of location", "location-independent pay",
        "no geographic pay differences", "no geo pay adjustments",
        "one comp band globally", "same salary worldwide", "same salary everywhere",
        "location-agnostic pay", "location agnostic compensation",
        "pay parity", "compensation parity",
        "salary is not adjusted for location", "location does not affect compensation",
        "we do not adjust for location",
    )

    # --- Remote signals ---
    remote_positive: tuple[str, ...] = (
        "remote", "work from anywhere", "distributed", "wfh", "fully remote",
    )
    remote_negative: tuple[str, ...] = (
        "on-site", "onsite", "in office", "in-office", "hybrid",
        "in-person required", "must be in office",
    )

    # --- Culture / benefits signals (nice-to-have, non-vetoing) ---
    travel_positive: tuple[str, ...] = (
        "annual retreat", "team retreat", "company offsite", "company off-site",
        "team offsite", "team off-site", "we fly", "flights to", "travel stipend",
        "coworking stipend", "conference budget", "conference travel",
        "quarterly retreats", "annual offsite", "annual meetup",
        "biannual retreat", "onsite retreat",
    )

    # --- Role scoping ---
    # RoleCategory values that pass the hard filter. Anything else is dropped
    # before it reaches LLM qualification.
    include_role_categories: tuple[str, ...] = (
        "software_engineering", "ml_ai", "data", "devops_sre", "security",
        "research", "engineering_leadership",
    )
    # Explicit title exclusions — cheap regex trigger words to hard-drop.
    excluded_title_terms: tuple[str, ...] = (
        "sales", "marketing", "recruiter", "recruiting", "talent",
        "customer success", "account executive", "account manager",
        "business development", "bdr", "sdr", "growth marketing",
        "human resources", "people operations", "content marketer",
        "community manager", "brand", "copywriter", "designer",
        "product designer", "ux designer", "ui designer",
        "finance", "accountant", "controller", "paralegal", "legal counsel",
        "operations manager", "office manager", "executive assistant",
        "customer support", "support engineer",  # customer support, not eng
    )

    # --- HTTP ---
    http_timeout_seconds: int = _env_int("HTTP_TIMEOUT", 30)
    http_concurrency: int = _env_int("HTTP_CONCURRENCY", 6)
    # Per-host cap prevents one slow ATS from starving the rest.
    http_per_host_concurrency: int = _env_int("HTTP_PER_HOST_CONCURRENCY", 3)
    user_agent: str = os.getenv(
        "USER_AGENT", "karani/0.2 (job-pipeline; contact: kelyn.njeri@gmail.com)"
    )

    # --- Storage ---
    database_url: str = os.getenv("DATABASE_URL", "")

    # --- Freshness / lifecycle ---
    cache_ttl_hours: int = _env_int("CACHE_TTL_HOURS", 4)
    stale_job_days: int = _env_int("STALE_JOB_DAYS", 10)  # not-seen → close

    # --- FX (approximate; refresh from an FX API for accuracy) ---
    fx_to_usd: dict[str, float] = field(default_factory=lambda: {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.27,
        "CAD": 0.73,
        "AUD": 0.66,
        "CHF": 1.13,
        "SEK": 0.096,
        "NOK": 0.093,
        "DKK": 0.145,
        "KES": 0.0077,
        "INR": 0.012,
        "SGD": 0.75,
    })


settings = Settings()
