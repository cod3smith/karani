"""Slack rendering for a watch pass.

Lives here rather than in `slackbridge/blocks.py` so a new subsystem
does not edit a file other workstreams are in; it uses the same
`_section`/`_header` shapes the rest of the bridge does.

Deliberately button-free. Every button in karani maps to a state
transition the human is confirming (ADR 0012); the watcher has already
made the only transitions it is confident about, and what is left —
a reply to send, a message it could not place — is work that happens
in a mail client, not in Slack.
"""
from __future__ import annotations

from .models import WatchReport

_LABEL_WORDS = {
    "rejection": "Rejection",
    "interview_invite": "Interview invite",
    "assessment": "Assessment",
    "scheduling": "Scheduling",
    "offer": "OFFER",
    "recruiter_outreach": "Recruiter outreach",
    "irrelevant": "Irrelevant",
    "unclassified": "Unclassified",
}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text",
                                       "text": text[:150]}}


def watch_blocks(report: WatchReport, *, job_titles: dict | None = None,
                 limit: int = 10) -> list[dict]:
    job_titles = job_titles or {}
    advanced = report.advanced
    drafts = [p for p in report.processed if p.reply]
    unplaced = [p for p in report.unmatched
                if p.classification.label not in
                ("irrelevant", "unclassified")]

    blocks = [_header("karani — inbox")]
    if not (advanced or drafts or unplaced):
        blocks.append(_section(
            f"Read {report.fetched} message(s); nothing needed a decision."))
        return blocks

    if advanced:
        lines = [
            f"• *[{p.match.job_id}]* {job_titles.get(p.match.job_id, '')} — "
            f"*{_LABEL_WORDS.get(p.classification.label, p.classification.label)}*"
            f" · `{p.status_before or 'new'}` → `{p.status_after}`"
            for p in advanced[:limit]
        ]
        blocks.append(_section("*Moved*\n" + "\n".join(lines)))

    for processed in drafts[:limit]:
        label = _LABEL_WORDS.get(processed.classification.label, "")
        who = processed.message.from_name or processed.message.from_address
        where = job_titles.get(processed.match.job_id, "unmatched")
        blocks.append(_section(
            f"*Reply draft — {label}* · {where}\n"
            f"_from {who}: {processed.message.subject}_\n"
            f"*{processed.reply.subject}*\n>>> {processed.reply.body}"))

    if unplaced:
        lines = [
            f"• *{_LABEL_WORDS.get(p.classification.label, '')}* from "
            f"{p.message.from_name or p.message.from_address} — "
            f"_{p.message.subject}_"
            + (f" (candidates: {', '.join(str(c) for c in p.match.candidates)})"
               if p.match.candidates else "")
            for p in unplaced[:limit]
        ]
        blocks.append(_section(
            "*Could not place — record these by hand*\n" + "\n".join(lines)))

    if report.errors:
        blocks.append(_section("*Errors*\n" + "\n".join(
            f"• `{e}`" for e in report.errors[:5])))
    return blocks


def summary_line(report: WatchReport) -> str:
    return (f"karani inbox: {report.fetched} read, "
            f"{len(report.advanced)} advanced, "
            f"{len([p for p in report.processed if p.reply])} reply draft(s), "
            f"{len(report.unmatched)} unplaced")
