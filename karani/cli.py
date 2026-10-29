"""The `karani` CLI — every verb of the pipeline behind one command.

Pipeline: run, qualify, digest, draft, prep, followup, autopilot, refilter
Submit:   console (build the field sheet), submitted (record the send)
Review:   actions, funnel, coach, variant, stats, verdict, status, stage,
          asked, outcome
Inbox:    watch (read-only Gmail; never sends)
Context:  remember, recall, reindex, intel, notion, notify
Setup:    init, config, hunt, unschedule, infra, mcp, slack, hourly
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from karani.ingestion.config import settings
from karani.ingestion.digest import render as render_digest
from karani.ingestion.discovery import probe_unpromoted
from karani.ingestion.orchestrator import run
from karani.ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
from karani.ingestion.storage import Storage


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
    from karani.qualification import get_qualifier, qualify_pending

    resume = ResumeProfile.from_file(resume_path)
    print(f"resume loaded: {resume.word_count} words, hash={resume.hash[:12]}")

    client = get_qualifier(provider=provider, model=model)
    mode = "AGENT (tools)" if agent_mode else "single-turn"
    print(f"provider={type(client).__name__} model={client.model_name} mode={mode}")

    if agent_mode and not hasattr(client, "chat_turn"):
        print("ERROR: --agent needs a provider with tool-calling support "
              "(openrouter, openai, or local — not anthropic yet).",
              file=sys.stderr)
        return 2

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        from karani.memory import MemoryManager
        from karani import budget
        async with budget.track(storage, "cli:qualify"):
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
    from karani.drafting import build_application_pack
    from karani.qualification import get_qualifier

    resume = ResumeProfile.from_file(resume_path)
    # None lets each stage route through [llm.<task>] (ADR 0017) —
    # passing a client pins the whole pack to one model, which is how
    # cover letters ended up on the qualify tier.
    override = get_qualifier(provider=provider, model=model) \
        if (provider or model) else None
    if override is not None:
        print(f"drafting job_id={job_id} via {type(override).__name__}"
              f"/{override.model_name} (explicit override)")
    else:
        from karani.config import get_config
        cfg = get_config().llm.for_task("draft")
        print(f"drafting job_id={job_id} via {cfg.provider}/{cfg.model} "
              f"(per-task routing)")

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        from karani import budget
        # One-off commands bill real money too; without this the guard
        # only ever saw scheduler passes (ADR 0019).
        async with budget.track(storage, "cli:draft"):
            pack = await build_application_pack(
                override, storage, job_row=row,
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
            from karani.memory import MemoryManager
            await MemoryManager(storage).remember_verdict(row, verdict)
        from karani.notionsync import maybe_sync_job
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
        from karani.notionsync import maybe_sync_job
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
    from karani.memory import MemoryManager
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
            from karani.memory import MemoryManager
            await MemoryManager(storage).remember_outcome(row, outcome)
        from karani.notionsync import maybe_sync_job
        await maybe_sync_job(storage, job_id)
    finally:
        await storage.close()
    return 0


# -----------------------  submission console  -----------------------

async def _console(limit: int, output: str | None, refresh: bool) -> int:
    """Build the one-file submission console. Free: no LLM call."""
    _configure_logging()
    import httpx

    from karani.submission import build_console, introspection_enabled

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        if introspection_enabled():
            async with httpx.AsyncClient(
                headers={"User-Agent": settings.user_agent},
                timeout=settings.http_timeout_seconds,
                follow_redirects=True,
            ) as client:
                result = await build_console(
                    storage, limit=limit, output=output, client=client,
                    refresh=refresh)
        else:
            result = await build_console(
                storage, limit=limit, output=output, refresh=refresh)

        count = len(result.sheets)
        print(f"{count} pack{'' if count == 1 else 's'} -> {result.path}")
        if count:
            print(f"  forms read from the ATS: {result.introspected}"
                  f"/{count}")
            print(f"  fields still needing you: {result.outstanding}")
            if result.needs_review:
                print(f"  {result.needs_review} need a work-authorization "
                      f"decision before sending")
            print(f"  open it:  open {result.path}")
            print("  karani never submits. Send each one yourself, mark it "
                  "in the console, then run the `karani submitted` command "
                  "it prints.")
        return 0
    finally:
        await storage.close()


async def _submitted(job_ids: list[int], warm_path: bool | None,
                     via: str) -> int:
    """Record applications YOU sent: status, timestamp, artifact version."""
    _configure_logging()
    from karani.notionsync import maybe_sync_job

    storage = Storage(settings.database_url)
    await storage.connect()
    missing: list[int] = []
    try:
        for job_id in job_ids:
            if not await storage.mark_submitted(job_id, via=via,
                                                warm_path=warm_path):
                missing.append(job_id)
                continue
            row = await storage.get_job(job_id) or {}
            print(f"job_id={job_id} applied via={via} "
                  f"{row.get('company_display', '')} - {row.get('title', '')}")
            await maybe_sync_job(storage, job_id)
    finally:
        await storage.close()
    for job_id in missing:
        print(f"ERROR: no job with id={job_id}", file=sys.stderr)
    return 1 if missing else 0


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
        # The ledger-backed half: positioning angle, variant, company,
        # comp band, time-to-first-response, and the promotion verdict.
        from karani.analytics.console import print_conversion
        await print_conversion(storage)
    finally:
        await storage.close()
    return 0


async def _coach(window_days: int, slack: bool, save: str | None,
                 docs: bool) -> int:
    _configure_logging()
    from karani.analytics.console import run_coach
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        return await run_coach(storage, window_days=window_days,
                               slack=slack, save=save, docs=docs)
    finally:
        await storage.close()


async def _variant(action: str, name: str | None, dimension: str,
                   description: str, guidance: str,
                   job_id: int | None) -> int:
    _configure_logging()
    from karani.analytics.console import run_variant
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        return await run_variant(storage, action, name=name,
                                 dimension=dimension,
                                 description=description, guidance=guidance,
                                 job_id=job_id)
    finally:
        await storage.close()


# -----------------------  prep / followup / intel / notify  -----------------------

async def _prep(job_id: int, provider: str | None, model: str | None,
                resume_path: str, stage: str = "interview") -> int:
    _configure_logging()
    from karani.coach import build_prep_pack
    from karani.qualification import get_qualifier

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        row = await storage.get_job(job_id)
        if not row:
            print(f"ERROR: no job with id={job_id}", file=sys.stderr)
            return 2
        result = await build_prep_pack(
            storage, job_row=row, stage=stage,
            resume_markdown=ResumeProfile.from_file(resume_path).raw_markdown,
            client=(get_qualifier(provider=provider, model=model)
                    if (provider or model) else None),
        )
        pack = result.pack
        print(f"prep pack: {result.path}")
        for ext, path in result.documents.items():
            print(f"  {ext}: {path}")
        print(f"  likely_questions={len(pack.likely_questions)} "
              f"questions_to_ask={len(pack.questions_to_ask)} "
              f"stories={len(pack.stories)} "
              f"warm_openers={len(pack.warm_openers)}")
        dropped = [e for e in pack.story_trace
                   if str(e.get("status", "")).startswith("dropped")]
        if dropped:
            print(f"  {len(dropped)} story/stories dropped for unverifiable "
                  f"provenance (see the trace at the end of the pack)")
        return 0
    finally:
        await storage.close()


async def _followup(job_id: int, provider: str | None, model: str | None) -> int:
    _configure_logging()
    from datetime import datetime, timezone

    from karani.drafting import followup_for_job
    from karani.intel import dossier_text, get_company_intel
    from karani.qualification import get_qualifier

    from karani.ingestion.storage import _days_ago

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
    from karani.intel import dossier_text, get_company_intel

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

    from karani.slackbridge import SlackClient
    from karani.slackbridge.blocks import actions_blocks, digest_blocks

    from karani.slackbridge import configured_channel
    channel = configured_channel()
    if not channel:
        print("ERROR: SLACK_CHANNEL not set", file=sys.stderr)
        return 2
    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        slack = SlackClient()
        # Surface a dead scheduler before the digest it would have filled.
        from karani.orchestration.graph import heartbeat_alert
        alert = await heartbeat_alert(storage)
        if alert:
            await slack.post_message(channel, alert)
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

    from karani.autopilot import run_autopilot
    from karani.qualification import get_qualifier
    from karani.slackbridge import SlackClient

    from karani.slackbridge import configured_channel
    channel = configured_channel()
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
            min_fit=min_fit,
            max_drafts=max_drafts,
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

    from karani.notionsync import NotionClient, NotionError, init_database, sync_jobs

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

    from karani.notionsync import configured_database_id
    database_id = configured_database_id()
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
    from karani.memory import MemoryManager
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
    from karani.memory import MemoryManager
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
    from karani.memory import MemoryManager
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
    from karani.ingestion.filters import pre_filter
    from karani.ingestion.models import Job, RemoteStatus, Source

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


# -----------------------  watch  -----------------------

async def _watch(days: int | None, limit: int | None,
                 deliver: bool) -> int:
    """Read the mailbox and reconcile it with the pipeline (read-only)."""
    _configure_logging()
    from karani.watcher import watch
    from karani.watcher.gmail import ENV_CLIENT_ID, configured

    # `watch()` itself degrades (a scheduled caller must not crash on a
    # mailbox that was never set up), but someone typing the verb asked
    # for mail and deserves to be told why there is none.
    if not configured():
        print(f"Gmail is not set up: {ENV_CLIENT_ID}, GMAIL_CLIENT_SECRET "
              f"and GMAIL_REFRESH_TOKEN must be in your .env.",
              file=sys.stderr)
        print("See docs/watcher.md for the one-time Google Cloud steps, "
              "then run `karani watch --authorize`.", file=sys.stderr)
        return 2

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        report = await watch(storage, lookback_days=days, limit=limit,
                             deliver=deliver)
    finally:
        await storage.close()

    print(f"read={report.fetched} already_seen={report.already_seen} "
          f"advanced={len(report.advanced)} "
          f"unplaced={len(report.unmatched)} errors={len(report.errors)}")
    for processed in report.advanced:
        print(f"  [{processed.match.job_id}] "
              f"{processed.classification.label}: "
              f"{processed.status_before or 'new'} -> {processed.status_after}")
    for processed in report.unmatched:
        print(f"  UNPLACED {processed.classification.label}: "
              f"{processed.message.subject[:70]!r} "
              f"from {processed.message.from_address}")
    for err in report.errors:
        print(f"  ERR: {err}", file=sys.stderr)
    return 0 if not report.errors else 1


def _watch_authorize() -> int:
    from karani.watcher.gmail import (
        ENV_REFRESH_TOKEN, GmailAuthError, authorize,
    )
    try:
        token = authorize()
    except GmailAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("Authorized, read-only. Add this to your .env:\n")
    print(f"{ENV_REFRESH_TOKEN}={token}")
    return 0


# -----------------------  setup / serve / schedule  -----------------------

def _karani_bin() -> str:
    import shutil
    return shutil.which("karani") or f"{sys.executable} -m karani"


def _workdir_and_logs() -> tuple[str, str]:
    """Repo mode (CWD has karani data) vs installed mode (~/.karani)."""
    from pathlib import Path

    from karani.config.loader import karani_home
    if Path("data/resume.md").exists() or Path("karani.toml").exists():
        logs = Path("logs")
    else:
        logs = karani_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return str(Path.cwd()), str(logs)


def _resource(name: str) -> str:
    from importlib import resources
    return (resources.files("karani") / "resources" / name).read_text()


def _init(yes: bool) -> int:
    from pathlib import Path

    from karani.config.loader import karani_home
    from karani.config.wizard import run_wizard
    dest = (Path("karani.toml")
            if Path("pyproject.toml").exists() or Path(".git").exists()
            else karani_home() / "karani.toml")
    if dest.exists() and not yes:
        answer = input(f"{dest} exists — overwrite? [y/N]: ")
        if not answer.lower().startswith("y"):
            print("aborted")
            return 1
    run_wizard(dest, yes=yes)
    return 0


def _config_check() -> int:
    import json as _json

    from karani.config import config_sources, get_config
    cfg = get_config()
    print(f"config file: {config_sources()['file'] or '(defaults)'}")
    print(_json.dumps(cfg.model_dump(), indent=2, default=str))
    problems: list[str] = []
    llm = cfg.llm.for_task("qualify")
    provider = os.getenv("QUAL_PROVIDER") or llm.provider or "openrouter"
    key_for = {"openrouter": "OPENROUTER_API_KEY",
               "openai": "OPENAI_API_KEY",
               "anthropic": "ANTHROPIC_API_KEY"}
    need = llm.api_key_env or key_for.get(provider)
    if need and not os.getenv(need):
        problems.append(f"provider '{provider}' needs {need} in env")
    if not os.getenv("DATABASE_URL"):
        problems.append("DATABASE_URL unset — using in-memory storage "
                        "(state is lost between runs)")
    for prob in problems:
        print(f"WARNING: {prob}", file=sys.stderr)
    return 0


def _install_watchdog(agents, workdir: str, logs: str, subprocess) -> None:
    """Install the external dead-man's switch.

    It lives outside the repo (in ~/.karani) and runs under /bin/sh, so
    deleting the virtualenv — the failure that hid an 8-day outage —
    cannot take the alarm down with the alarm's subject.
    """
    from pathlib import Path

    from karani.config.loader import karani_home
    from karani.orchestration.graph import pass_marker_path
    from karani.slackbridge import configured_channel

    # launchd runs with no working directory, so every path baked into
    # the job must be absolute — a relative one makes it refuse to spawn
    # (EX_CONFIG) or hunt for a marker that is not there.
    work = Path(workdir).resolve()
    marker = Path(pass_marker_path())
    if not marker.is_absolute():
        marker = work / marker
    log_dir = Path(logs) if Path(logs).is_absolute() else work / logs

    home = karani_home()
    home.mkdir(parents=True, exist_ok=True)
    script = home / "watchdog.sh"
    script.write_text(
        _resource("watchdog.sh")
        .replace("__MARKER__", str(marker))
        .replace("__MAX_AGE_H__", os.getenv("KARANI_WATCHDOG_MAX_AGE_H", "4"))
        .replace("__ENV_FILE__", str(work / ".env"))
        .replace("__CHANNEL__", configured_channel() or "")
    )
    script.chmod(0o755)

    plist = agents / "com.karani.watchdog.plist"
    plist.write_text(_resource("karani.watchdog.plist.template")
                     .replace("__SCRIPT__", str(script))
                     .replace("__LOGS__", str(log_dir)))
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(plist)], check=True)


def _install_ollama_env(agents, subprocess) -> None:
    """Pin Ollama's context window across reboots, when qualify is local.

    `launchctl setenv` is lost on reboot and Ollama.app reads its config
    only from the launchd environment, so the window silently falls back
    to 4096 and truncates qualification prompts (docs/operations.md).
    """
    from karani.config import get_config
    provider = (os.getenv("QUAL_PROVIDER")
                or get_config().llm.for_task("qualify").provider or "")
    if provider.lower() != "local":
        return

    plist = agents / "com.karani.ollama-env.plist"
    plist.write_text(
        _resource("karani.ollama-env.plist.template")
        .replace("__CONTEXT__", os.getenv("OLLAMA_CONTEXT_LENGTH", "16384"))
        .replace("__PARALLEL__", os.getenv("OLLAMA_NUM_PARALLEL", "1"))
    )
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(plist)], check=True)


def _schedule() -> int:
    import subprocess
    from pathlib import Path

    workdir, logs = _workdir_and_logs()
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    for name in ("karani.hourly", "karani.daily"):
        content = (_resource(f"{name}.plist.template")
                   .replace("__KARANI__", _karani_bin())
                   .replace("__WORKDIR__", workdir)
                   .replace("__LOGS__", logs))
        plist = agents / f"com.{name}.plist"
        plist.write_text(content)
        subprocess.run(["launchctl", "unload", str(plist)],
                       capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(plist)], check=True)

    _install_watchdog(agents, workdir, logs, subprocess)
    _install_ollama_env(agents, subprocess)
    print("scheduled: com.karani.hourly (every hour) + com.karani.daily "
          "(06:00/13:00) + com.karani.watchdog (hourly)")
    print(f"logs: {logs}")
    print("run one now: launchctl start com.karani.hourly")
    return 0


def _unschedule() -> int:
    import subprocess
    from pathlib import Path

    agents = Path.home() / "Library" / "LaunchAgents"
    for name in ("com.karani.hourly.plist", "com.karani.daily.plist"):
        plist = agents / name
        subprocess.run(["launchctl", "unload", "-w", str(plist)],
                       capture_output=True)
        plist.unlink(missing_ok=True)
    print("unscheduled")
    return 0


def _infra(action: str, profile: str | None) -> int:
    import subprocess
    from pathlib import Path

    from karani.config.loader import karani_home
    compose = Path("docker-compose.yml")
    if not compose.exists():
        compose = karani_home() / "docker-compose.yml"
        compose.parent.mkdir(parents=True, exist_ok=True)
        if not compose.exists():
            compose.write_text(_resource("docker-compose.yml"))
    cmd = ["docker", "compose", "-f", str(compose)]
    if profile:
        cmd += ["--profile", profile]
    cmd += (["up", "-d"] if action == "up" else ["down"])
    return subprocess.run(cmd).returncode


def _hourly(loop: int | None) -> int:
    from karani.orchestration import graph
    from karani.orchestration.graph import run_with_deadline

    async def _loop() -> None:
        while True:
            try:
                state = await run_with_deadline(graph.run_hunt_once())
                print(f"hunt pass done: "
                      f"errors={len(state.get('errors', []))}")
            except TimeoutError as exc:
                print(f"ERR: {exc}", file=sys.stderr)
            await asyncio.sleep(loop)

    if loop:
        asyncio.run(_loop())
        return 0
    try:
        state = asyncio.run(run_with_deadline(graph.run_hunt_once()))
    except TimeoutError as exc:
        # Non-zero so the launchd record shows the lost hour instead of
        # a silent success (ADR 0025).
        print(f"ERR: {exc}", file=sys.stderr)
        return 1
    errors = state.get("errors", [])
    for section in ("ingest", "qualify", "autopilot", "notion"):
        print(f"{section}={state.get(section)}")
    for e in errors:
        print(f"ERR: {e}", file=sys.stderr)
    return 1 if errors else 0


# -----------------------  argparse  -----------------------

def main() -> None:
    from karani import __version__
    from karani.config import load_config
    load_config()

    parser = argparse.ArgumentParser(
        "karani",
        description="a self-hosted job-hunt pipeline that drafts "
                    "everything and submits nothing",
    )
    parser.add_argument("--version", action="version",
                        version=f"karani {__version__}")
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

    ch = sub.add_parser("coach", help="weekly review over the conversion ledger")
    ch.add_argument("--window-days", type=int, default=7)
    ch.add_argument("--slack", action="store_true",
                    help="also post the review as a Slack card")
    ch.add_argument("--save", type=str, default=None,
                    help="write the review markdown to this path")
    ch.add_argument("--docs", action="store_true",
                    help="with --save, also render .docx/.pdf")

    vr = sub.add_parser("variant",
                        help="strategy variants: list, add, retire, assign")
    vr.add_argument("action", choices=["list", "add", "retire", "assign"])
    vr.add_argument("name", nargs="?", default=None)
    vr.add_argument("--dimension", default="positioning",
                    choices=["positioning", "draft_prompt", "resume_order",
                             "outreach"])
    vr.add_argument("--description", default="")
    vr.add_argument("--guidance", default="",
                    help="what a drafter should actually do differently")
    vr.add_argument("--job", type=int, default=None,
                    help="job id, for `variant assign`")

    con = sub.add_parser(
        "console",
        help="build the submission console: one HTML file with every "
             "pack waiting to be sent, its real ATS questions and the "
             "answers")
    con.add_argument("--limit", type=int, default=50)
    con.add_argument("--output", type=str, default=None,
                     help="default data/submission_console.html")
    con.add_argument("--refresh", action="store_true",
                     help="re-read each job's questions from the ATS")

    smd = sub.add_parser(
        "submitted",
        help="record that YOU sent an application: status, timestamp and "
             "the artifact version that went out")
    smd.add_argument("job_ids", type=int, nargs="+")
    sent_group = smd.add_mutually_exclusive_group()
    sent_group.add_argument("--warm", action="store_true",
                            help="went through a warm contact or referral")
    sent_group.add_argument("--cold", action="store_true",
                            help="cold portal application")
    smd.add_argument("--via", type=str, default="console",
                     help="console | portal | email | referral")

    pr = sub.add_parser("prep", help="interview prep pack: dossier + likely "
                                     "questions + STAR stories + comp")
    pr.add_argument("job_id", type=int)
    pr.add_argument("--stage", type=str, default="interview",
                    choices=["screen", "interview"])
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

    wt = sub.add_parser("watch", help="read replies in Gmail (read-only) and "
                                      "advance the application state machine")
    wt.add_argument("--days", type=int, default=None, help="lookback window")
    wt.add_argument("--limit", type=int, default=None,
                    help="max messages to read")
    wt.add_argument("--no-slack", action="store_true",
                    help="print the pass instead of pushing it to Slack")
    wt.add_argument("--authorize", action="store_true",
                    help="run the one-time read-only OAuth consent flow")

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

    ini = sub.add_parser("init", help="interactive setup — writes karani.toml")
    ini.add_argument("--yes", action="store_true", help="accept all defaults")

    cfg_p = sub.add_parser("config", help="show the resolved configuration")
    cfg_p.add_argument("action", nargs="?", choices=["check"], default="check")

    sub.add_parser("hunt", help="schedule the continuous hunt (hourly + daily summaries)")
    sub.add_parser("unschedule", help="remove the scheduled hunt")

    inf = sub.add_parser("infra", help="manage karani's docker infra")
    inf.add_argument("action", choices=["up", "down"])
    inf.add_argument("--profile", default=None,
                     help="compose profile, e.g. local-llm")

    hr = sub.add_parser("hourly", help="run one hunt pass (or --loop N seconds)")
    hr.add_argument("--loop", type=int, nargs="?", const=3600, default=None)

    sub.add_parser("mcp", help="serve the MCP server over stdio")
    sub.add_parser("slack", help="run the two-way Slack listener")

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
    elif args.cmd == "coach":
        sys.exit(asyncio.run(_coach(args.window_days, args.slack,
                                    args.save, args.docs)))
    elif args.cmd == "variant":
        sys.exit(asyncio.run(_variant(args.action, args.name, args.dimension,
                                      args.description, args.guidance,
                                      args.job)))

    elif args.cmd == "console":
        sys.exit(asyncio.run(_console(args.limit, args.output, args.refresh)))
    elif args.cmd == "submitted":
        warm = True if args.warm else (False if args.cold else None)
        sys.exit(asyncio.run(_submitted(args.job_ids, warm, args.via)))
    elif args.cmd == "prep":
        sys.exit(asyncio.run(_prep(args.job_id, args.provider, args.model,
                                   args.resume, args.stage)))
    elif args.cmd == "followup":
        sys.exit(asyncio.run(_followup(args.job_id, args.provider,
                                       args.model)))
    elif args.cmd == "intel":
        sys.exit(asyncio.run(_intel(args.company, args.refresh)))
    elif args.cmd == "notify":
        sys.exit(asyncio.run(_notify(args.kind, args.limit)))
    elif args.cmd == "watch":
        if args.authorize:
            sys.exit(_watch_authorize())
        sys.exit(asyncio.run(_watch(args.days, args.limit,
                                    not args.no_slack)))
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
    elif args.cmd == "init":
        sys.exit(_init(args.yes))
    elif args.cmd == "config":
        sys.exit(_config_check())
    elif args.cmd == "hunt":
        sys.exit(_schedule())
    elif args.cmd == "unschedule":
        sys.exit(_unschedule())
    elif args.cmd == "infra":
        sys.exit(_infra(args.action, args.profile))
    elif args.cmd == "hourly":
        sys.exit(_hourly(args.loop))
    elif args.cmd == "mcp":
        from karani.mcp_server.__main__ import main as mcp_main
        mcp_main()
    elif args.cmd == "slack":
        from karani.slackbridge.__main__ import main as slack_main
        slack_main()


if __name__ == "__main__":
    main()
