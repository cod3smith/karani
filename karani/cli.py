"""CLI entry: `python -m ingestion.cli {run,qualify,digest,draft,prep,
followup,intel,notify,notion,verdict,status,stage,asked,outcome,discover,stats,actions,
funnel,remember,recall,refilter,sweep}`"""
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
    from drafting import build_application_pack
    from qualification import get_qualifier

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
        pack = await build_application_pack(
            client, storage, job_row=row,
            resume_markdown=resume.raw_markdown,
            output_path=output_path,
        )
        if pack.failed:
            print("ERROR: draft failed (malformed LLM output); retry",
                  file=sys.stderr)
            return 1
        pkg = pack.pkg
        print(f"drafted: {pack.draft_path}")
        print(f"  cover_letter_words={len(pkg.cover_letter.split())} "
              f"bullets={len(pkg.tailored_bullets)} "
              f"answers={len(pkg.application_answers)} "
              f"keyword_coverage={pkg.keyword_coverage}")
        if pack.voice.get("after"):
            print(f"  voice: {pack.voice['before']['score']} -> "
                  f"{pack.voice['after']['score']} "
                  f"(kept {pack.voice['kept']})")
        if pack.resume_path and pack.resume and pack.resume.resume_markdown:
            print(f"  tailored resume: {pack.resume_path} "
                  f"(coverage={pack.resume.keyword_coverage})")
        for name, meta in pack.artifacts.items():
            print(f"  artifact: {name} -> {meta['url']}")
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
        from notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
    finally:
        await storage.close()
    return 0


async def _status(job_id: int, status: str,
                  warm_path: bool | None = None) -> int:
    _configure_logging()
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        await storage.set_application_status(job_id, status,
                                             warm_path=warm_path)
        warm = "" if warm_path is None else f" warm_path={warm_path}"
        print(f"job_id={job_id} application_status={status}{warm}")
        from notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
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
        print(f"  tip: log what they asked with "
              f"`asked {job_id} \"<question>\" --stage {stage}` — "
              f"it feeds future prep packs")
    finally:
        await storage.close()
    return 0


async def _asked(job_id: int, question: str, stage: str) -> int:
    _configure_logging()
    from memory import MemoryManager
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        result = await MemoryManager(storage).remember_question(
            row, question, stage=stage,
        )
        state = "already recorded" if result.get("deduped") else "recorded"
        print(f"{state}: question bank for "
              f"{row.get('company_display')} (memory id={result.get('id')})")
        return 0
    finally:
        await storage.close()


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
        from notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
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


# -----------------------  prep / followup / intel / notify  -----------------------

async def _prep(job_id: int, provider: str | None, model: str | None,
                resume_path: str) -> int:
    _configure_logging()
    from drafting import prep_for_job
    from intel import dossier_text, get_company_intel
    from memory import MemoryManager
    from qualification import get_qualifier

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        company = row.get("company_display") or row.get("company") or ""
        intel = await get_company_intel(storage, company)
        memory = MemoryManager(storage)
        bank = [m["content"] for m in await memory.recall(
            f"{company} interview questions", kind="question", limit=10)]
        pkg, path = await prep_for_job(
            get_qualifier(provider=provider, model=model),
            resume=ResumeProfile.from_file(resume_path).raw_markdown,
            job_row=row, qualification=row.get("qualification"),
            dossier=dossier_text(intel), question_bank=bank,
        )
        print(f"prep pack: {path}")
        print(f"  likely_questions={len(pkg.likely_questions)} "
              f"questions_to_ask={len(pkg.questions_to_ask)} "
              f"warm_openers={len(pkg.warm_openers)}")
        return 0
    finally:
        await storage.close()


async def _followup(job_id: int, provider: str | None, model: str | None) -> int:
    _configure_logging()
    from datetime import datetime, timezone

    from drafting import followup_for_job
    from intel import dossier_text, get_company_intel
    from qualification import get_qualifier

    from .storage import _days_ago

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        company = row.get("company_display") or row.get("company") or ""
        intel = await get_company_intel(storage, company)
        days = _days_ago(row.get("applied_at"),
                         datetime.now(timezone.utc)) or 0
        pkg, path = await followup_for_job(
            get_qualifier(provider=provider, model=model),
            job_row=row, days_since_applied=days,
            dossier=dossier_text(intel),
        )
        print(f"follow-up note: {path}")
        print(f"  hook: {pkg.hook_used or '(none)'}")
        return 0
    finally:
        await storage.close()


async def _intel(company: str, refresh: bool) -> int:
    _configure_logging()
    from intel import dossier_text, get_company_intel

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        intel = await get_company_intel(storage, company,
                                        force_refresh=refresh)
        print(f"cached={intel.get('cached', False)}")
        print(dossier_text(intel))
        return 0
    finally:
        await storage.close()


async def _notify(kind: str, limit: int) -> int:
    _configure_logging()
    import os

    from slackbridge import SlackClient
    from slackbridge.blocks import actions_blocks, digest_blocks

    channel = os.getenv("SLACK_CHANNEL", "")
    if not channel:
        print("ERROR: SLACK_CHANNEL not set", file=sys.stderr)
        return 2
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        slack = SlackClient()
        if kind == "digest":
            rows = await storage.top_qualified(limit=limit)
            blocks = digest_blocks(rows, limit=limit)
            fallback = f"karani digest: {len(rows)} role(s)"
        else:
            buckets = await storage.next_actions(review_limit=limit)
            blocks = actions_blocks(buckets)
            fallback = "karani: next actions"
        await slack.post_message(channel, fallback, blocks=blocks)
        print(f"pushed {kind} to {channel}")
        return 0
    finally:
        await storage.close()


# -----------------------  autopilot  -----------------------

async def _autopilot(min_fit: int | None, max_drafts: int | None) -> int:
    _configure_logging()
    import os

    from autopilot import run_autopilot
    from autopilot.runner import DEFAULT_MAX_DRAFTS, DEFAULT_MIN_FIT
    from qualification import get_qualifier
    from slackbridge import SlackClient

    channel = os.getenv("SLACK_CHANNEL", "")
    if not channel:
        print("autopilot: SLACK_CHANNEL not set — nothing to deliver to; "
              "skipping (configure Slack to enable the continuous hunt)")
        return 0
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        stats = await run_autopilot(
            storage, slack=SlackClient(), channel=channel,
            make_qualifier=lambda: get_qualifier(),
            load_resume=lambda: ResumeProfile.from_file(DEFAULT_RESUME_PATH),
            min_fit=min_fit if min_fit is not None else DEFAULT_MIN_FIT,
            max_drafts=(max_drafts if max_drafts is not None
                        else DEFAULT_MAX_DRAFTS),
        )
        print(f"candidates={stats.candidates} drafted={stats.drafted} "
              f"delivered={stats.delivered} errors={len(stats.errors)}")
        for e in stats.errors[:5]:
            print(f"  ERR: {e}", file=sys.stderr)
        return 0 if not stats.errors else 1
    finally:
        await storage.close()


# -----------------------  notion  -----------------------

async def _notion(action: str, parent_page_id: str | None) -> int:
    _configure_logging()
    import os

    from notionsync import NotionClient, NotionError, init_database, sync_jobs

    try:
        client = NotionClient()
    except NotionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if action == "init":
        if not parent_page_id:
            print("ERROR: notion init requires a parent page id "
                  "(share that page with the integration first)",
                  file=sys.stderr)
            return 2
        db_id = await init_database(client, parent_page_id)
        print(f"created database: {db_id}")
        print("add to .env:  NOTION_DATABASE_ID=" + db_id)
        return 0

    database_id = os.getenv("NOTION_DATABASE_ID", "")
    if not database_id:
        print("ERROR: NOTION_DATABASE_ID not set — run "
              "`notion init <parent_page_id>` first", file=sys.stderr)
        return 2
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        result = await sync_jobs(storage, client, database_id)
        print(f"tracked={result['tracked']} created={result['created']} "
              f"updated={result['updated']} errors={result['errors']}")
        return 0 if not result["errors"] else 1
    finally:
        await storage.close()


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


async def _reindex() -> int:
    _configure_logging()
    from memory import MemoryManager
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        manager = MemoryManager(storage)
        result = await manager.reindex()
        print(f"mode={result['mode']} indexed={result['indexed']} "
              f"failed={result.get('failed', 0)}"
              + (f"  note: {result['note']}" if result.get("note") else ""))
        return 0
    finally:
        await storage.close()


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


# -----------------------  refilter  -----------------------

async def _refilter() -> int:
    """Re-run the pre-filter over all active rows after a rules change
    (profile skills, geo/relocation signals, title exclusions)."""
    _configure_logging()
    from .filters import pre_filter
    from .models import Job, RemoteStatus, Source

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        rows = await storage.active_jobs()
        was_passing = sum(1 for r in rows if r.get("prefilter_passed"))
        flipped_in = flipped_out = 0
        for r in rows:
            try:
                job = Job(
                    source=Source(r["source"]),
                    source_id=r["source_id"],
                    company=r.get("company") or "",
                    company_display=r.get("company_display") or "",
                    title=r.get("title") or "",
                    location_raw=r.get("location_raw") or "",
                    remote_status=RemoteStatus(r.get("remote_status")
                                               or "unknown"),
                    description_text=r.get("description_text") or "",
                    apply_url=r.get("apply_url") or "",
                    posted_at=r.get("posted_at"),
                    comp_min_usd=r.get("comp_min_usd"),
                    comp_max_usd=r.get("comp_max_usd"),
                    comp_disclosed=bool(r.get("comp_disclosed")),
                    comp_currency_original=r.get("comp_currency_original"),
                    tags=list(r.get("tags") or []),
                )
                pf = pre_filter(job)
                if pf.pass_hard_filters != bool(r.get("prefilter_passed")):
                    if pf.pass_hard_filters:
                        flipped_in += 1
                    else:
                        flipped_out += 1
                await storage.update_prefilter(r["id"], pf)
            except Exception as e:
                print(f"  ERR job_id={r.get('id')}: {e}", file=sys.stderr)
        now_passing = was_passing + flipped_in - flipped_out
        print(f"refiltered={len(rows)} passing {was_passing} -> {now_passing} "
              f"(+{flipped_in} newly pass, -{flipped_out} newly drop)")
        print("newly-passing rows are pending qualification on the next "
              "qualify run")
        return 0
    finally:
        await storage.close()


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
    warm_group = st.add_mutually_exclusive_group()
    warm_group.add_argument("--warm", action="store_true",
                            help="application went through a warm contact")
    warm_group.add_argument("--cold", action="store_true",
                            help="cold portal application")

    sg = sub.add_parser("stage", help="log an interview or application stage")
    sg.add_argument("job_id", type=int)
    sg.add_argument("stage", type=str,
                    help="e.g. recruiter_screen, hiring_manager, technical, onsite")
    sg.add_argument("--notes", type=str, default="")

    ak = sub.add_parser("asked", help="log an interview question into the question bank")
    ak.add_argument("job_id", type=int)
    ak.add_argument("question", type=str)
    ak.add_argument("--stage", type=str, default="")

    oc = sub.add_parser("outcome", help="record final outcome")
    oc.add_argument("job_id", type=int)
    oc.add_argument("outcome", choices=sorted(Storage.OUTCOMES))

    sub.add_parser("refilter", help="re-run the pre-filter over all active rows after a rules change")

    disc = sub.add_parser("discover", help="probe unpromoted companies for ATS presence")
    disc.add_argument("--limit", type=int, default=10)

    sub.add_parser("stats", help="print DB counts + pipeline funnel")

    ac = sub.add_parser("actions", help="prioritized worklist: review, draft, submit, follow up")
    ac.add_argument("--review-limit", type=int, default=10)
    ac.add_argument("--follow-up-days", type=int, default=7)

    sub.add_parser("funnel", help="conversion funnel: response/interview/offer rates")

    pr = sub.add_parser("prep", help="interview prep pack: brief + questions + warm openers")
    pr.add_argument("job_id", type=int)
    pr.add_argument("--provider", type=str, default=None,
                    choices=[None, "openrouter", "anthropic", "local"])
    pr.add_argument("--model", type=str, default=None)
    pr.add_argument("--resume", type=str, default=DEFAULT_RESUME_PATH)

    fu = sub.add_parser("followup", help="draft a follow-up note for a silent application")
    fu.add_argument("job_id", type=int)
    fu.add_argument("--provider", type=str, default=None,
                    choices=[None, "openrouter", "anthropic", "local"])
    fu.add_argument("--model", type=str, default=None)

    it = sub.add_parser("intel", help="company dossier from public probes (cached)")
    it.add_argument("company", type=str)
    it.add_argument("--refresh", action="store_true")

    nt = sub.add_parser("notify", help="push digest or actions to Slack")
    nt.add_argument("--kind", choices=["digest", "actions"], default="digest")
    nt.add_argument("--limit", type=int, default=10)

    ap = sub.add_parser("autopilot", help="draft packs for top-fit roles and deliver to Slack for review")
    ap.add_argument("--min-fit", type=int, default=None)
    ap.add_argument("--max-drafts", type=int, default=None)

    no = sub.add_parser("notion", help="mirror tracked applications to a Notion board")
    no.add_argument("action", choices=["init", "sync"])
    no.add_argument("parent_page_id", nargs="?", default=None,
                    help="for init: page id to create the database under")

    rm = sub.add_parser("remember", help="store a fact in the memory layer")
    rm.add_argument("content", type=str)
    rm.add_argument("--kind", default="preference",
                    choices=sorted(Storage.MEMORY_KINDS))
    rm.add_argument("--company", type=str, default=None)

    sub.add_parser("reindex", help="rebuild the mem0 index from the memory ledger")

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
        warm = True if args.warm else (False if args.cold else None)
        sys.exit(asyncio.run(_status(args.job_id, args.status, warm)))
    elif args.cmd == "stage":
        sys.exit(asyncio.run(_stage(args.job_id, args.stage, args.notes)))
    elif args.cmd == "asked":
        sys.exit(asyncio.run(_asked(args.job_id, args.question, args.stage)))
    elif args.cmd == "outcome":
        sys.exit(asyncio.run(_outcome(args.job_id, args.outcome)))
    elif args.cmd == "refilter":
        sys.exit(asyncio.run(_refilter()))
    elif args.cmd == "discover":
        sys.exit(asyncio.run(_discover(args.limit)))
    elif args.cmd == "stats":
        sys.exit(asyncio.run(_stats()))
    elif args.cmd == "actions":
        sys.exit(asyncio.run(_actions(args.review_limit, args.follow_up_days)))
    elif args.cmd == "funnel":
        sys.exit(asyncio.run(_funnel()))
    elif args.cmd == "prep":
        sys.exit(asyncio.run(_prep(args.job_id, args.provider, args.model,
                                   args.resume)))
    elif args.cmd == "followup":
        sys.exit(asyncio.run(_followup(args.job_id, args.provider,
                                       args.model)))
    elif args.cmd == "intel":
        sys.exit(asyncio.run(_intel(args.company, args.refresh)))
    elif args.cmd == "notify":
        sys.exit(asyncio.run(_notify(args.kind, args.limit)))
    elif args.cmd == "notion":
        sys.exit(asyncio.run(_notion(args.action, args.parent_page_id)))
    elif args.cmd == "autopilot":
        sys.exit(asyncio.run(_autopilot(args.min_fit, args.max_drafts)))
    elif args.cmd == "remember":
        sys.exit(asyncio.run(_remember(args.content, args.kind, args.company)))
    elif args.cmd == "recall":
        sys.exit(asyncio.run(_recall(args.query, args.kind, args.company,
                                     args.limit)))
    elif args.cmd == "reindex":
        sys.exit(asyncio.run(_reindex()))
    elif args.cmd == "sweep":
        sys.exit(asyncio.run(_sweep(args.days)))


if __name__ == "__main__":
    main()
