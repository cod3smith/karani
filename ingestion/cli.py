"""CLI entry: `python -m ingestion.cli {run,qualify,digest,draft,verdict,
status,stage,outcome,discover,stats,actions,funnel,remember,recall,sweep}`"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import settings
from .digest import render as render_digest
from .discovery import probe_unpromoted
from .orchestrator import run
from .resume import DEFAULT_RESUME_PATH, ResumeProfile
from .storage import Storage


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# -----------------------  run  -----------------------

async def _run() -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        stats = await run(storage)
        print(
            f"fetched={stats.fetched} inserted={stats.inserted} "
            f"updated={stats.updated} passed_prefilter={stats.passed_prefilter} "
            f"stale_closed={stats.stale_closed} errors={len(stats.errors)}"
        )
        if stats.dropped_by_reason:
            print("dropped by reason:")
            for reason, count in stats.dropped_by_reason.most_common(15):
                print(f"  {count:>5}  {reason}")
        if stats.per_source:
            print("per-source outcomes:")
            for key, out in sorted(stats.per_source.items()):
                marker = "!" if out.errors else " "
                print(f"  {marker} {key:<40} fetched={out.fetched:>4}"
                      f"  errors={out.errors}")
        if stats.errors:
            for e in stats.errors[:10]:
                print(f"  UPSERT ERR: {e}", file=sys.stderr)
        print(f"db: {dict(await storage.stats())}")
        return 0 if not stats.errors else 1
    finally:
        await storage.close()


# -----------------------  qualify  -----------------------

async def _qualify(limit: int, concurrency: int, provider: str | None,
                   model: str | None, resume_path: str,
                   agent_mode: bool) -> int:
    _configure_logging()
    from qualification import get_qualifier, qualify_pending

    resume = ResumeProfile.from_file(resume_path)
    print(f"resume loaded: {resume.word_count} words, hash={resume.hash[:12]}")

    client = get_qualifier(provider=provider, model=model)
    mode = "AGENT (tools)" if agent_mode else "single-turn"
    print(f"provider={type(client).__name__} model={client.model_name} mode={mode}")

    if agent_mode and not hasattr(client, "chat_turn"):
        print("ERROR: --agent requires an OpenRouter client "
              "(other providers do not implement chat_turn yet).",
              file=sys.stderr)
        return 2

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        from memory import MemoryManager
        stats = await qualify_pending(
            storage, client, resume,
            limit=limit, concurrency=concurrency,
            agent_mode=agent_mode,
            memory=MemoryManager(storage),
        )
        print(
            f"fetched={stats.fetched} qualified={stats.qualified} "
            f"maybe={stats.maybe} skipped={stats.skipped} "
            f"errors={len(stats.errors)}"
        )
        for e in stats.errors[:10]:
            print(f"  ERR: {e}", file=sys.stderr)
        return 0 if not stats.errors else 1
    finally:
        await storage.close()


# -----------------------  digest  -----------------------

async def _digest(limit: int, fmt: str, output: str | None) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        rows = await storage.top_qualified(limit=limit)
        content = render_digest(rows, fmt)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"wrote {output} ({len(content):,} bytes, {len(rows)} rows)")
        else:
            print(content)
        return 0
    finally:
        await storage.close()


# -----------------------  draft  -----------------------

async def _draft(job_id: int, provider: str | None, model: str | None,
                 resume_path: str, output_path: str | None) -> int:
    _configure_logging()
    from qualification import get_qualifier
    from drafting import draft_for_job

    resume = ResumeProfile.from_file(resume_path)
    client = get_qualifier(provider=provider, model=model)
    print(f"drafting for job_id={job_id} via {type(client).__name__}"
          f"/{client.model_name}")

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        pkg, path = await draft_for_job(
            client, resume=resume.raw_markdown,
            job_row=row, qualification=row.get("qualification"),
            output_path=output_path,
        )
        # Bookkeeping: move to `drafting` + persist path and prompt/model
        # provenance so funnel_stats can A/B drafts by version.
        await storage.record_draft(job_id, str(path),
                                   prompt_version=pkg.prompt_version,
                                   model=pkg.model)
        print(f"drafted: {path}")
        print(f"  cover_letter_words={len(pkg.cover_letter.split())} "
              f"bullets={len(pkg.tailored_bullets)} "
              f"answers={len(pkg.application_answers)}")
        return 0
    finally:
        await storage.close()


# -----------------------  verdict / status / stage / outcome  -----------------------

async def _verdict(job_id: int, verdict: str) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        await storage.set_user_verdict(job_id, verdict)
        print(f"job_id={job_id} user_verdict={verdict}")
        row = await storage.get_job(job_id)
        if row:
            from memory import MemoryManager
            await MemoryManager(storage).remember_verdict(row, verdict)
    finally:
        await storage.close()
    return 0


async def _status(job_id: int, status: str) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        await storage.set_application_status(job_id, status)
        print(f"job_id={job_id} application_status={status}")
    finally:
        await storage.close()
    return 0


async def _stage(job_id: int, stage: str, notes: str) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        await storage.add_stage(job_id, stage, notes)
        print(f"job_id={job_id} + stage={stage!r} notes={notes!r}")
    finally:
        await storage.close()
    return 0


async def _outcome(job_id: int, outcome: str) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        await storage.set_outcome(job_id, outcome)
        print(f"job_id={job_id} outcome={outcome}")
        row = await storage.get_job(job_id)
        if row:
            from memory import MemoryManager
            await MemoryManager(storage).remember_outcome(row, outcome)
    finally:
        await storage.close()
    return 0


# -----------------------  actions / funnel  -----------------------

async def _actions(review_limit: int, follow_up_days: int) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        buckets = await storage.next_actions(
            review_limit=review_limit, follow_up_days=follow_up_days,
        )
        for name, items in buckets.items():
            print(f"\n{name} ({len(items)}):")
            for a in items:
                age = (f"  posted {a['posted_days_ago']}d ago"
                       if a.get("posted_days_ago") is not None else "")
                extra = (f"  applied {a['applied_days_ago']}d ago"
                         if a.get("applied_days_ago") is not None else "")
                print(f"  [{a['id']}] fit={a['fit_score'] or '-':<3} "
                      f"{a['company']} — {a['title']}{age}{extra}")
    finally:
        await storage.close()
    return 0


async def _funnel() -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        f = await storage.funnel_stats()
        t = f["totals"]
        print(f"applied={t['applied']} responded={t['responded']} "
              f"interviewed={t['interviewed']} offers={t['offers']} "
              f"ghosted={t['ghosted']}")
        print(f"response_rate={t['response_rate']:.1%} "
              f"interview_rate={t['interview_rate']:.1%} "
              f"offer_rate={t['offer_rate']:.1%}")
        for section in ("by_fit_band", "by_source",
                        "by_qualify_prompt", "by_draft_prompt"):
            if f[section]:
                print(f"\n{section}:")
                for key, b in sorted(f[section].items()):
                    print(f"  {key:<20} applied={b['applied']:<4} "
                          f"responded={b['responded']:<4} "
                          f"rate={b['response_rate']:.1%}")
    finally:
        await storage.close()
    return 0


# -----------------------  remember / recall  -----------------------

async def _remember(content: str, kind: str, company: str | None) -> int:
    _configure_logging()
    from memory import MemoryManager
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        result = await MemoryManager(storage).remember(
            content, kind, company=company,
        )
        state = "deduped" if result.get("deduped") else "stored"
        print(f"{state} memory id={result.get('id')} mode={result.get('mode')}")
    finally:
        await storage.close()
    return 0


async def _recall(query: str, kind: str | None, company: str | None,
                  limit: int) -> int:
    _configure_logging()
    from memory import MemoryManager
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        rows = await MemoryManager(storage).recall(
            query, kind=kind, company=company, limit=limit,
        )
        if not rows:
            print("no matching memories")
            return 0
        for r in rows:
            scope = f" [{r['company']}]" if r.get("company") else ""
            print(f"  ({r.get('kind', '?')}){scope} {r.get('content', '')}")
    finally:
        await storage.close()
    return 0


# -----------------------  discover / stats / sweep  -----------------------

async def _discover(limit: int) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        outcomes = await probe_unpromoted(storage, limit=limit)
        if not outcomes:
            print("no unpromoted companies to probe")
            return 0
        hits = sum(1 for o in outcomes if o.get("ats"))
        print(f"probed={len(outcomes)} promoted={hits}")
        for o in outcomes:
            marker = "+" if o.get("ats") else " "
            slug_part = f" ({o.get('ats')}/{o.get('slug')})" if o.get("ats") else ""
            print(f"  {marker} {o['company']}{slug_part}")
        promoted = await storage.promoted_companies()
        if promoted:
            print(f"\ntotal promoted companies in DB: {len(promoted)}")
        return 0
    finally:
        await storage.close()


async def _stats() -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        s = await storage.stats()
        print("counts:")
        for k, v in dict(s).items():
            print(f"  {k:<12} {v}")
        pipe = await storage.pipeline_summary()
        if pipe:
            print("\napplication pipeline:")
            for status, n in sorted(pipe.items()):
                print(f"  {status:<12} {n}")
    finally:
        await storage.close()
    return 0


async def _sweep(days: int) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        closed = await storage.sweep_stale(days)
        print(f"stale_closed={closed} threshold_days={days}")
    finally:
        await storage.close()
    return 0


# -----------------------  argparse  -----------------------

def main() -> None:
    parser = argparse.ArgumentParser("ingestion")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="fetch → pre-filter → store → sweep → discover")

    q = sub.add_parser("qualify", help="LLM-qualify pending rows")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--concurrency", type=int, default=3)
    q.add_argument("--provider", type=str, default=None,
                   choices=[None, "openrouter", "anthropic", "local"])
    q.add_argument("--model", type=str, default=None)
    q.add_argument("--resume", type=str, default=DEFAULT_RESUME_PATH)
    q.add_argument("--agent", action="store_true",
                   help="tool-using agent loop (OpenRouter only)")

    d = sub.add_parser("digest", help="render top qualified roles")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--format", choices=["text", "md", "html"], default="text")
    d.add_argument("--output", type=str, default=None,
                   help="write to file instead of stdout")

    dr = sub.add_parser("draft", help="draft cover letter + tailored bullets + Q&A")
    dr.add_argument("job_id", type=int)
    dr.add_argument("--provider", type=str, default=None,
                    choices=[None, "openrouter", "anthropic", "local"])
    dr.add_argument("--model", type=str, default=None)
    dr.add_argument("--resume", type=str, default=DEFAULT_RESUME_PATH)
    dr.add_argument("--output", type=str, default=None,
                    help="output path; default drafts/<id>_<company>__<title>.md")

    v = sub.add_parser("verdict", help="record your reaction to a suggestion")
    v.add_argument("job_id", type=int)
    v.add_argument("verdict", choices=["apply", "shortlist", "later",
                                        "skip", "applied"])

    st = sub.add_parser("status", help="set application state machine status")
    st.add_argument("job_id", type=int)
    st.add_argument("status", choices=sorted(Storage.APPLICATION_STATUSES))

    sg = sub.add_parser("stage", help="log an interview or application stage")
    sg.add_argument("job_id", type=int)
    sg.add_argument("stage", type=str,
                    help="e.g. recruiter_screen, hiring_manager, technical, onsite")
    sg.add_argument("--notes", type=str, default="")

    oc = sub.add_parser("outcome", help="record final outcome")
    oc.add_argument("job_id", type=int)
    oc.add_argument("outcome", choices=sorted(Storage.OUTCOMES))

    disc = sub.add_parser("discover", help="probe unpromoted companies for ATS presence")
    disc.add_argument("--limit", type=int, default=10)

    sub.add_parser("stats", help="print DB counts + pipeline funnel")

    ac = sub.add_parser("actions", help="prioritized worklist: review, draft, submit, follow up")
    ac.add_argument("--review-limit", type=int, default=10)
    ac.add_argument("--follow-up-days", type=int, default=7)

    sub.add_parser("funnel", help="conversion funnel: response/interview/offer rates")

    rm = sub.add_parser("remember", help="store a fact in the memory layer")
    rm.add_argument("content", type=str)
    rm.add_argument("--kind", default="preference",
                    choices=sorted(Storage.MEMORY_KINDS))
    rm.add_argument("--company", type=str, default=None)

    rc = sub.add_parser("recall", help="query the memory layer")
    rc.add_argument("query", type=str)
    rc.add_argument("--kind", default=None,
                    choices=[None, *sorted(Storage.MEMORY_KINDS)])
    rc.add_argument("--company", type=str, default=None)
    rc.add_argument("--limit", type=int, default=5)

    sweep = sub.add_parser("sweep", help="mark stale jobs inactive")
    sweep.add_argument("--days", type=int, default=settings.stale_job_days)

    args = parser.parse_args()
    if args.cmd == "run":
        sys.exit(asyncio.run(_run()))
    elif args.cmd == "qualify":
        sys.exit(asyncio.run(_qualify(
            args.limit, args.concurrency, args.provider, args.model,
            args.resume, args.agent,
        )))
    elif args.cmd == "digest":
        sys.exit(asyncio.run(_digest(args.limit, args.format, args.output)))
    elif args.cmd == "draft":
        sys.exit(asyncio.run(_draft(
            args.job_id, args.provider, args.model, args.resume, args.output,
        )))
    elif args.cmd == "verdict":
        sys.exit(asyncio.run(_verdict(args.job_id, args.verdict)))
    elif args.cmd == "status":
        sys.exit(asyncio.run(_status(args.job_id, args.status)))
    elif args.cmd == "stage":
        sys.exit(asyncio.run(_stage(args.job_id, args.stage, args.notes)))
    elif args.cmd == "outcome":
        sys.exit(asyncio.run(_outcome(args.job_id, args.outcome)))
    elif args.cmd == "discover":
        sys.exit(asyncio.run(_discover(args.limit)))
    elif args.cmd == "stats":
        sys.exit(asyncio.run(_stats()))
    elif args.cmd == "actions":
        sys.exit(asyncio.run(_actions(args.review_limit, args.follow_up_days)))
    elif args.cmd == "funnel":
        sys.exit(asyncio.run(_funnel()))
    elif args.cmd == "remember":
        sys.exit(asyncio.run(_remember(args.content, args.kind, args.company)))
    elif args.cmd == "recall":
        sys.exit(asyncio.run(_recall(args.query, args.kind, args.company,
                                     args.limit)))
    elif args.cmd == "sweep":
        sys.exit(asyncio.run(_sweep(args.days)))


if __name__ == "__main__":
    main()
