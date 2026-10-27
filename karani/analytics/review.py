"""The weekly coach review — a read over the ledger, in plain language.

Deliberately not an LLM call. Every line here is derived arithmetic
over recorded facts, which means it costs nothing, cannot hallucinate
a trend, and says the same thing twice when asked twice. A model would
write prettier prose and would also, on a sample of four, confidently
explain why Kubernetes roles are rejecting you.

The review is built to be useful at n=0. With no applications sent the
honest output is one observation and one action, and that is what it
produces — not an empty template, and not an invented insight.

It renders three ways from one model: markdown (which the .docx/.pdf
renderers in `karani/documents` consume unchanged), a Slack Block Kit
card, and plain text for the terminal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from .funnel import MEANINGFUL_SAMPLE, conversion_report
from .models import UNASSIGNED, Breakdown, ConversionReport, LedgerEntry
from .variants import MIN_SAMPLE_PER_ARM

log = logging.getLogger(__name__)

# An application silent for this long is, for practical purposes, over.
# Calibrated against the reply distribution when there is one.
DEFAULT_STALE_DAYS = 28


class CoachReview(BaseModel):
    """One week's read. Rendered, never narrated by a model."""
    generated_at: datetime
    window_days: int = 7
    applications_in_window: int = 0
    replies_in_window: int = 0
    total_applications: int = 0
    total_replies: int = 0
    headline: str = ""
    promotion_line: str = ""
    winner: str | None = None
    observations: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    report: ConversionReport = Field(default_factory=ConversionReport)
    drafted_unsent: int = 0


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _top(rows: list[Breakdown], *, min_applied: int = 1) -> list[Breakdown]:
    return [b for b in rows if b.applied >= min_applied]


def build_review(
    entries: list[LedgerEntry],
    *,
    now: datetime | None = None,
    window_days: int = 7,
    min_sample_per_arm: int = MIN_SAMPLE_PER_ARM,
    drafted_unsent: int = 0,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> CoachReview:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    report = conversion_report(entries, min_sample_per_arm=min_sample_per_arm)

    in_window = [e for e in entries if e.applied_at and e.applied_at >= cutoff]
    replies_in_window = [e for e in entries
                         if e.first_response_at and e.first_response_at >= cutoff]
    total = len(entries)
    replied = report.totals.responded

    review = CoachReview(
        generated_at=now,
        window_days=window_days,
        applications_in_window=len(in_window),
        replies_in_window=len(replies_in_window),
        total_applications=total,
        total_replies=replied,
        report=report,
        promotion_line=report.promotion.headline,
        winner=report.promotion.winner,
        drafted_unsent=drafted_unsent,
    )

    observations: list[str] = []
    actions: list[str] = []

    # --- the week itself ---
    if total == 0:
        review.headline = ("No applications sent yet — the loop has nothing "
                           "to learn from.")
        observations.append(
            "Every stage of the pipeline works except the one that produces "
            "outcomes. Until an application is actually sent, this review "
            "has no data and no opinion.")
        actions.append(
            "Send the first applications. Thirty of them is what turns this "
            "page from a template into a measurement."
            + (f" {drafted_unsent} pack(s) are drafted and unsent."
               if drafted_unsent else ""))
        review.observations, review.actions = observations, actions
        return review

    if len(in_window) == 0:
        review.headline = (f"Nothing sent in the last {window_days} days; "
                           f"{total} application(s) on record overall.")
        observations.append(
            f"No applications went out in the last {window_days} days. "
            f"The numbers below are the whole history, not this week.")
        actions.append(
            "Restart the cadence before the measurement goes stale"
            + (f" — {drafted_unsent} pack(s) are drafted and unsent."
               if drafted_unsent else "."))
    else:
        review.headline = (
            f"{len(in_window)} application(s) sent and "
            f"{len(replies_in_window)} repl(y/ies) received in the last "
            f"{window_days} days; {replied} of {total} overall "
            f"({_pct(report.totals.response_rate)}).")

    # --- reply rate, with the honest caveat attached ---
    if replied == 0 and total >= 10:
        observations.append(
            f"0 of {total} applications have drawn any reply. Before "
            f"changing the pitch, check the channel: whether the documents "
            f"are reaching a human at all, whether the submission path "
            f"completes, and whether the format survives the parser. A "
            f"positioning problem and a delivery problem look identical "
            f"from here.")
        actions.append(
            "Verify one submission end to end — format, upload, confirmation "
            "email — before attributing the silence to the writing.")
    elif replied:
        ttfr = report.time_to_first_response
        if ttfr.median_days is not None:
            observations.append(
                f"When a reply comes it takes a median of "
                f"{ttfr.median_days:g} days (n={ttfr.n}"
                + (f", slowest {ttfr.slowest_days:g}" if ttfr.slowest_days
                   else "") + f"). {ttfr.awaiting} application(s) are still "
                "open.")

    # --- what converted ---
    for label, rows in (("source", report.by_source),
                        ("company", report.by_company),
                        ("comp band", report.by_comp_band)):
        best = next((b for b in _top(rows, min_applied=max(5, total // 10))
                     if b.responded), None)
        if best is not None and best.applied < total:
            observations.append(
                f"Best-performing {label}: {best.key} — {best.responded} of "
                f"{best.applied} replied ({_pct(best.response_rate)}, 95% CI "
                f"{_pct(best.ci_low)}-{_pct(best.ci_high)}).")

    # --- incomparable scales and confounded arms ---
    if report.scale_warning:
        observations.append(report.scale_warning)
        actions.append(
            "Re-score the backlog on one model before reading anything "
            "into fit bands, or compare only within a scale. A threshold "
            "tuned on one scorer does not transfer to another.")
    for confound in report.confounds:
        observations.append(confound)

    # --- silence that should be closed out ---
    stale = [e for e in entries
             if not e.responded and e.outcome is None and e.applied_at
             and (now - e.applied_at).days >= stale_days]
    if stale:
        observations.append(
            f"{len(stale)} application(s) have been silent for "
            f"{stale_days}+ days with no reply and no recorded outcome.")
        actions.append(
            f"Mark the {len(stale)} long-silent application(s) ghosted so "
            f"they stop inflating the open pipeline, or send a follow-up.")

    # --- recurring gaps in rejections ---
    rejections = sum(1 for e in entries if e.outcome == "rejection")
    recurring = [(gap, n) for gap, n in report.recurring_gaps if n >= 2]
    if recurring:
        gap, count = recurring[0]
        observations.append(
            f"'{gap}' was an unmet requirement in {count} of {rejections} "
            f"rejected application(s)"
            + (", alongside " + ", ".join(f"'{g}' ({n})"
                                          for g, n in recurring[1:3])
               if len(recurring) > 1 else "") + ".")
        actions.append(
            f"Decide on '{gap}': close the gap, find honest evidence for it "
            f"already in the record, or filter roles that require it. "
            f"Three rejections is a pattern worth a decision, not proof of "
            f"causation.")

    # --- the experiment ---
    if report.promotion.status == "promote" and report.promotion.winner:
        actions.append(
            f"Promote '{report.promotion.winner}' to the default angle and "
            f"retire the weaker arm. {report.promotion.detail}")
    elif report.promotion.status == "insufficient_data":
        untagged = next((b.applied for b in report.by_variant
                         if b.key == UNASSIGNED), 0)
        if untagged:
            actions.append(
                f"{untagged} application(s) carry no variant tag, so their "
                f"outcomes cannot be attributed to any angle. Tag them, or "
                f"accept that the experiment restarts from the next batch.")
        review.promotion_line = (report.promotion.headline + " "
                                 + report.promotion.detail)
        if "not enough data" not in review.promotion_line.lower():
            review.promotion_line = ("Not enough data to name a winner. "
                                     + review.promotion_line)

    if total < MEANINGFUL_SAMPLE:
        observations.append(
            f"Sample size: {total}. Treat every ordering above as "
            f"provisional until there are {MEANINGFUL_SAMPLE}+ applications "
            f"per arm; the design brief's estimate was one to two months at "
            f"~30 applications a month.")

    review.observations, review.actions = observations, actions
    return review


# --- renderers -------------------------------------------------------

def _table(rows: list[Breakdown], limit: int = 6) -> list[str]:
    out = []
    for b in rows[:limit]:
        out.append(f"- **{b.key}** — {b.responded}/{b.applied} replied "
                   f"({_pct(b.response_rate)}, 95% CI {_pct(b.ci_low)}-"
                   f"{_pct(b.ci_high)})")
    return out


def render_markdown(review: CoachReview) -> str:
    """Markdown that the .docx/.pdf renderers consume unchanged."""
    r = review.report
    date = review.generated_at.date().isoformat()
    lines = [
        "# Application coach review",
        f"Week ending {date} · {review.window_days}-day window",
        "",
        "## Summary",
        review.headline,
        "",
        "## Conversion",
    ]
    lines.append(
        f"- **Overall** — {r.totals.responded}/{r.totals.applied} replied "
        f"({_pct(r.totals.response_rate)}, 95% CI {_pct(r.totals.ci_low)}-"
        f"{_pct(r.totals.ci_high)})")
    ttfr = r.time_to_first_response
    if ttfr.n:
        lines.append(
            f"- **Time to first response** — median {ttfr.median_days:g}d, "
            f"p75 {ttfr.p75_days:g}d, range {ttfr.fastest_days:g}-"
            f"{ttfr.slowest_days:g}d over {ttfr.n} repl(y/ies); "
            f"{ttfr.awaiting} still open")
    else:
        lines.append(f"- **Time to first response** — no replies yet; "
                     f"{ttfr.awaiting} application(s) open")

    for title, rows in (("By positioning angle", r.by_positioning_angle),
                        ("By variant", r.by_variant),
                        ("By source", r.by_source),
                        ("By company", r.by_company),
                        ("By comp band", r.by_comp_band)):
        if rows:
            lines += ["", f"## {title}"] + _table(rows)

    if r.fit_scales:
        lines += ["", "## Fit scales"]
        if r.scale_warning:
            lines.append(r.scale_warning)
        for scale in r.fit_scales:
            lines.append(
                f"- **{scale.label}** — n={scale.n}, mean fit "
                f"{scale.mean_fit if scale.mean_fit is not None else 'n/a'}, "
                f"range {scale.min_fit}-{scale.max_fit}; "
                + ", ".join(f"{b.key} {b.responded}/{b.applied}"
                            for b in scale.bands))

    if r.recurring_gaps:
        lines += ["", "## Recurring gaps in rejected applications"]
        lines += [f"- {gap} — {n} rejection(s)"
                  for gap, n in r.recurring_gaps[:8]]

    lines += ["", "## Strategy experiment", review.promotion_line or
              r.promotion.headline]
    for arm in r.promotion.arms:
        lines.append(f"- **{arm.name}** — {arm.responded}/{arm.applied} "
                     f"({_pct(arm.response_rate)}, 95% CI "
                     f"{_pct(arm.ci_low)}-{_pct(arm.ci_high)})"
                     + ("" if arm.eligible else " — below minimum sample"))

    if review.observations:
        lines += ["", "## What the week says"]
        lines += [f"- {o}" for o in review.observations]

    lines += ["", "## What to do differently"]
    lines += [f"- {a}" for a in review.actions] or ["- Keep sending."]

    lines += ["", "## Confidence", r.confidence]
    return "\n".join(lines) + "\n"


def render_text(review: CoachReview) -> str:
    """Terminal rendering — the markdown without the decoration."""
    import re
    md = render_markdown(review)
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    return re.sub(r"^#+ ", "", md, flags=re.MULTILINE)


def _section(text: str) -> dict:
    return {"type": "section",
            "text": {"type": "mrkdwn", "text": text[:2900]}}


def review_blocks(review: CoachReview) -> list[dict]:
    """A Slack card. Block Kit limits are hard: 50 blocks, 3000 chars
    per section, 150 per header — so this summarises rather than dumps
    the document, and says where the full one lives."""
    r = review.report
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"Coach review — week ending "
                          f"{review.generated_at.date().isoformat()}"[:150]}},
        _section(review.headline or "No activity."),
    ]
    overall = (f"*Overall* {r.totals.responded}/{r.totals.applied} replied "
               f"({_pct(r.totals.response_rate)}, 95% CI "
               f"{_pct(r.totals.ci_low)}-{_pct(r.totals.ci_high)})")
    ttfr = r.time_to_first_response
    if ttfr.n:
        overall += (f"\n*Time to first reply* median {ttfr.median_days:g}d "
                    f"over {ttfr.n}; {ttfr.awaiting} still open")
    blocks.append(_section(overall))

    top = [b for b in r.by_positioning_angle or r.by_source][:4]
    if top:
        blocks.append(_section(
            "*Converting*\n" + "\n".join(
                f"• {b.key} — {b.responded}/{b.applied} "
                f"({_pct(b.response_rate)})" for b in top)))

    blocks.append(_section("*Experiment*\n" + (review.promotion_line
                                               or r.promotion.headline)))
    if review.observations:
        blocks.append(_section("*What the week says*\n"
                               + "\n".join(f"• {o}"
                                           for o in review.observations[:5])))
    if review.actions:
        blocks.append(_section("*What to do differently*\n"
                               + "\n".join(f"• {a}"
                                           for a in review.actions[:5])))
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": r.confidence[:2900]}]})
    return blocks[:50]


def render_documents(review: CoachReview) -> dict[str, bytes]:
    """`{extension: bytes}` for whatever this machine can render.

    Best-effort by design, the same as the application pack: a missing
    Chrome or python-docx must not cost you the review, which is
    readable as markdown regardless.
    """
    try:
        from karani.documents import render_all
        return render_all(render_markdown(review),
                          title="Application coach review")
    except Exception:
        log.warning("coach review document rendering failed; markdown only",
                    exc_info=True)
        return {}


# --- storage-backed entry point --------------------------------------

async def weekly_review(storage, *, now: datetime | None = None,
                        window_days: int = 7,
                        min_sample_per_arm: int = MIN_SAMPLE_PER_ARM,
                        sync: bool = True) -> CoachReview:
    """Reconcile the ledger, then read it."""
    from .ledger import ConversionLedger

    ledger = ConversionLedger(storage)
    await ledger.ensure_schema()
    if sync:
        await ledger.sync()
    entries = await ledger.entries()

    drafted_unsent = 0
    try:
        counts = await storage.pipeline_summary()
        drafted_unsent = int(counts.get("drafting", 0)) + \
            int(counts.get("ready", 0))
    except Exception:  # pragma: no cover - summary is advisory only
        log.warning("pipeline summary unavailable for the review",
                    exc_info=True)

    return build_review(entries, now=now, window_days=window_days,
                        min_sample_per_arm=min_sample_per_arm,
                        drafted_unsent=drafted_unsent)


__all__ = ["DEFAULT_STALE_DAYS", "CoachReview", "build_review",
           "render_markdown", "render_text", "render_documents",
           "review_blocks", "weekly_review"]
