"""Prep fires on the state change, not on being remembered.

`karani prep <id>` has existed for months and has been run when the
candidate thought of it, which is never the moment that matters. The
moment that matters is when an interview gets scheduled — days before
it happens, while there is still time to read a dossier and rehearse a
story.

So the trigger hangs off the application state machine rather than off
any one caller. `Storage.set_application_status` dispatches here, which
means prep arrives whatever moved the row: the CLI, the MCP tool, a
Slack button, or the email watcher reading a scheduling mail. Nothing
has to import the coach to get it.

Three properties the trigger must hold, in order of how badly their
absence would hurt:

- **It never breaks the transition.** The state machine is the system
  of record; a coach that raises must not take a status write with it.
  Every failure here is caught and logged.
- **It never preps the same stage twice.** Statuses get re-set — a
  button, then the watcher reading the same thread. The stages log is
  the idempotency key, so a second pass costs nothing.
- **It is killable.** `KARANI_AUTO_PREP=off` (or karani.toml
  `[coach] auto_prep = false`) stops it dead, because an unattended
  billed call needs a switch within reach.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from karani.ingestion.storage import Storage

log = logging.getLogger(__name__)

# The transitions that mean "a human will talk to you soon". `applied`
# is deliberately not here: nothing is scheduled yet, and prepping for
# every submitted application would spend the month's budget in a day.
PREP_STATUSES = frozenset({"screen", "interview"})

_STAGE_PREFIX = "prep"
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def auto_prep_enabled() -> bool:
    """env > karani.toml `[coach] auto_prep` > on."""
    override = os.getenv("KARANI_AUTO_PREP")
    if override is not None:
        return override.lower() not in ("off", "0", "false", "no")
    try:
        from karani.config import get_config
        return bool(get_config().coach.auto_prep)
    except Exception:
        return True


def _stage_marker(status: str) -> str:
    return f"{_STAGE_PREFIX}:{status}"


def _already_prepped(row: dict, status: str) -> bool:
    marker = _stage_marker(status)
    stages = row.get("stages") or []
    if isinstance(stages, str):
        import json
        try:
            stages = json.loads(stages)
        except json.JSONDecodeError:
            return False
    return any(isinstance(s, dict) and s.get("stage") == marker
               for s in stages)


def scheduled_from_stages(row: dict) -> datetime | None:
    """The interview date, if whoever recorded the stage wrote one.

    karani does not own a calendar, and the email watcher writes what
    it found into the stage note. An ISO date in the latest note is
    enough to say "in four days" on the card; anything fancier would be
    guessing at a format nobody has agreed on yet.
    """
    stages = row.get("stages") or []
    if isinstance(stages, str):
        import json
        try:
            stages = json.loads(stages)
        except json.JSONDecodeError:
            return None
    for stage in reversed(list(stages)):
        if not isinstance(stage, dict):
            continue
        if str(stage.get("stage", "")).startswith(_STAGE_PREFIX):
            continue
        match = _ISO_DATE.search(str(stage.get("notes") or ""))
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)),
                                int(match.group(3)), tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


async def on_status_change(
    storage: Storage, job_id: int, status: str, *,
    resume_markdown: str | None = None,
    client=None, slack=None, channel: str | None = None,
):
    """Build and deliver a prep pack when `status` is an interview.

    Returns the `PrepResult`, or None when nothing was due. Raises
    nothing: the caller is a status write.
    """
    if status not in PREP_STATUSES or not auto_prep_enabled():
        return None

    row = await storage.get_job(job_id)
    if not row:
        log.warning("prep trigger: no job with id=%s", job_id)
        return None
    if _already_prepped(row, status):
        log.info("prep trigger: job %s already prepped for %s — skipping",
                 job_id, status)
        return None

    if resume_markdown is None:
        from karani.ingestion.resume import ResumeProfile
        resume_markdown = ResumeProfile.from_file().raw_markdown

    from .pipeline import build_prep_pack
    result = await build_prep_pack(
        storage, job_row=row, resume_markdown=resume_markdown,
        stage=status, client=client,
        scheduled_at=scheduled_from_stages(row))

    # The marker goes in whether or not the composing call succeeded:
    # a failed pack still cost two calls, and retrying it unattended on
    # the next status write would just spend them again. `karani prep`
    # remains the deliberate retry.
    await storage.add_stage(job_id, _stage_marker(status),
                            notes=str(result.path))
    await _deliver(result, row, slack=slack, channel=channel)
    return result


async def notify_status_change(storage: Storage, job_id: int,
                               status: str, **kwargs) -> None:
    """The entry point `Storage.set_application_status` calls.

    Swallows everything. A transition that fails because the coach fell
    over is a far worse bug than a missing prep pack.
    """
    try:
        await on_status_change(storage, job_id, status, **kwargs)
    except Exception:
        log.warning("prep trigger failed for job %s (%s); the status "
                    "change stands", job_id, status, exc_info=True)


async def _deliver(result, job_row: dict, *, slack=None,
                   channel: str | None = None) -> None:
    """Slack card, best-effort. The pack is already on disk either way."""
    from .blocks import prep_blocks

    try:
        if slack is None:
            from karani.slackbridge import SlackClient, configured_channel
            channel = channel or configured_channel()
            if not channel:
                return
            slack = SlackClient()
        elif channel is None:
            from karani.slackbridge import configured_channel
            channel = configured_channel()
        company = (job_row.get("company_display")
                   or job_row.get("company") or "")
        await slack.post_message(
            channel,
            f"Interview prep ready: {company} — {job_row.get('title', '')}",
            blocks=prep_blocks(job_row, result.pack,
                               artifacts=result.artifacts,
                               path=str(result.path)))
    except Exception:
        log.warning("could not deliver the prep card to Slack; the pack is "
                    "at %s", result.path, exc_info=True)
