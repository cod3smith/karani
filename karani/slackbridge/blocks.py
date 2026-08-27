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


def _button(action_id: str, text: str, value: str,
            style: str | None = None) -> dict:
    b: dict = {"type": "button", "action_id": action_id, "value": value,
               "text": {"type": "plain_text", "text": text}}
    if style:
        b["style"] = style
    return b


def pack_blocks(job_row: dict, pkg, *, artifacts: dict | None = None,
                voice: dict | None = None) -> list[dict]:
    """An application pack for review: summary, cover letter, artifact
    links (tweak-and-submit), voice score, and the review buttons.
    Karani drafts; the buttons drive Kelyn's flow — the actual submission
    always happens on the company portal (non-goal: never auto-submit)."""
    job_id = job_row.get("id")
    company = job_row.get("company_display") or job_row.get("company") or ""
    letter = (pkg.cover_letter or "").strip()
    if len(letter) > 2600:
        letter = letter[:2600] + "\n[truncated — full text in the draft file]"
    coverage = (f" · keyword coverage {pkg.keyword_coverage:.0%}"
                if pkg.keyword_coverage is not None else "")
    voice_note = ""
    if voice and voice.get("after"):
        voice_note = f" · voice {voice['after'].get('score', '?')}/100"
    blocks = [
        _header(f"Application pack — {company}"),
        _section(
            f"*[{job_id}]* {company} — {job_row.get('title', '')}\n"
            f"fit *{job_row.get('fit_score', '?')}* · {_comp(job_row)}"
            f"{coverage}{voice_note} · <{job_row.get('apply_url')}|posting>\n"
            f"{len(pkg.tailored_bullets)} tailored bullets · "
            f"{len(pkg.application_answers)} answers in the draft file"
        ),
    ]
    if artifacts:
        links = " · ".join(
            f"<{meta['url']}|{name.replace('_', ' ').removesuffix('.md')}>"
            for name, meta in artifacts.items() if meta.get("url")
        )
        if links:
            blocks.append(_section(f"*Tweak and submit:* {links}"))
    blocks += [
        _section(f"*Cover letter:*\n>>> {letter}"),
        {
            "type": "actions",
            "block_id": f"pack:{job_id}",
            "elements": [
                _button("pack_approve", "Approve pack", str(job_id),
                        style="primary"),
                _button("pack_skip", "Skip role", str(job_id),
                        style="danger"),
                _button("pack_applied_warm", "I applied (warm)", str(job_id)),
                _button("pack_applied_cold", "I applied (cold)", str(job_id)),
            ],
        },
    ]
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
