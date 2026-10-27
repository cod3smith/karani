"""The Slack card that arrives when an interview gets scheduled.

Deliberately not in `slackbridge/blocks.py`: the coach owns the shape
of its own card, and the two modules are edited by different concerns.
The helpers are small enough that sharing them would cost more than it
saves.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _header(text: str) -> dict:
    return {"type": "header",
            "text": {"type": "plain_text", "text": text[:150]}}


def _when(scheduled_at: datetime | None) -> str:
    if scheduled_at is None:
        return "date not recorded"
    when = scheduled_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (when.date() - datetime.now(timezone.utc).date()).days
    if days > 1:
        horizon = f"in {days} days"
    elif days == 1:
        horizon = "tomorrow"
    elif days == 0:
        horizon = "today"
    else:
        horizon = f"{abs(days)} days ago"
    return f"{when.date().isoformat()} ({horizon})"


def prep_blocks(job_row: dict, pack, *, artifacts: dict | None = None,
                path: str = "") -> list[dict]:
    """Enough to act on from a phone: when it is, what they will ask,
    and a link to the document."""
    company = job_row.get("company_display") or job_row.get("company") or ""
    job_id = job_row.get("id")
    blocks = [
        _header(f"Interview prep — {company}"),
        _section(f"*[{job_id}]* {company} — {job_row.get('title', '')}\n"
                 f"*{pack.stage}* · {_when(pack.scheduled_at)} · "
                 f"{len(pack.likely_questions)} likely questions · "
                 f"{len(pack.stories)} stories from your own record"),
    ]
    if pack.company_brief:
        blocks.append(_section(f"*Brief:* {pack.company_brief}"))

    top = pack.likely_questions[:4]
    if top:
        lines = []
        for question in top:
            answer = (f"  _answer with: {question.story_title}_"
                      if question.story_title else "")
            lines.append(f"• {question.question}{answer}")
        blocks.append(_section("*They will ask:*\n" + "\n".join(lines)))

    if pack.comp is not None:
        from .comp import _usd
        blocks.append(_section(
            f"*Comp:* posted {_usd(pack.comp.posted_min_usd)}"
            f"–{_usd(pack.comp.posted_max_usd)} · anchor "
            f"${pack.comp.anchor_usd:,}"
            + (f"\n{pack.comp_notes}" if pack.comp_notes else "")))

    if pack.red_flags:
        blocks.append(_section("*Walk in knowing:* "
                               + " · ".join(pack.red_flags[:3])))

    links = " · ".join(
        f"<{meta['url']}|{name}>"
        for name, meta in (artifacts or {}).items() if meta.get("url"))
    if links:
        blocks.append(_section(f"*Documents:* {links}"))
    elif path:
        blocks.append(_section(f"*Pack:* `{path}`"))
    return blocks
