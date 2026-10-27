"""Comp research for the band — derived, never generated.

A prep pack exists to be repeated out loud. Every other section can
survive a model being approximately right; a salary number cannot,
because the candidate will quote it in a negotiation and be asked
where it came from. So this follows the `karani/legal.py` discipline:
the numbers are arithmetic over facts karani already holds, and the
model receives them as resolved input it may phrase around but never
originate.

Three sources, all local:

- the **posted band** on the job row,
- the candidate's **declared floor and target** (`MIN_COMP_USD` /
  `TARGET_COMP_USD`, karani.toml `[hunt]`),
- a **benchmark** over comparable postings karani has already ingested
  — same role category and seniority, comp disclosed. That is a sample
  of advertised bands, not a market survey, and the rendered brief says
  so rather than dressing it up as Levels.fyi.

Shape matters: a globally-remote role is judged against SF bands, a
relocation role against local top-of-market in the destination, which
karani has no data for. The brief says which case it is instead of
quietly comparing the wrong things.
"""
from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# A benchmark drawn from a handful of postings is noise. Below this we
# still report n, but we refuse to present percentiles as a band.
MIN_SAMPLE = 3


class CompBrief(BaseModel):
    posted_min_usd: int | None = None
    posted_max_usd: int | None = None
    floor_usd: int = 0
    target_usd: int = 0
    # Benchmark over comparable postings in karani's own pipeline.
    sample_size: int = 0
    p25_usd: int | None = None
    median_usd: int | None = None
    p75_usd: int | None = None
    # global_remote | relocation | unclear — decides which band applies.
    shape: str = "unclear"
    role_country: str | None = None
    anchor_usd: int = 0
    below_floor: bool = False
    notes: list[str] = Field(default_factory=list)


def _midpoint(row: dict) -> int | None:
    lo, hi = row.get("comp_min_usd"), row.get("comp_max_usd")
    if lo and hi:
        return int((lo + hi) / 2)
    return int(lo) if lo else None


def _percentile(values: list[int], fraction: float) -> int | None:
    """Nearest-rank. Small samples make interpolation a false precision."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def _shape(job_row: dict) -> tuple[str, str | None]:
    """Which comp band this role is judged against (ADR 0021 / rule 4.1).

    Reuses the deterministic work-authorization resolver rather than
    re-deriving geography: a role with no work location is the
    global-remote shape, a relocation role is judged locally.
    """
    try:
        from karani.config import get_config
        from karani.legal import resolve_work_authorization
        answers = resolve_work_authorization(job_row, get_config().legal)
    except Exception:
        log.warning("could not resolve role shape for comp", exc_info=True)
        return "unclear", None
    if answers.authorized_in_role_country is None:
        return "global_remote", None
    if answers.requires_sponsorship and answers.willing_to_relocate:
        return "relocation", answers.role_country
    return "unclear", answers.role_country


async def comp_samples(storage, job_row: dict) -> list[int]:
    """Midpoints of disclosed bands for comparable postings."""
    role = job_row.get("role_category")
    seniority = job_row.get("seniority")
    try:
        rows = await storage.active_jobs()
    except Exception:
        log.warning("comp benchmark unavailable", exc_info=True)
        return []
    out: list[int] = []
    for row in rows:
        if row.get("id") == job_row.get("id"):
            continue
        if role and row.get("role_category") != role:
            continue
        if seniority and row.get("seniority") != seniority:
            continue
        mid = _midpoint(row)
        if mid:
            out.append(mid)
    return out


async def comp_brief(storage, job_row: dict) -> CompBrief:
    from karani.ingestion.config import settings

    floor = int(settings.min_comp_usd)
    target = int(settings.target_comp_usd)
    posted_min = job_row.get("comp_min_usd") or None
    posted_max = job_row.get("comp_max_usd") or None
    shape, country = _shape(job_row)

    samples = await comp_samples(storage, job_row)
    brief = CompBrief(
        posted_min_usd=int(posted_min) if posted_min else None,
        posted_max_usd=int(posted_max) if posted_max else None,
        floor_usd=floor, target_usd=target,
        sample_size=len(samples), shape=shape, role_country=country,
    )
    if len(samples) >= MIN_SAMPLE:
        brief.p25_usd = _percentile(samples, 0.25)
        brief.median_usd = _percentile(samples, 0.50)
        brief.p75_usd = _percentile(samples, 0.75)

    # Anchor: the top of the posted band when it clears the target,
    # otherwise the target itself. Never below the target — that is the
    # number the whole pipeline is filtered on (rule 4.1).
    brief.anchor_usd = max(target, int(posted_max or 0))
    brief.below_floor = bool(posted_max and int(posted_max) < floor)

    notes: list[str] = []
    if not posted_min and not posted_max:
        notes.append("Comp is not disclosed on this posting. Ask for the "
                     "band before giving a number; if pushed, cite the "
                     f"target of ${target:,} as a requirement, not a wish.")
    elif brief.below_floor:
        notes.append(
            f"The posted maximum (${int(posted_max):,}) is below the "
            f"${floor:,} floor this pipeline filters on. Treat it as a "
            f"decline unless equity, relocation or title closes the gap.")
    elif posted_max and int(posted_max) < target:
        notes.append(
            f"The posted band tops out below the ${target:,} target. "
            f"Worth a conversation, but open with the gap rather than "
            f"discovering it at offer stage.")
    else:
        notes.append(f"Anchor at ${brief.anchor_usd:,} and let them "
                     f"counter. The floor is ${floor:,}.")

    if shape == "global_remote":
        notes.append("Global-remote shape: the band is the company's own "
                     "band, not a local one. Do not accept a "
                     "location-adjusted discount.")
    elif shape == "relocation":
        where = f" in {country}" if country else ""
        notes.append(f"Relocation shape: judge the offer against local "
                     f"top-of-market{where}, and price relocation support, "
                     f"visa costs and the first year's tax position "
                     f"separately. karani has no local benchmark for this.")
    else:
        notes.append("Role geography is unclear — establish where the job "
                     "actually sits before discussing numbers.")

    if brief.sample_size < MIN_SAMPLE:
        notes.append("Too few comparable postings in karani's pipeline for "
                     "a benchmark; the numbers above are the posting's and "
                     "your own.")
    brief.notes = notes
    return brief


def _usd(value: int | None) -> str:
    return f"${value:,}" if value else "not disclosed"


def render_comp(brief: CompBrief) -> str:
    """Prompt-ready and pack-ready. Same text both places, so the model
    cannot paraphrase a number into something the pack does not say."""
    lines = [
        f"- Posted band: {_usd(brief.posted_min_usd)}"
        + (f" - {_usd(brief.posted_max_usd)}" if brief.posted_max_usd else ""),
        f"- Your floor: ${brief.floor_usd:,} · target: "
        f"${brief.target_usd:,} · anchor: ${brief.anchor_usd:,}",
        f"- Role shape: {brief.shape.replace('_', ' ')}"
        + (f" ({brief.role_country})" if brief.role_country else ""),
    ]
    if brief.median_usd:
        lines.append(
            f"- Comparable postings (n={brief.sample_size}, same role "
            f"category and seniority, from karani's own ingestion — "
            f"advertised bands, not a market survey): p25 "
            f"{_usd(brief.p25_usd)} · median {_usd(brief.median_usd)} · "
            f"p75 {_usd(brief.p75_usd)}")
    else:
        lines.append(f"- Comparable postings in karani's own ingestion: "
                     f"n={brief.sample_size} — too few to benchmark.")
    lines += [f"- {n}" for n in brief.notes]
    return "\n".join(lines)
