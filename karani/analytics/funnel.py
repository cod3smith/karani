"""Conversion analysis over the ledger — the question that compounds.

`Storage.funnel_stats` already splits applications by fit band, source,
prompt version, warm path and posting age. This adds the slices the
design doc asks for and that one cannot answer from the jobs table:
which **positioning angle** and which **strategy variant** convert,
which companies and comp bands reply, and how long a reply takes.

Three rules hold everywhere in here:

1. Every rate is reported with its 95% Wilson interval. A bare
   percentage over six applications is a lie told with arithmetic.
2. Slices are ordered by volume, never by rate. Sorting by rate puts
   the 1-of-1 company on top, which is exactly the number you should
   ignore.
3. The report carries a `confidence` sentence stating the sample size
   and what cannot be concluded from it. On an empty ledger that
   sentence is the entire useful output, and it is correct.
"""
from __future__ import annotations

from collections import Counter

from karani.ingestion.storage import _fit_band  # shared so bands cannot drift

from .models import (UNASSIGNED, Breakdown, ConversionReport, LedgerEntry,
                     TimeToResponse)
from .stats import median, percentile, proportion, wilson_interval
from .variants import MIN_SAMPLE_PER_ARM, promotion_report

# Below this, the per-slice numbers are decoration. Stated out loud in
# `confidence` rather than silently suppressed — you should be able to
# see your own data, just not believe a ranking built from it.
MEANINGFUL_SAMPLE = 30


def comp_band(min_usd: int | None, max_usd: int | None) -> str:
    """Band an application by the top of its disclosed range.

    The top, not the midpoint: the band is a proxy for "what level of
    company is this", and the ceiling carries that better than an
    average over a range the employer chose how wide to make.
    """
    top = max_usd or min_usd
    if not top:
        return "undisclosed"
    if top < 160_000:
        return "<160k"
    if top < 200_000:
        return "160-199k"
    if top < 250_000:
        return "200-249k"
    if top < 300_000:
        return "250-299k"
    return "300k+"


def _breakdown(key: str, entries: list[LedgerEntry]) -> Breakdown:
    applied = len(entries)
    responded = sum(1 for e in entries if e.responded)
    offers = sum(1 for e in entries if e.outcome == "offer")
    low, high = wilson_interval(responded, applied)
    days = [e.days_to_first_response for e in entries
            if e.days_to_first_response is not None]
    return Breakdown(
        key=key, applied=applied, responded=responded, offers=offers,
        response_rate=round(proportion(responded, applied), 4),
        ci_low=low, ci_high=high,
        median_days_to_response=median(days),
    )


def _group(entries: list[LedgerEntry], key_of) -> list[Breakdown]:
    buckets: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        key = key_of(entry)
        if key is None:
            continue
        buckets.setdefault(str(key), []).append(entry)
    rows = [_breakdown(key, group) for key, group in buckets.items()]
    # Volume first. Ranking by rate would promote whichever slice has
    # the smallest denominator, every single time.
    rows.sort(key=lambda b: (-b.applied, -b.response_rate, b.key))
    return rows


def _time_to_first_response(entries: list[LedgerEntry]) -> TimeToResponse:
    days = sorted(e.days_to_first_response for e in entries
                  if e.days_to_first_response is not None)
    awaiting = sum(1 for e in entries if not e.responded)
    if not days:
        note = ("No replies yet, so there is no distribution to describe."
                if entries else "No applications on record.")
        return TimeToResponse(n=0, awaiting=awaiting, note=note)
    note = ""
    if len(days) < 5:
        note = (f"Based on {len(days)} repl{'y' if len(days) == 1 else 'ies'} "
                "— a description of what happened, not a prediction.")
    return TimeToResponse(
        n=len(days), awaiting=awaiting,
        median_days=median(days), p75_days=percentile(days, 75),
        fastest_days=days[0], slowest_days=days[-1], note=note,
    )


def _recurring_gaps(entries: list[LedgerEntry]) -> list[tuple[str, int]]:
    """Which requirements keep showing up in roles that said no.

    Only rejections count. A gap on an application still in flight
    proves nothing, and a gap on one that got a screen is evidence the
    gap did NOT matter — counting either would turn the candidate's
    whole skill profile into a to-do list.
    """
    counter: Counter[str] = Counter()
    for entry in entries:
        if entry.outcome != "rejection":
            continue
        counter.update({g for g in entry.gaps if g})
    return counter.most_common()


def _confidence(entries: list[LedgerEntry], responded: int) -> str:
    n = len(entries)
    if n == 0:
        return ("No applications on record. Nothing in this report is a "
                "finding yet — the ledger starts answering questions once "
                "applications are actually sent.")
    if n < 10:
        return (f"{n} application(s) on record, {responded} with a reply. "
                "That is too few to rank anything: every breakdown below "
                "is a description of what happened, not evidence about "
                f"what works. Expect signal around {MEANINGFUL_SAMPLE} "
                "applications per arm.")
    if n < MEANINGFUL_SAMPLE:
        return (f"{n} applications on record, {responded} with a reply. "
                "Enough to notice a pattern, not enough to act on one: "
                "confidence intervals below are still wide enough to "
                "contain almost any ordering.")
    return (f"{n} applications on record, {responded} with a reply. "
            "Slices with at least a few dozen applications are worth "
            "reading; the rest are listed for completeness. Intervals "
            "are 95% Wilson — where two of them overlap, the difference "
            "is not established.")


def conversion_report(
    entries: list[LedgerEntry],
    *,
    min_sample_per_arm: int = MIN_SAMPLE_PER_ARM,
    guidance: dict[str, str] | None = None,
) -> ConversionReport:
    """Everything the ledger can honestly say, including "not much yet"."""
    totals = _breakdown("all", list(entries))
    arms: dict[str, dict] = {}
    for entry in entries:
        bucket = arms.setdefault(entry.variant or UNASSIGNED,
                                 {"applied": 0, "responded": 0})
        bucket["applied"] += 1
        bucket["responded"] += int(entry.responded)

    return ConversionReport(
        totals=totals,
        by_variant=_group(entries, lambda e: e.variant or UNASSIGNED),
        by_positioning_angle=_group(entries, lambda e: e.positioning_angle),
        by_source=_group(entries, lambda e: e.source or "unknown"),
        by_company=_group(entries,
                          lambda e: e.company_display or e.company or
                          "unknown"),
        by_comp_band=_group(entries, lambda e: comp_band(e.comp_min_usd,
                                                         e.comp_max_usd)),
        by_fit_band=_group(entries, lambda e: _fit_band(e.fit_score)),
        by_draft_prompt=_group(entries,
                               lambda e: e.draft_prompt_version or "none"),
        time_to_first_response=_time_to_first_response(entries),
        recurring_gaps=_recurring_gaps(entries),
        promotion=promotion_report(arms,
                                   min_sample_per_arm=min_sample_per_arm,
                                   guidance=guidance),
        confidence=_confidence(entries, totals.responded),
    )


__all__ = ["MEANINGFUL_SAMPLE", "comp_band", "conversion_report"]
