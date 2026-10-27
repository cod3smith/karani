"""Terminal surface for the analytics layer.

Kept out of `karani/cli.py` on purpose: that file is edited by every
feature and is the busiest merge point in the repo. It gains three
dispatch lines; the printing lives here.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .funnel import conversion_report
from .ledger import ConversionLedger
from .models import ConversionReport, StrategyVariant
from .registry import VariantRegistry
from .review import (CoachReview, render_documents, render_markdown,
                     render_text, review_blocks, weekly_review)
from .variants import MIN_SAMPLE_PER_ARM

log = logging.getLogger(__name__)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _print_rows(title: str, rows, limit: int = 8) -> None:
    if not rows:
        return
    print(f"\n{title}:")
    for b in rows[:limit]:
        print(f"  {b.key[:28]:<28} applied={b.applied:<4} "
              f"replied={b.responded:<4} rate={_pct(b.response_rate):>6} "
              f"(95% CI {_pct(b.ci_low)}-{_pct(b.ci_high)})")


async def print_conversion(storage, *, sync: bool = True,
                           min_sample_per_arm: int = MIN_SAMPLE_PER_ARM
                           ) -> ConversionReport:
    """The ledger-backed half of `karani funnel`."""
    ledger = ConversionLedger(storage)
    await ledger.ensure_schema()
    if sync:
        await ledger.sync()
    entries = await ledger.entries()
    registry = VariantRegistry(storage)
    await registry.ensure_schema()
    report = conversion_report(entries,
                              min_sample_per_arm=min_sample_per_arm,
                              guidance=await registry.guidance_map())

    print("\n--- conversion ledger ---")
    print(report.confidence)
    if not entries:
        return report

    ttfr = report.time_to_first_response
    if ttfr.n:
        print(f"\ntime to first response: median {ttfr.median_days:g}d · "
              f"p75 {ttfr.p75_days:g}d · range {ttfr.fastest_days:g}-"
              f"{ttfr.slowest_days:g}d · n={ttfr.n} · "
              f"{ttfr.awaiting} still open")
        if ttfr.note:
            print(f"  {ttfr.note}")
    else:
        print(f"\ntime to first response: no replies yet "
              f"({ttfr.awaiting} open)")

    _print_rows("by positioning angle", report.by_positioning_angle)
    _print_rows("by variant", report.by_variant)
    _print_rows("by company", report.by_company)
    _print_rows("by comp band", report.by_comp_band)

    if report.scale_warning:
        print(f"\n{report.scale_warning}")
    if report.fit_scales:
        print("\nfit scales (a score is only comparable within one):")
        for scale in report.fit_scales:
            print(f"  {scale.label[:34]:<34} n={scale.n:<4} "
                  f"mean={scale.mean_fit} "
                  f"range={scale.min_fit}-{scale.max_fit}")
    _print_rows("by fit band (pooled — single scale only)",
                report.by_fit_band)
    for confound in report.confounds:
        print(f"\nconfound: {confound}")
    if report.recurring_gaps:
        print("\nrecurring gaps in rejected applications:")
        for gap, n in report.recurring_gaps[:8]:
            print(f"  {gap[:50]:<50} {n}")
    print(f"\nexperiment: {report.promotion.headline}")
    if report.promotion.detail:
        print(f"  {report.promotion.detail}")
    return report


async def run_coach(storage, *, window_days: int = 7, slack: bool = False,
                    save: str | None = None, docs: bool = False,
                    now=None) -> int:
    """`karani coach` — the weekly read, optionally delivered."""
    review: CoachReview = await weekly_review(storage, window_days=window_days,
                                              now=now)
    print(render_text(review))

    if save:
        target = Path(save)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(review), encoding="utf-8")
        print(f"\nwrote {target}")
        if docs:
            for ext, blob in render_documents(review).items():
                doc_path = target.with_suffix(f".{ext}")
                doc_path.write_bytes(blob)
                print(f"wrote {doc_path}")

    if slack:
        # The canonical resolver, not a raw env read — see
        # tests/test_config.py::test_no_raw_delivery_env_reads_outside_helpers.
        from karani.slackbridge import configured_channel
        channel = configured_channel()
        if not channel:
            print("\nNo Slack channel configured — skipping the Slack card.")
            return 0
        try:
            from karani.slackbridge.client import SlackClient
            await SlackClient().post_message(
                channel, text=review.headline or "Coach review",
                blocks=review_blocks(review))
            print(f"\nposted the coach review to {channel}")
        except Exception as exc:
            # A delivery failure must not lose the review that is
            # already on stdout.
            print(f"\nSlack delivery failed: {exc}")
    return 0


async def run_variant(storage, action: str, *, name: str | None = None,
                      dimension: str = "positioning", description: str = "",
                      guidance: str = "", job_id: int | None = None) -> int:
    """`karani variant <list|add|retire|assign>`."""
    registry = VariantRegistry(storage)
    await registry.ensure_schema()

    if action == "list":
        variants = await registry.all()
        if not variants:
            print("No strategy variants registered. Until at least two "
                  "exist and applications are tagged with them, conversion "
                  "cannot be attributed to an angle.")
            return 0
        for v in variants:
            state = "active" if v.active else "retired"
            print(f"  {v.name:<24} {v.dimension:<14} [{state}] "
                  f"{v.description or v.guidance[:60]}")
        return 0

    if action == "add":
        if not name:
            print("variant add needs a name.")
            return 2
        await registry.register(StrategyVariant(
            name=name, dimension=dimension, description=description,
            guidance=guidance))
        print(f"registered variant '{name}' ({dimension}).")
        if not guidance:
            print("  No guidance text: this arm labels applications but "
                  "does not change how they are written, so a difference "
                  "between arms would not be attributable to it.")
        return 0

    if action == "retire":
        if not name:
            print("variant retire needs a name.")
            return 2
        if not await registry.deactivate(name):
            print(f"no variant named '{name}'.")
            return 1
        print(f"retired '{name}'. Its applications keep the label.")
        return 0

    if action == "assign":
        if job_id is None:
            print("variant assign needs a job id.")
            return 2
        variant = await registry.assign(job_id)
        if variant is None:
            print("No active variants — register at least one with "
                  "`karani variant add`.")
            return 1
        ledger = ConversionLedger(storage)
        await ledger.ensure_schema()
        tagged = await ledger.tag_variant(job_id, variant.name,
                                          angle=variant.name)
        if not tagged:
            print(f"job {job_id} has not been applied to, so there is "
                  f"nothing to tag. Assignment would be '{variant.name}'.")
            return 1
        print(f"job {job_id} tagged '{variant.name}'.")
        if variant.guidance:
            print(f"  guidance: {variant.guidance}")
        return 0

    print(f"unknown variant action '{action}'.")
    return 2


__all__ = ["print_conversion", "run_coach", "run_variant"]
