"""Autopilot pass: candidates -> draft pack -> Slack review card.

Runs inside the scheduled daily chain. Per-job failures are contained —
one bad draft or Slack hiccup skips that job and continues; the next
pass retries anything still eligible (drafted jobs move to `drafting`,
so they leave the candidate pool).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from ingestion.storage import Storage
from notionsync import maybe_sync_job

log = logging.getLogger(__name__)

DEFAULT_MIN_FIT = int(os.getenv("AUTOPILOT_MIN_FIT", "85"))
DEFAULT_MAX_DRAFTS = int(os.getenv("AUTOPILOT_MAX_DRAFTS", "3"))
# Hard daily spend ceiling across all runs — what makes hourly scheduling
# safe: 24 runs/day share this budget, they don't multiply it.
DEFAULT_DAILY_CAP = int(os.getenv("AUTOPILOT_MAX_DRAFTS_PER_DAY", "5"))


@dataclass
class AutopilotStats:
    candidates: int = 0
    drafted: int = 0
    delivered: int = 0
    budget_left: int = 0
    errors: list[str] = field(default_factory=list)


async def run_autopilot(
    storage: Storage, *,
    slack, channel: str,
    make_qualifier, load_resume,
    min_fit: int = DEFAULT_MIN_FIT,
    max_drafts: int = DEFAULT_MAX_DRAFTS,
    daily_cap: int = DEFAULT_DAILY_CAP,
) -> AutopilotStats:
    """Draft + deliver packs for the top candidates, bounded twice:
    `max_drafts` per run AND `daily_cap` per UTC day across all runs."""
    from drafting import build_application_pack
    from slackbridge.blocks import pack_blocks

    stats = AutopilotStats()
    if max_drafts <= 0:
        log.info("autopilot disabled (AUTOPILOT_MAX_DRAFTS<=0)")
        return stats

    spent_today = await storage.drafts_today()
    budget = min(max_drafts, max(0, daily_cap - spent_today))
    stats.budget_left = max(0, daily_cap - spent_today)
    if budget <= 0:
        log.info("autopilot daily budget spent (%d/%d today)",
                 spent_today, daily_cap)
        return stats

    rows = await storage.autopilot_candidates(min_fit=min_fit,
                                              limit=budget)
    stats.candidates = len(rows)
    if not rows:
        return stats

    resume = load_resume()
    client = make_qualifier()
    for row in rows:
        job_id = row["id"]
        try:
            pack = await build_application_pack(
                client, storage, job_row=row,
                resume_markdown=resume.raw_markdown,
            )
            if pack.failed:
                # Malformed LLM output: never deliver a broken pack, and
                # leave the job in the candidate pool so the next pass
                # retries it (build_application_pack records nothing on
                # failure).
                stats.errors.append(f"job_id={job_id}: draft failed "
                                    f"(malformed LLM output); will retry")
                continue
            stats.drafted += 1
            await slack.post_message(
                channel,
                f"Application pack ready for review: "
                f"{row.get('company_display')} — {row.get('title')}",
                blocks=pack_blocks(row, pack.pkg,
                                   artifacts=pack.artifacts,
                                   voice=pack.voice),
            )
            stats.delivered += 1
            await maybe_sync_job(storage, job_id)
        except Exception as exc:
            stats.errors.append(f"job_id={job_id}: {exc}")
            log.exception("autopilot failed for job %s", job_id)
    return stats
