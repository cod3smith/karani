"""Slack command dispatcher — the two-way half of the bridge.

Same thin-adapter rule as the CLI and MCP server: parse, call the shared
Storage/runner/memory functions, format mrkdwn. Any new capability goes
in those layers first, then gets a verb here.

Every handler catches its own errors and returns a message — a typo in a
Slack DM must never kill the listener.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ingestion.digest import render as render_digest
from ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
from ingestion.storage import Storage, _days_ago

log = logging.getLogger(__name__)

HELP = """\
*karani commands*
`actions` — prioritized worklist  ·  `digest [n]` — top roles
`job <id>` — detail  ·  `funnel` — conversion rates  ·  `stats` — counts
`verdict <id> <apply|shortlist|later|skip|applied>` — react to a suggestion
`status <id> <state>` · `stage <id> <name> [notes]` · `outcome <id> <o>`
`qualify [n]` — LLM-qualify pending (billed)
`draft <id>` · `prep <id>` · `followup <id>` — generate materials (billed)
`intel <company>` — company dossier  ·  `warm <company>` — warm paths
`remember <fact>` · `recall <query>` — memory layer
"""


def _default_make_qualifier(provider=None, model=None):
    from qualification import get_qualifier
    return get_qualifier(provider=provider, model=model)


def _default_load_resume(path: str | None = None) -> ResumeProfile:
    return ResumeProfile.from_file(path or DEFAULT_RESUME_PATH)


async def handle_command(
    text: str, *,
    storage: Storage,
    memory=None,
    make_qualifier=_default_make_qualifier,
    load_resume=_default_load_resume,
) -> str:
    """Parse one inbound message and return the mrkdwn reply."""
    words = (text or "").strip().split()
    if not words:
        return HELP
    cmd, args = words[0].lower(), words[1:]
    try:
        return await _dispatch(cmd, args, storage, memory,
                               make_qualifier, load_resume)
    except Exception as exc:
        log.exception("slack command failed: %r", text)
        return f"That failed: `{exc}`. Try `help`."


def _need_id(args: list[str]) -> int:
    if not args or not args[0].isdigit():
        raise ValueError("expected a numeric job id, e.g. `job 123`")
    return int(args[0])


async def _dispatch(cmd, args, storage, memory,
                    make_qualifier, load_resume) -> str:
    if cmd in ("help", "?"):
        return HELP

    if cmd == "actions":
        buckets = await storage.next_actions()
        lines = []
        for name, items in buckets.items():
            if not items:
                continue
            lines.append(f"*{name}* ({len(items)}):")
            for a in items[:8]:
                marker = "  FAST LANE" if a.get("fast_lane") else ""
                lines.append(f"  [{a['id']}] fit={a['fit_score'] or '-'} "
                             f"{a['company']} — {a['title']}{marker}")
        return "\n".join(lines) or "Nothing pending."

    if cmd == "digest":
        limit = int(args[0]) if args and args[0].isdigit() else 10
        rows = await storage.top_qualified(limit=limit)
        return render_digest(rows, "text")

    if cmd == "funnel":
        f = await storage.funnel_stats()
        t = f["totals"]
        lines = [
            f"applied={t['applied']} responded={t['responded']} "
            f"interviewed={t['interviewed']} offers={t['offers']}",
            f"response_rate={t['response_rate']:.1%} "
            f"interview_rate={t['interview_rate']:.1%}",
        ]
        cov = f["autopsy"]["keyword_coverage"]
        if cov["responded_avg"] is not None or cov["silent_avg"] is not None:
            lines.append(f"keyword coverage: responded={cov['responded_avg']} "
                         f"vs silent={cov['silent_avg']}")
        return "\n".join(lines)

    if cmd == "stats":
        s = await storage.stats()
        return "  ".join(f"{k}={v}" for k, v in dict(s).items())

    if cmd == "job":
        job_id = _need_id(args)
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        return (f"*[{row['id']}]* {row.get('company_display')} — "
                f"{row.get('title')}\n"
                f"fit={row.get('fit_score')} verdict={row.get('verdict')} "
                f"status={row.get('application_status')} "
                f"outcome={row.get('outcome')}\n{row.get('apply_url', '')}")

    if cmd == "verdict":
        job_id = _need_id(args)
        if len(args) < 2:
            return "Usage: `verdict <id> <apply|shortlist|later|skip|applied>`"
        await storage.set_user_verdict(job_id, args[1].lower())
        row = await storage.get_job(job_id)
        if row and memory is not None:
            await memory.remember_verdict(row, args[1].lower())
        return f"Recorded: job {job_id} → {args[1].lower()}."

    if cmd == "status":
        job_id = _need_id(args)
        if len(args) < 2:
            return f"Usage: `status <id> <{('|'.join(sorted(Storage.APPLICATION_STATUSES)))}>`"
        await storage.set_application_status(job_id, args[1].lower())
        return f"Job {job_id} → {args[1].lower()}."

    if cmd == "stage":
        job_id = _need_id(args)
        if len(args) < 2:
            return "Usage: `stage <id> <name> [notes]`"
        await storage.add_stage(job_id, args[1], " ".join(args[2:]))
        return f"Stage `{args[1]}` logged on job {job_id}."

    if cmd == "outcome":
        job_id = _need_id(args)
        if len(args) < 2:
            return f"Usage: `outcome <id> <{('|'.join(sorted(Storage.OUTCOMES)))}>`"
        await storage.set_outcome(job_id, args[1].lower())
        row = await storage.get_job(job_id)
        if row and memory is not None:
            await memory.remember_outcome(row, args[1].lower())
        return f"Outcome recorded: job {job_id} → {args[1].lower()}."

    if cmd == "qualify":
        from qualification import qualify_pending
        limit = int(args[0]) if args and args[0].isdigit() else 5
        resume = load_resume()
        stats = await qualify_pending(storage, make_qualifier(), resume,
                                      limit=limit, memory=memory)
        return (f"Qualified {stats.fetched} row(s): "
                f"{stats.qualified} qualified, {stats.maybe} maybe, "
                f"{stats.skipped} skip, {len(stats.errors)} errors.")

    if cmd == "draft":
        from drafting import draft_for_job
        job_id = _need_id(args)
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        pkg, path = await draft_for_job(
            make_qualifier(), resume=load_resume().raw_markdown,
            job_row=row, qualification=row.get("qualification"),
        )
        await storage.record_draft(job_id, str(path),
                                   prompt_version=pkg.prompt_version,
                                   model=pkg.model,
                                   keyword_coverage=pkg.keyword_coverage)
        return (f"Drafted `{path}` — {len(pkg.cover_letter.split())}-word "
                f"letter, keyword coverage {pkg.keyword_coverage}.")

    if cmd == "prep":
        from drafting import prep_for_job
        from intel import dossier_text, get_company_intel
        job_id = _need_id(args)
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        company = row.get("company_display") or row.get("company") or ""
        intel = await get_company_intel(storage, company)
        bank = []
        if memory is not None:
            bank = [m["content"] for m in await memory.recall(
                f"{company} interview questions", kind="question", limit=10)]
        pkg, path = await prep_for_job(
            make_qualifier(), resume=load_resume().raw_markdown, job_row=row,
            qualification=row.get("qualification"),
            dossier=dossier_text(intel), question_bank=bank,
        )
        return (f"Prep pack: `{path}` — {len(pkg.likely_questions)} likely "
                f"questions, {len(pkg.questions_to_ask)} to ask, "
                f"{len(pkg.warm_openers)} warm openers.")

    if cmd == "followup":
        from drafting import followup_for_job
        from intel import dossier_text, get_company_intel
        job_id = _need_id(args)
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        company = row.get("company_display") or row.get("company") or ""
        intel = await get_company_intel(storage, company)
        days = _days_ago(row.get("applied_at"),
                         datetime.now(timezone.utc)) or 0
        pkg, path = await followup_for_job(
            make_qualifier(), job_row=row, days_since_applied=days,
            dossier=dossier_text(intel),
        )
        return f"Follow-up note: `{path}`\n\n> {pkg.note}"

    if cmd == "intel":
        from intel import dossier_text, get_company_intel
        if not args:
            return "Usage: `intel <company>`"
        intel = await get_company_intel(storage, " ".join(args))
        cached = " (cached)" if intel.get("cached") else ""
        return f"*Dossier{cached}:*\n{dossier_text(intel)[:2800]}"

    if cmd == "warm":
        from intel import find_warm_paths
        if not args:
            return "Usage: `warm <company>`"
        paths = await find_warm_paths(storage, " ".join(args))
        if not paths:
            return "No public warm-path candidates found."
        return "*Warm-path candidates:*\n" + "\n".join(
            f"• {p['login']} — {p['url']}" for p in paths[:10])

    if cmd == "remember":
        if not args:
            return "Usage: `remember <fact>`"
        if memory is None:
            return "Memory layer is off."
        result = await memory.remember(" ".join(args), "preference",
                                       source="manual")
        return ("Already knew that." if result.get("deduped")
                else f"Stored (memory id={result.get('id')}).")

    if cmd == "recall":
        if not args:
            return "Usage: `recall <query>`"
        if memory is None:
            return "Memory layer is off."
        rows = await memory.recall(" ".join(args), limit=5)
        if not rows:
            return "Nothing relevant in memory."
        return "\n".join(f"• ({r.get('kind', '?')}) {r.get('content', '')}"
                         for r in rows)

    return f"Unknown command `{cmd}`.\n\n{HELP}"
