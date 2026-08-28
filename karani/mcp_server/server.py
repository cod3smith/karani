"""MCP server for karani.

Exposes the whole pipeline — ingest, qualify, digest, shortlist, draft, and
the application state machine — as MCP tools over stdio, so any MCP client
(Claude Code, Claude Desktop, Cowork) can drive the daily loop conversationally.

Storage is a process-wide singleton: connected lazily on the first tool call
and shared across calls so the in-memory fallback keeps state for the whole
server lifetime. Tests inject an in-memory Storage via `use_storage()`.

LLM-backed tools (`qualify`, `draft`) resolve their client through
`_make_qualifier`, a thin wrapper over `qualification.factory.get_qualifier`
that tests monkeypatch with a fake.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from karani.ingestion.config import settings
from karani.ingestion.digest import render as render_digest
from karani.ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
from karani.ingestion.storage import Storage

app = MCPServer(
    name="karani",
    instructions=(
        "Personal job-application pipeline: ingest postings, qualify them "
        "against the resume, review the shortlist, draft applications, and "
        "track application state. Typical flow: ingest -> qualify -> "
        "shortlist -> get_job -> draft -> set_status/record_verdict."
    ),
)

_storage: Storage | None = None
_storage_lock = asyncio.Lock()
_memory = None


async def _get_storage() -> Storage:
    global _storage
    async with _storage_lock:
        if _storage is None:
            s = Storage(settings.database_url)
            await s.connect()
            _storage = s
    return _storage


async def _get_memory():
    """MemoryManager bound to the storage singleton (docs/memory.md)."""
    global _memory
    storage = await _get_storage()
    if _memory is None or _memory.storage is not storage:
        from karani.memory import MemoryManager
        _memory = MemoryManager(storage)
    return _memory


def use_storage(storage: Storage | None) -> None:
    """Inject a pre-connected Storage (tests / embedding). None resets."""
    global _storage, _memory
    _storage = storage
    _memory = None


def _make_qualifier(provider: str | None, model: str | None):
    from karani.qualification import get_qualifier
    return get_qualifier(provider=provider, model=model)


def _load_resume(resume_path: str | None) -> ResumeProfile:
    return ResumeProfile.from_file(resume_path or DEFAULT_RESUME_PATH)


def _jsonable(obj: Any) -> Any:
    """Round-trip through JSON so datetimes/enums become plain strings."""
    return json.loads(json.dumps(obj, default=str))


def _job_summary(row: dict) -> dict:
    qual = row.get("qualification") or {}
    if isinstance(qual, str):
        try:
            qual = json.loads(qual)
        except json.JSONDecodeError:
            qual = {}
    return _jsonable({
        "id": row.get("id"),
        "company": row.get("company_display") or row.get("company"),
        "title": row.get("title"),
        "location": row.get("location_raw"),
        "comp_min_usd": row.get("comp_min_usd"),
        "comp_max_usd": row.get("comp_max_usd"),
        "verdict": row.get("verdict"),
        "fit_score": row.get("fit_score") or qual.get("fit_score"),
        "why_apply": qual.get("why_apply", ""),
        "apply_url": row.get("apply_url"),
        "user_verdict": row.get("user_verdict"),
        "application_status": row.get("application_status"),
    })


# -----------------------  ingestion  -----------------------

@app.tool()
async def ingest() -> dict:
    """Fetch postings from all sources, pre-filter, and store them.

    Network-heavy: hits every configured ATS board and feed. Returns run
    statistics including per-source outcomes and drop reasons.
    """
    from karani.ingestion.orchestrator import run as run_ingestion

    storage = await _get_storage()
    stats = await run_ingestion(storage)
    return _jsonable({
        "fetched": stats.fetched,
        "inserted": stats.inserted,
        "updated": stats.updated,
        "passed_prefilter": stats.passed_prefilter,
        "stale_closed": stats.stale_closed,
        "dropped_by_reason": dict(stats.dropped_by_reason),
        "per_source": {
            k: {"fetched": o.fetched, "errors": o.errors}
            for k, o in stats.per_source.items()
        },
        "errors": stats.errors[:10],
    })


@app.tool()
async def sweep(days: int | None = None) -> dict:
    """Mark jobs not seen for `days` (default from settings) as closed."""
    storage = await _get_storage()
    threshold = days or settings.stale_job_days
    closed = await storage.sweep_stale(threshold)
    return {"stale_closed": closed, "threshold_days": threshold}


@app.tool()
async def discover(limit: int = 10) -> dict:
    """Probe companies discovered on feed sources for ATS presence
    (Greenhouse/Lever/Ashby/Workable) and promote hits for future ingest
    runs. Network-bound; requires Postgres (no-op on the in-memory
    fallback)."""
    from karani.ingestion.discovery import probe_unpromoted

    storage = await _get_storage()
    outcomes = await probe_unpromoted(storage, limit=limit)
    promoted = await storage.promoted_companies()
    return _jsonable({
        "probed": len(outcomes),
        "promoted_now": sum(1 for o in outcomes if o.get("ats")),
        "outcomes": outcomes,
        "total_promoted": len(promoted),
    })


# -----------------------  qualification  -----------------------

@app.tool()
async def qualify(
    limit: int = 20,
    concurrency: int = 3,
    agent_mode: bool = False,
    provider: str | None = None,
    model: str | None = None,
    resume_path: str | None = None,
) -> dict:
    """LLM-qualify pending pre-filtered rows against the resume.

    Billed per row. `agent_mode=True` runs the tool-using agent loop
    (OpenRouter only) — much more expensive; use on top candidates only.
    """
    from karani.qualification import qualify_pending

    resume = _load_resume(resume_path)
    client = _make_qualifier(provider, model)
    if agent_mode and not hasattr(client, "chat_turn"):
        raise ToolError(
            "agent_mode requires an OpenRouter client; other providers "
            "do not implement chat_turn yet."
        )
    storage = await _get_storage()
    stats = await qualify_pending(
        storage, client, resume,
        limit=limit, concurrency=concurrency, agent_mode=agent_mode,
        memory=await _get_memory(),
    )
    return {
        "fetched": stats.fetched,
        "qualified": stats.qualified,
        "maybe": stats.maybe,
        "skipped": stats.skipped,
        "errors": stats.errors[:10],
    }


# -----------------------  review surface  -----------------------

@app.tool()
async def digest(limit: int = 20, format: str = "md") -> str:
    """Render the top qualified roles as text, md, or html."""
    if format not in {"text", "md", "html"}:
        raise ToolError("format must be one of: text, md, html")
    storage = await _get_storage()
    rows = await storage.top_qualified(limit=limit)
    return render_digest(rows, format)


@app.tool()
async def shortlist(limit: int = 20) -> dict:
    """Top qualified/maybe roles ranked by fit score, as structured rows.

    Excludes roles already reacted to (except `later`).
    """
    storage = await _get_storage()
    rows = await storage.top_qualified(limit=limit)
    return {"count": len(rows), "jobs": [_job_summary(r) for r in rows]}


@app.tool()
async def get_job(job_id: int, include_description: bool = False) -> dict:
    """Full detail for one job: qualification, gaps, state, and optionally
    the job description text."""
    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    detail = _job_summary(row)
    qual = row.get("qualification") or {}
    if isinstance(qual, str):
        try:
            qual = json.loads(qual)
        except json.JSONDecodeError:
            qual = {}
    detail.update(_jsonable({
        "source": row.get("source"),
        "role_category": row.get("role_category"),
        "seniority": row.get("seniority"),
        "remote_status": row.get("remote_status"),
        "qualification": qual,
        "stages": row.get("stages") or [],
        "outcome": row.get("outcome"),
        "draft_path": row.get("draft_path"),
        "posted_at": row.get("posted_at"),
    }))
    if include_description:
        detail["description_text"] = row.get("description_text", "")
    return detail


@app.tool()
async def pipeline_stats() -> dict:
    """DB counts plus the application state-machine funnel."""
    storage = await _get_storage()
    counts = await storage.stats()
    funnel = await storage.pipeline_summary()
    return _jsonable({"counts": dict(counts), "funnel": funnel})


@app.tool()
async def next_actions(review_limit: int = 10, follow_up_days: int = 7) -> dict:
    """Prioritized worklist for the daily loop, bucketed by required action:

    - review: qualified/maybe roles awaiting a verdict (fit + freshness ranked)
    - to_draft: verdict was `apply` but no draft exists yet
    - to_submit: drafts sitting in `drafting`/`ready`
    - follow_up: applied >= follow_up_days ago with no response

    Call this first each session; it is the pipeline's answer to
    "what should happen next".
    """
    storage = await _get_storage()
    return _jsonable(await storage.next_actions(
        review_limit=review_limit, follow_up_days=follow_up_days,
    ))


@app.tool()
async def funnel_stats() -> dict:
    """Conversion funnel over all applications: response, interview, and
    offer rates — overall and split by fit band, source, and qualify/draft
    prompt version. The measurement layer for positioning experiments."""
    storage = await _get_storage()
    return _jsonable(await storage.funnel_stats())


# -----------------------  drafting  -----------------------

@app.tool()
async def draft(
    job_id: int,
    provider: str | None = None,
    model: str | None = None,
    resume_path: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Build the full application pack for a job: cover letter + bullets
    + answers, an AI-voice humanizer pass, a complete tailored resume,
    and artifact uploads to object storage with presigned tweak-and-
    submit links. Moves the job to `drafting`. Billed (up to three LLM
    calls; KARANI_HUMANIZE / KARANI_TAILOR_RESUME toggle the extras)."""
    from karani.drafting import build_application_pack

    resume = _load_resume(resume_path)
    client = _make_qualifier(provider, model)
    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    pack = await build_application_pack(
        client, storage, job_row=row,
        resume_markdown=resume.raw_markdown,
        output_path=output_path,
    )
    if pack.failed:
        raise ToolError("draft failed (malformed LLM output); retry")
    pkg = pack.pkg
    return _jsonable({
        "path": str(pack.draft_path),
        "cover_letter_words": len(pkg.cover_letter.split()),
        "tailored_bullets": len(pkg.tailored_bullets),
        "application_answers": len(pkg.application_answers),
        "subject_line": pkg.subject_line,
        "model": pkg.model,
        "keyword_coverage": pkg.keyword_coverage,
        "keyword_missing": pkg.keyword_missing,
        "voice": pack.voice,
        "tailored_resume_path": (str(pack.resume_path)
                                 if pack.resume_path else None),
        "resume_keyword_coverage": (pack.resume.keyword_coverage
                                    if pack.resume else None),
        "artifacts": pack.artifacts,
    })


@app.tool()
async def prep(
    job_id: int,
    provider: str | None = None,
    model: str | None = None,
    resume_path: str | None = None,
) -> dict:
    """Interview prep pack for a job in the pipeline: company brief from
    the cached dossier, likely questions derived from the qualifier's gap
    analysis (with STAR answers from the resume), specific questions to
    ask grounded in company data, and warm-path openers. Writes
    drafts/prep_<id>_<company>.md. Billed (one LLM call)."""
    from karani.drafting import prep_for_job
    from karani.intel import dossier_text, get_company_intel

    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    company = row.get("company_display") or row.get("company") or ""
    intel = await get_company_intel(storage, company)
    memory = await _get_memory()
    bank = [m["content"] for m in await memory.recall(
        f"{company} interview questions", kind="question", limit=10)]
    pkg, path = await prep_for_job(
        _make_qualifier(provider, model),
        resume=_load_resume(resume_path).raw_markdown,
        job_row=row, qualification=row.get("qualification"),
        dossier=dossier_text(intel), question_bank=bank,
    )
    return _jsonable({
        "path": str(path),
        "company_brief": pkg.company_brief,
        "likely_questions": len(pkg.likely_questions),
        "questions_to_ask": [q.question for q in pkg.questions_to_ask],
        "warm_openers": len(pkg.warm_openers),
    })


@app.tool()
async def draft_followup(
    job_id: int,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """Draft a follow-up note for an application gone quiet, hooked on a
    specific fact from the company dossier. Karani drafts; the user sends.
    Writes drafts/followup_<id>_<company>.md. Billed (one LLM call)."""
    from datetime import datetime, timezone

    from karani.drafting import followup_for_job
    from karani.ingestion.storage import _days_ago
    from karani.intel import dossier_text, get_company_intel

    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    company = row.get("company_display") or row.get("company") or ""
    intel = await get_company_intel(storage, company)
    days = _days_ago(row.get("applied_at"), datetime.now(timezone.utc)) or 0
    pkg, path = await followup_for_job(
        _make_qualifier(provider, model), job_row=row,
        days_since_applied=days, dossier=dossier_text(intel),
    )
    return {"path": str(path), "note": pkg.note,
            "subject_line": pkg.subject_line, "hook_used": pkg.hook_used}


@app.tool()
async def company_intel(company: str, force_refresh: bool = False) -> dict:
    """Cached public-signal dossier for a company: background, engineering
    presence (GitHub), and warm-path candidates. TTL-refreshed (14 days);
    reused by prep, follow-up, and agent-mode qualification."""
    from karani.intel import dossier_text, get_company_intel as fetch_intel

    storage = await _get_storage()
    intel = await fetch_intel(storage, company, force_refresh=force_refresh)
    return _jsonable({
        "company": company,
        "cached": intel.get("cached", False),
        "fetched_at": intel.get("fetched_at"),
        "dossier": dossier_text(intel),
        "warm_candidates": intel["payload"].get("warm_candidates", []),
    })


@app.tool()
async def warm_paths(company: str) -> dict:
    """Warm-path candidates at a company — engineers with a public
    presence (currently: public GitHub org members). Referrals and direct
    contact convert far better than cold portal applications; karani
    surfaces candidates, the user decides whom to contact."""
    from karani.intel import find_warm_paths

    storage = await _get_storage()
    paths = await find_warm_paths(storage, company)
    return _jsonable({"company": company, "count": len(paths),
                      "candidates": paths})


@app.tool()
async def notify_slack(kind: str = "digest", limit: int = 10) -> dict:
    """Push the digest or the next-actions worklist to the configured
    Slack channel (SLACK_CHANNEL + SLACK_BOT_TOKEN). kind: digest |
    actions."""

    from karani.slackbridge import SlackClient, SlackError
    from karani.slackbridge.blocks import actions_blocks, digest_blocks

    if kind not in ("digest", "actions"):
        raise ToolError("kind must be digest or actions")
    from karani.slackbridge import configured_channel
    channel = configured_channel()
    if not channel:
        raise ToolError("SLACK_CHANNEL not set")
    storage = await _get_storage()
    try:
        slack = SlackClient()
        if kind == "digest":
            rows = await storage.top_qualified(limit=limit)
            await slack.post_message(
                channel, f"karani digest: {len(rows)} role(s)",
                blocks=digest_blocks(rows, limit=limit),
            )
            count = len(rows)
        else:
            buckets = await storage.next_actions(review_limit=limit)
            await slack.post_message(channel, "karani: next actions",
                                     blocks=actions_blocks(buckets))
            count = sum(len(v) for v in buckets.values())
    except SlackError as e:
        raise ToolError(str(e)) from e
    return {"kind": kind, "channel": channel, "items": count}


@app.tool()
async def autopilot(min_fit: int | None = None,
                    max_drafts: int | None = None) -> dict:
    """Run one autopilot pass: draft application packs for the top-fit
    qualified roles not yet reviewed and deliver each to Slack as a
    review card with Approve / Skip / I-applied buttons. Billed (one
    draft per candidate, capped by max_drafts, default 3; fit floor
    default 85). Karani never submits — the human does."""

    from karani.autopilot import run_autopilot
    from karani.slackbridge import SlackClient, SlackError

    from karani.slackbridge import configured_channel
    channel = configured_channel()
    if not channel:
        raise ToolError("SLACK_CHANNEL not set — autopilot needs a Slack "
                        "channel to deliver packs to")
    storage = await _get_storage()
    try:
        slack = SlackClient()
    except SlackError as e:
        raise ToolError(str(e)) from e
    stats = await run_autopilot(
        storage, slack=slack, channel=channel,
        make_qualifier=lambda: _make_qualifier(None, None),
        load_resume=lambda: _load_resume(None),
        min_fit=min_fit,
        max_drafts=max_drafts,
    )
    return {"candidates": stats.candidates, "drafted": stats.drafted,
            "delivered": stats.delivered, "errors": stats.errors[:5]}


@app.tool()
async def notion_sync() -> dict:
    """Mirror every tracked application (any job with a verdict or an
    application status) onto the Notion job-hunt board. Idempotent: pages
    are created once and patched thereafter. Requires NOTION_TOKEN and
    NOTION_DATABASE_ID (create the board with `python -m ingestion.cli
    notion init <parent_page_id>`)."""

    from karani.notionsync import NotionClient, NotionError, sync_jobs

    from karani.notionsync import configured_database_id
    database_id = configured_database_id()
    if not database_id:
        raise ToolError("NOTION_DATABASE_ID not set — run "
                        "`notion init <parent_page_id>` first")
    storage = await _get_storage()
    try:
        client = NotionClient()
        return await sync_jobs(storage, client, database_id)
    except NotionError as e:
        raise ToolError(str(e)) from e


# -----------------------  feedback + state machine  -----------------------

@app.tool()
async def record_verdict(job_id: int, verdict: str) -> dict:
    """Record your reaction to a suggestion: apply, shortlist, later, skip,
    or applied. Feeds the taste-calibration few-shot loop on future runs."""
    storage = await _get_storage()
    try:
        await storage.set_user_verdict(job_id, verdict)
    except ValueError as e:
        raise ToolError(str(e)) from e
    row = await storage.get_job(job_id)
    if row:
        memory = await _get_memory()
        await memory.remember_verdict(row, verdict)
    from karani.notionsync import maybe_sync_job
    await maybe_sync_job(storage, job_id)
    return {"job_id": job_id, "user_verdict": verdict}


@app.tool()
async def set_status(job_id: int, status: str,
                     warm_path: bool | None = None) -> dict:
    """Set the application state: new, drafting, ready, applied, screen,
    interview, offer, rejected, declined, or ghosted. When marking
    `applied`, pass warm_path=true/false (referral or direct contact vs
    cold portal) — it feeds the warm-vs-cold split in funnel_stats."""
    storage = await _get_storage()
    try:
        await storage.set_application_status(job_id, status,
                                             warm_path=warm_path)
    except ValueError as e:
        raise ToolError(str(e)) from e
    from karani.notionsync import maybe_sync_job
    await maybe_sync_job(storage, job_id)
    return {"job_id": job_id, "application_status": status,
            "warm_path": warm_path}


@app.tool()
async def add_stage(job_id: int, stage: str, notes: str = "") -> dict:
    """Append an interview/application stage to the job's stage log,
    e.g. recruiter_screen, hiring_manager, technical, onsite. After the
    stage, bank the questions actually asked via record_question — they
    compound into future prep packs."""
    storage = await _get_storage()
    await storage.add_stage(job_id, stage, notes)
    return {"job_id": job_id, "stage": stage, "notes": notes,
            "tip": "bank their questions with record_question"}


@app.tool()
async def record_question(job_id: int, question: str,
                          stage: str = "") -> dict:
    """Bank a question actually asked in an interview for this job.
    Company-scoped: every future prep pack for this company recalls the
    bank, so preps get sharper with each interview."""
    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    memory = await _get_memory()
    result = await memory.remember_question(row, question, stage=stage)
    return _jsonable({**result,
                      "company": row.get("company_display"),
                      "question": question})


@app.tool()
async def record_outcome(job_id: int, outcome: str) -> dict:
    """Record the final outcome: offer, rejection, ghosted, declined, or
    withdrew. Hard signal for future taste calibration."""
    storage = await _get_storage()
    try:
        await storage.set_outcome(job_id, outcome)
    except ValueError as e:
        raise ToolError(str(e)) from e
    row = await storage.get_job(job_id)
    if row:
        memory = await _get_memory()
        await memory.remember_outcome(row, outcome)
    from karani.notionsync import maybe_sync_job
    await maybe_sync_job(storage, job_id)
    return {"job_id": job_id, "outcome": outcome}


# -----------------------  memory  -----------------------

@app.tool()
async def remember(
    content: str,
    kind: str = "preference",
    company: str | None = None,
) -> dict:
    """Store a distilled fact in karani's memory layer, e.g. a preference
    ("prefers infra roles over pure ML"), a company fact ("GitLab responds
    within a week"), or a strategy note ("data-platform angle beats
    causal-ML angle for infra roles"). kind: preference | company |
    outcome | strategy | question. Recalled automatically during
    qualification and on demand via `recall`."""
    memory = await _get_memory()
    try:
        result = await memory.remember(
            content, kind, source="agent", company=company,
        )
    except ValueError as e:
        raise ToolError(str(e)) from e
    return _jsonable(result)


@app.tool()
async def recall(
    query: str,
    kind: str | None = None,
    company: str | None = None,
    limit: int = 5,
) -> dict:
    """Query karani's memory layer. Returns the most relevant stored facts
    for a free-text query, optionally filtered by kind or scoped to a
    company (company scoping still includes global memories)."""
    memory = await _get_memory()
    rows = await memory.recall(query, kind=kind, company=company, limit=limit)
    return _jsonable({"count": len(rows), "memories": rows})
