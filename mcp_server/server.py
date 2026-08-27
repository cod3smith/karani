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

from ingestion.config import settings
from ingestion.digest import render as render_digest
from ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
from ingestion.storage import Storage

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
        from memory import MemoryManager
        _memory = MemoryManager(storage)
    return _memory


def use_storage(storage: Storage | None) -> None:
    """Inject a pre-connected Storage (tests / embedding). None resets."""
    global _storage, _memory
    _storage = storage
    _memory = None


def _make_qualifier(provider: str | None, model: str | None):
    from qualification import get_qualifier
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
    from ingestion.orchestrator import run as run_ingestion

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
    from ingestion.discovery import probe_unpromoted

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
    from qualification import qualify_pending

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
    """Draft a cover letter, tailored bullets, and application answers for a
    job. Writes drafts/<id>_<company>__<title>.md and moves the job to the
    `drafting` state. Billed (one LLM call)."""
    from drafting import draft_for_job

    resume = _load_resume(resume_path)
    client = _make_qualifier(provider, model)
    storage = await _get_storage()
    row = await storage.get_job(job_id)
    if row is None:
        raise ToolError(f"no job with id={job_id}")
    pkg, path = await draft_for_job(
        client, resume=resume.raw_markdown,
        job_row=row, qualification=row.get("qualification"),
        output_path=output_path,
    )
    await storage.record_draft(job_id, str(path),
                               prompt_version=pkg.prompt_version,
                               model=pkg.model)
    return {
        "path": str(path),
        "cover_letter_words": len(pkg.cover_letter.split()),
        "tailored_bullets": len(pkg.tailored_bullets),
        "application_answers": len(pkg.application_answers),
        "subject_line": pkg.subject_line,
        "model": pkg.model,
    }


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
    return {"job_id": job_id, "user_verdict": verdict}


@app.tool()
async def set_status(job_id: int, status: str) -> dict:
    """Set the application state: new, drafting, ready, applied, screen,
    interview, offer, rejected, declined, or ghosted."""
    storage = await _get_storage()
    try:
        await storage.set_application_status(job_id, status)
    except ValueError as e:
        raise ToolError(str(e)) from e
    return {"job_id": job_id, "application_status": status}


@app.tool()
async def add_stage(job_id: int, stage: str, notes: str = "") -> dict:
    """Append an interview/application stage to the job's stage log,
    e.g. recruiter_screen, hiring_manager, technical, onsite."""
    storage = await _get_storage()
    await storage.add_stage(job_id, stage, notes)
    return {"job_id": job_id, "stage": stage, "notes": notes}


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
