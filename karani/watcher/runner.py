"""One watch pass: read, label, match, advance, draft, deliver.

The ordering is the whole design. Classification is billed, so the
ledger is consulted first and a message already handled never reaches a
model. Matching is deterministic and runs before any write, so the
state machine only ever moves on evidence karani computed itself —
never on something the email said about itself. And every write is
guarded by `confidence == "high"`: a low or absent match is reported
for a human, because a wrong transition is worse than a missing one
(design 5.4).

Failure is contained at every level. A dead mailbox, a dead classifier,
a dead Slack: each lands in `report.errors` and the pass continues.
This is the degradation contract the budget guard and the memory layer
already hold (ADR 0019, 0009).
"""
from __future__ import annotations

import logging

from karani.ingestion.storage import Storage

from .blocks import summary_line, watch_blocks
from .classify import classify_message
from .match import match_message
from .models import ProcessedMessage, WatchReport
from .reply import draft_reply
from .state import next_status
from .store import WatcherStore

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_LIMIT = 50

# Labels whose arrival is worth a line in the stages log even when they
# move nothing — this is the evidence trail for the conversion ledger.
_LOGGED = frozenset({"rejection", "interview_invite", "assessment",
                     "scheduling", "offer"})


def _watcher_cfg():
    from karani.config import get_config
    return get_config().watcher


async def deliver_report(report: WatchReport, *,
                         job_titles: dict | None = None) -> bool:
    """Push the pass to Slack. Best-effort: returns False when the
    bridge is not configured rather than raising."""
    from karani.slackbridge import SlackClient, configured_channel

    channel = configured_channel()
    if not channel:
        return False
    client = SlackClient()
    await client.post_message(channel, summary_line(report),
                              blocks=watch_blocks(report,
                                                  job_titles=job_titles))
    return True


async def watch(
    storage: Storage,
    *,
    gmail=None,
    classifier=None,
    replier=None,
    lookback_days: int | None = None,
    limit: int | None = None,
    deliver: bool = True,
) -> WatchReport:
    """Read recent mail and reconcile it with the application pipeline.

    `gmail`, `classifier` and `replier` are injectable so the suite can
    run the real pass against a fake inbox and a scripted model — the
    tests must never reach a mailbox or a billed endpoint (CLAUDE.md 6).
    """
    cfg = _watcher_cfg()
    report = WatchReport()
    ledger = WatcherStore(storage)

    if gmail is None:
        from .gmail import GmailClient
        gmail = GmailClient()

    try:
        messages = await gmail.recent(
            lookback_days=lookback_days or cfg.lookback_days,
            limit=limit or cfg.max_messages,
            extra_query=cfg.query,
        )
    except Exception as exc:
        log.warning("watcher: could not read the mailbox: %s", exc)
        report.errors.append(f"mailbox unreadable: {exc}")
        return report

    applications = await storage.tracked_jobs()
    by_id = {row["id"]: row for row in applications}
    thread_index = await ledger.thread_index()

    for message in messages:
        if await ledger.seen(message.gmail_id):
            report.already_seen += 1
            continue
        report.fetched += 1
        try:
            processed = await _process(
                message, storage=storage, ledger=ledger,
                applications=applications, by_id=by_id,
                thread_index=thread_index, classifier=classifier,
                replier=replier)
            report.processed.append(processed)
        except Exception as exc:
            # One message must never cost the pass.
            log.exception("watcher: message %s failed", message.gmail_id)
            report.errors.append(f"{message.gmail_id}: {exc}")

    if deliver and (report.advanced or report.unmatched
                    or any(p.reply for p in report.processed)):
        try:
            titles = {jid: (f"{row.get('company_display') or row.get('company')}"
                            f" — {row.get('title', '')}")
                      for jid, row in by_id.items()}
            await deliver_report(report, job_titles=titles)
        except Exception as exc:
            log.warning("watcher: Slack delivery failed: %s", exc)
            report.errors.append(f"slack delivery failed: {exc}")

    return report


async def _process(
    message, *, storage: Storage, ledger: WatcherStore,
    applications: list[dict], by_id: dict, thread_index: dict,
    classifier, replier,
) -> ProcessedMessage:
    if classifier is None:
        from karani.qualification import get_qualifier
        classifier = get_qualifier(task="email")

    classification = await classify_message(classifier, message)
    match = match_message(message, applications, thread_index=thread_index)

    job_row = by_id.get(match.job_id) if match.job_id else None
    status_before = (job_row or {}).get("application_status") or ""
    status_after = ""

    if match.confidence == "high" and job_row is not None:
        thread_index[message.thread_id] = match.job_id
        status_after = await _advance(
            storage, job_id=match.job_id, label=classification.label,
            status_before=status_before, subject=message.subject)
        if status_after:
            job_row["application_status"] = status_after

    reply = None
    if classification.label in ("interview_invite", "assessment",
                                "scheduling", "offer", "recruiter_outreach"):
        reply = await draft_reply(replier or classifier, message,
                                  classification, job_row)

    await ledger.record(
        message.gmail_id, thread_id=message.thread_id,
        from_address=message.from_address, from_domain=message.from_domain,
        subject=message.subject, received_at=message.received_at,
        label=classification.label, confidence=classification.confidence,
        job_id=match.job_id, matched_by=match.matched_by,
        match_confidence=match.confidence, status_after=status_after,
        reply_drafted=reply is not None)

    return ProcessedMessage(
        message=message, classification=classification, match=match,
        status_before=status_before, status_after=status_after, reply=reply)


async def _advance(storage: Storage, *, job_id: int, label: str,
                   status_before: str, subject: str) -> str:
    """Write the transition. Every branch is idempotent-safe: the stage
    log is append-only and the status is recomputed from the ladder."""
    if label in _LOGGED:
        await storage.add_stage(job_id, label, notes=subject[:200])

    status = next_status(label, status_before)
    if not status:
        return ""
    await storage.set_application_status(job_id, status)
    if label in ("rejection", "offer"):
        await storage.set_outcome(job_id,
                                  "rejection" if label == "rejection"
                                  else "offer")
    try:
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
    except Exception as exc:  # pragma: no cover - best-effort mirror
        log.warning("watcher: notion mirror failed for %s: %s", job_id, exc)
    return status
