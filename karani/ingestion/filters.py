"""Cheap deterministic pre-filter. Runs before LLM qualification.

Goals:
- Hard-drop obvious rejects (wrong role, wrong seniority, region-locked, comp too low)
- Extract structured signals the LLM will refine later
- Never false-negative on a plausible senior/staff engineering role at SF pay
"""
from __future__ import annotations

import re
from functools import lru_cache

from .config import settings
from .models import (
    Job, PreFilterResult, RemoteStatus, Seniority,
)
from .profile import UserProfile, DEFAULT_PROFILE
from .roles import classify


# --- Signal matching with word boundaries ---

@lru_cache(maxsize=256)
def _compile_signal(signal: str) -> re.Pattern[str]:
    # Use `re.escape` + word-boundary anchors. Signals can contain spaces —
    # \b anchors around alphanumerics only, which handles those correctly.
    return re.compile(rf"(?i)\b{re.escape(signal)}\b")


def _find_signal(text: str, signals: tuple[str, ...]) -> str | None:
    """Return the first matched signal or None. Word-boundary anchored."""
    for s in signals:
        if _compile_signal(s).search(text):
            return s
    return None


def _snippet(text: str, needle: str, window: int = 80) -> str:
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(needle) + window)
    return "..." + text[start:end].strip() + "..."


# --- Comp extraction ---

# All patterns require a currency/comp anchor within 40 chars, checked below,
# so "5-10 years" and "5 to 10 candidates" don't get parsed as $5k-$10k.
# Range separator: hyphen family, "to", or "and" (as in "between X and Y").
_SEP = r"(?:[-–—]|to|and)"

_COMP_PATTERNS: tuple[re.Pattern[str], ...] = (
    # $140k-$180k, $140k to $180k, 140K–180K
    re.compile(rf"\$?\s*(\d{{2,3}})\s*[kK]\s*{_SEP}\s*\$?\s*(\d{{2,3}})\s*[kK]"),
    # $140,000-$180,000  ($ optional; anchor requirement handles false-positives)
    re.compile(rf"\$?\s*(\d{{2,3}}(?:,\d{{3}}))\s*{_SEP}\s*\$?\s*(\d{{2,3}}(?:,\d{{3}}))"),
    # 140000-180000 or 140000 to 180000 (comma-optional)
    re.compile(rf"(\d{{5,6}})\s*{_SEP}\s*(\d{{5,6}})"),
    # $150k+, 200K+
    re.compile(r"\$?\s*(\d{2,3})\s*[kK]\s*\+"),
)

_COMP_ANCHOR = re.compile(
    r"(?i)(?:\$|usd|eur|gbp|salary|salaries|comp(?:ensation)?|"
    r"base pay|base salary|otr?e?|package|total comp|band|range)"
)


def _parse_comp(text: str) -> tuple[int | None, int | None, bool]:
    """Return (min_usd, max_usd, disclosed).

    Only parses matches that sit within 40 chars of a comp-anchor keyword.
    That kills false positives on "5-10 years experience" etc.
    """
    for pat in _COMP_PATTERNS:
        for m in pat.finditer(text):
            window_start = max(0, m.start() - 40)
            window_end = min(len(text), m.end() + 40)
            window = text[window_start:window_end]
            if not _COMP_ANCHOR.search(window):
                continue
            try:
                groups = m.groups()
                if len(groups) == 2:
                    lo = int(groups[0].replace(",", ""))
                    hi = int(groups[1].replace(",", ""))
                    if lo < 1000:  # 'k' shorthand
                        lo *= 1000
                        hi *= 1000
                    if not _plausible(lo, hi):
                        continue
                    return lo, hi, True
                if len(groups) == 1:
                    lo = int(groups[0].replace(",", ""))
                    if lo < 1000:
                        lo *= 1000
                    if not _plausible(lo, None):
                        continue
                    return lo, None, True
            except ValueError:
                continue
    return None, None, False


def _plausible(lo: int, hi: int | None) -> bool:
    """Reject obvious garbage — sub-$20k floors, or absurd bands."""
    if lo < 20_000 or lo > 2_000_000:
        return False
    if hi is not None and (hi < lo or hi > 3_000_000):
        return False
    return True


# --- Skill overlap ---

def _skill_overlap(text: str, must_haves: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for skill in must_haves:
        if _compile_signal(skill).search(text):
            matches.append(skill)
    return matches


def _nice_to_have_hits(text: str, nice: tuple[str, ...]) -> int:
    return sum(1 for skill in nice if _compile_signal(skill).search(text))


# --- Main filter ---

def pre_filter(job: Job, profile: UserProfile | None = None) -> PreFilterResult:
    profile = profile or DEFAULT_PROFILE
    haystack = f"{job.title}\n{job.location_raw}\n{job.description_text}"

    # --- 1. Role classification ---
    cls = classify(job.title, job.tags, job.description_text)
    role_category = cls.category
    seniority = cls.seniority

    reasons: list[str] = []
    if role_category not in profile.target_categories:
        reasons.append(f"role category {role_category.value} not in target set")

    # Title-based exclusions (belt + suspenders).
    for term in settings.excluded_title_terms:
        if _compile_signal(term).search(job.title):
            reasons.append(f"title contains excluded term: {term}")
            break

    # --- 2. Seniority ---
    if seniority != Seniority.UNKNOWN and seniority not in profile.seniority_bands:
        reasons.append(f"seniority {seniority.value} not in target bands")

    # --- 3. Global-hire eligibility ---
    # Relocation/visa sponsorship softens geo vetos: a region-locked role
    # that will relocate the candidate (EU/Japan especially) goes to the
    # LLM as "unclear" instead of being hard-dropped.
    relocation_hit = _find_signal(haystack, settings.relocation_signals)
    relocation_ev = _snippet(haystack, relocation_hit) if relocation_hit else ""

    neg = _find_signal(haystack, settings.regional_restriction_signals)
    pos = _find_signal(haystack, settings.global_hire_positive_signals)
    if neg and not pos and relocation_hit:
        hire = "unclear"
        hire_ev = (f"region-locked ('{neg}') but relocation/visa support: "
                   f"{relocation_ev}")
    elif neg and not pos:
        hire = "likely_no"
        hire_ev = _snippet(haystack, neg)
        reasons.append(f"regional restriction: {hire_ev}")
    elif pos and not neg:
        hire = "likely_yes"
        hire_ev = _snippet(haystack, pos)
    elif pos and neg:
        # Both signals — ambiguous, LLM decides.
        hire = "unclear"
        hire_ev = f"conflict: '{pos}' vs '{neg}'"
    else:
        hire = "unclear"
        hire_ev = ""

    # --- 4. Remote ---
    if job.remote_status == RemoteStatus.REMOTE:
        remote = "yes"
    elif job.remote_status == RemoteStatus.HYBRID:
        remote = "hybrid"
    elif job.remote_status == RemoteStatus.ONSITE:
        remote = "no"
    else:
        rp = _find_signal(haystack, settings.remote_positive)
        rn = _find_signal(haystack, settings.remote_negative)
        if rp and not rn:
            remote = "yes"
        elif rn and not rp:
            remote = "no"
        elif rp and rn:
            remote = "hybrid"
        else:
            remote = "unclear"

    if remote == "no" and not relocation_hit:
        reasons.append("onsite required")
    elif remote == "hybrid" and not relocation_hit:
        # Kelyn is Kenya-based — hybrid roles are functionally onsite unless
        # the company relocates him or the LLM sees an override.
        reasons.append("hybrid required")

    # --- 5. Comp ---
    if job.comp_min_usd:
        comp_min = job.comp_min_usd
        comp_disclosed = True
    else:
        comp_min, _, comp_disclosed = _parse_comp(haystack)

    meets_floor: bool | None = None
    meets_target: bool | None = None
    if comp_disclosed and comp_min is not None:
        meets_floor = comp_min >= settings.min_comp_usd
        meets_target = comp_min >= settings.target_comp_usd
        if not meets_floor:
            reasons.append(
                f"comp ${comp_min:,} below ${settings.min_comp_usd:,} floor"
            )

    # --- 6. Pay parity (SF-band regardless of location) ---
    parity_hit = _find_signal(haystack, settings.pay_parity_signals)
    if parity_hit:
        pay_parity = "likely_yes"
        parity_ev = _snippet(haystack, parity_hit)
    else:
        pay_parity = "unclear"
        parity_ev = ""

    # --- 7. Culture ---
    tp = _find_signal(haystack, settings.travel_positive)
    if tp:
        travel = "likely_yes"
        travel_ev = _snippet(haystack, tp)
    else:
        travel = "unclear"
        travel_ev = ""

    # --- 8. Skill overlap ---
    matched = _skill_overlap(job.description_text, profile.must_have_any)
    overlap_pct = len(matched) / max(1, len(profile.must_have_any))
    if len(matched) < profile.min_skill_overlap:
        reasons.append(
            f"no must-have skill detected (need {profile.min_skill_overlap})"
        )
    nice_hits = _nice_to_have_hits(job.description_text, profile.nice_to_have)

    # --- 9. Score (for ranking passing rows) ---
    score = 0
    score += 40 if meets_target else 15 if meets_floor else 0
    score += 20 if pay_parity == "likely_yes" else 0
    score += 15 if hire == "likely_yes" else 0
    score += 10 if relocation_hit else 0
    score += 10 if travel == "likely_yes" else 0
    score += min(15, nice_hits * 2)
    score += 5 if seniority in {Seniority.SENIOR, Seniority.STAFF,
                                 Seniority.PRINCIPAL} else 0

    return PreFilterResult(
        role_category=role_category,
        role_category_confidence=cls.confidence,
        seniority=seniority,
        global_hire_eligible=hire,
        hire_evidence=hire_ev,
        fully_remote=remote,
        comp_disclosed=comp_disclosed,
        comp_min_usd=comp_min,
        meets_comp_floor=meets_floor,
        meets_target_comp=meets_target,
        pay_parity=pay_parity,
        pay_parity_evidence=parity_ev,
        relocation_support="likely_yes" if relocation_hit else "unclear",
        relocation_evidence=relocation_ev,
        travel_benefits=travel,
        travel_evidence=travel_ev,
        skill_overlap=overlap_pct,
        matched_must_haves=matched,
        pass_hard_filters=len(reasons) == 0,
        reasons_failed=reasons,
        score=score,
    )
