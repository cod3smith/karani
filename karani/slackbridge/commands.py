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

from karani.ingestion.digest import render as render_digest
from karani.ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
from karani.ingestion.storage import Storage, _days_ago

log = logging.getLogger(__name__)

HELP = """\
*karani commands*
`actions` — prioritized worklist  ·  `digest [n]` — top roles
`job <id>` — detail  ·  `funnel` — conversion rates  ·  `stats` — counts
`verdict <id> <apply|shortlist|later|skip|applied>` — react to a suggestion
`status <id> <state> [warm|cold]` · `stage <id> <name> [notes]` · `outcome <id> <o>`
`asked <id> <question>` — bank an interview question for future preps
`qualify [n]` — LLM-qualify pending (billed)
`draft <id>` · `prep <id>` · `followup <id>` — generate materials (billed)
`intel <company>` — company dossier  ·  `warm <company>` — warm paths
`remember <fact>` · `recall <query>` — memory layer
`sync` — push tracked applications to the Notion board
"""


def _default_make_qualifier(provider=None, model=None):
    from karani.qualification import get_qualifier
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
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
        return f"Recorded: job {job_id} → {args[1].lower()}."

    if cmd == "status":
        job_id = _need_id(args)
        if len(args) < 2:
            return f"Usage: `status <id> <{('|'.join(sorted(Storage.APPLICATION_STATUSES)))}> [warm|cold]`"
        warm = None
        if len(args) > 2 and args[2].lower() in ("warm", "cold"):
            warm = args[2].lower() == "warm"
        await storage.set_application_status(job_id, args[1].lower(),
                                             warm_path=warm)
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
        note = "" if warm is None else f" ({args[2].lower()} path)"
        hint = ("" if args[1].lower() != "applied" or warm is not None
                else " Tip: add `warm` or `cold` next time — it feeds the "
                     "warm-vs-cold funnel split.")
        return f"Job {job_id} → {args[1].lower()}{note}.{hint}"

    if cmd == "stage":
        job_id = _need_id(args)
        if len(args) < 2:
            return "Usage: `stage <id> <name> [notes]`"
        await storage.add_stage(job_id, args[1], " ".join(args[2:]))
        return (f"Stage `{args[1]}` logged on job {job_id}. "
                f"Log their questions with `asked {job_id} <question>` — "
                f"they feed future prep packs.")

    if cmd == "asked":
        job_id = _need_id(args)
        if len(args) < 2:
            return "Usage: `asked <id> <the question they asked>`"
        if memory is None:
            return "Memory layer is off."
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        result = await memory.remember_question(row, " ".join(args[1:]))
        return ("Already in the question bank."
                if result.get("deduped")
                else f"Question banked for {row.get('company_display')}.")

    if cmd == "outcome":
        job_id = _need_id(args)
        if len(args) < 2:
            return f"Usage: `outcome <id> <{('|'.join(sorted(Storage.OUTCOMES)))}>`"
        await storage.set_outcome(job_id, args[1].lower())
        row = await storage.get_job(job_id)
        if row and memory is not None:
            await memory.remember_outcome(row, args[1].lower())
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
        return f"Outcome recorded: job {job_id} → {args[1].lower()}."

    if cmd == "qualify":
        from karani.qualification import qualify_pending
        limit = int(args[0]) if args and args[0].isdigit() else 5
        resume = load_resume()
        stats = await qualify_pending(storage, make_qualifier(), resume,
                                      limit=limit, memory=memory)
        return (f"Qualified {stats.fetched} row(s): "
                f"{stats.qualified} qualified, {stats.maybe} maybe, "
                f"{stats.skipped} skip, {len(stats.errors)} errors.")

    if cmd == "draft":
        from karani.drafting import build_application_pack
        job_id = _need_id(args)
        row = await storage.get_job(job_id)
        if not row:
            return f"No job with id={job_id}."
        pack = await build_application_pack(
            make_qualifier(), storage, job_row=row,
            resume_markdown=load_resume().raw_markdown,
        )
        if pack.failed:
            return "Draft failed (malformed LLM output) — try again."
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
        pkg = pack.pkg
        letter = pkg.cover_letter.strip()
        if len(letter) > 2600:
            letter = letter[:2600] + "\n[truncated — full text in the file]"
        links = " · ".join(
            f"<{m['url']}|{n.replace('_', ' ').removesuffix('.md')}>"
            for n, m in pack.artifacts.items() if m.get("url"))
        voice = ""
        if pack.voice.get("after"):
            voice = f", voice {pack.voice['after']['score']}/100"
        return (f"Drafted `{pack.draft_path}` — "
                f"{len(pkg.cover_letter.split())} words, keyword coverage "
                f"{pkg.keyword_coverage}{voice}."
                + (f"\n*Tweak and submit:* {links}" if links else "")
                + f"\n\n*Cover letter:*\n>>> {letter}")

    if cmd == "prep":
        from karani.drafting import prep_for_job
        from karani.intel import dossier_text, get_company_intel
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
        likely = "\n".join(f"• {q.question}"
                           for q in pkg.likely_questions[:6])
        to_ask = "\n".join(f"• {q.question}"
                           for q in pkg.questions_to_ask[:5])
        return (f"Prep pack: `{path}`\n\n"
                f"*Brief:* {pkg.company_brief[:600]}\n\n"
                f"*They'll likely ask:*\n{likely or '(none)'}\n\n"
                f"*Ask them:*\n{to_ask or '(none)'}\n\n"
                f"Full pack (answers + warm openers) in the file.")

    if cmd == "followup":
        from karani.drafting import followup_for_job
        from karani.intel import dossier_text, get_company_intel
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
        from karani.intel import dossier_text, get_company_intel
        if not args:
            return "Usage: `intel <company>`"
        intel = await get_company_intel(storage, " ".join(args))
        cached = " (cached)" if intel.get("cached") else ""
        return f"*Dossier{cached}:*\n{dossier_text(intel)[:2800]}"

    if cmd == "warm":
        from karani.intel import find_warm_paths
        if not args:
            return "Usage: `warm <company>`"
        paths = await find_warm_paths(storage, " ".join(args))
        if not paths:
            return "No public warm-path candidates found."
        return "*Warm-path candidates:*\n" + "\n".join(
            f"• {p['login']} — {p['url']}" for p in paths[:10])

    if cmd == "sync":

        from karani.notionsync import NotionClient, NotionError, sync_jobs
        from karani.notionsync import configured_database_id
        database_id = configured_database_id()
        if not database_id:
            return ("Notion is not configured — set NOTION_TOKEN and "
                    "NOTION_DATABASE_ID (see README).")
        try:
            result = await sync_jobs(storage, NotionClient(), database_id)
        except NotionError as e:
            return f"Notion sync failed: `{e}`"
        return (f"Notion board synced: {result['tracked']} tracked, "
                f"{result['created']} created, {result['updated']} updated"
                + (f", {result['errors']} errors" if result["errors"] else "")
                + ".")

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
