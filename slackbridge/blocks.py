"""Block Kit renderers for pushes. Command replies stay mrkdwn text —
blocks are reserved for the structured pushes (digest, actions)."""
from __future__ import annotations


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _header(text: str) -> dict:
    return {"type": "header",
            "text": {"type": "plain_text", "text": text[:150]}}


def _comp(row: dict) -> str:
    lo, hi = row.get("comp_min_usd"), row.get("comp_max_usd")
    if lo and hi:
        return f"${lo:,}–${hi:,}"
    if lo:
        return f"${lo:,}+"
    return "comp n/a"


def digest_blocks(rows: list[dict], *, limit: int = 10) -> list[dict]:
    if not rows:
        return [_section("No qualified roles waiting — run `qualify` or "
                         "check back after the next ingest.")]
    blocks = [_header(f"karani digest — {len(rows)} role(s)")]
    for r in rows[:limit]:
        fit = r.get("fit_score") or "?"
        blocks.append(_section(
            f"*[{r.get('id')}]* *{r.get('company_display') or ''}* — "
            f"{r.get('title') or ''}\n"
            f"fit *{fit}* · {_comp(r)} · <{r.get('apply_url')}|posting>\n"
            f"_reply:_ `verdict {r.get('id')} apply` / `skip` / `later`"
        ))
    return blocks


def actions_blocks(buckets: dict) -> list[dict]:
    blocks = [_header("karani — next actions")]
    labels = [
        ("review", "Review (reply `verdict <id> <apply|skip|later>`)"),
        ("to_draft", "Draft next (reply `draft <id>`)"),
        ("to_submit", "Ready to submit (reply `status <id> applied` once sent)"),
        ("follow_up", "Follow up (reply `followup <id>` for a drafted note)"),
    ]
    empty = True
    for key, label in labels:
        items = buckets.get(key) or []
        if not items:
            continue
        empty = False
        lines = []
        for a in items[:8]:
            marker = " *FAST LANE — apply today*" if a.get("fast_lane") else ""
            age = (f" · posted {a['posted_days_ago']}d ago"
                   if a.get("posted_days_ago") is not None else "")
            waited = (f" · applied {a['applied_days_ago']}d ago"
                      if a.get("applied_days_ago") is not None else "")
            fit = f"fit {a['fit_score']}" if a.get("fit_score") else "unscored"
            lines.append(f"• *[{a['id']}]* {a['company']} — {a['title']} "
                         f"({fit}{age}{waited}){marker}")
        blocks.append(_section(f"*{label}*\n" + "\n".join(lines)))
    if empty:
        blocks.append(_section("Nothing pending. The pipeline is idle."))
    return blocks
