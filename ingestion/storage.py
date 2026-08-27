"""Postgres storage with an in-memory fallback for local runs."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import asyncpg

from .models import Job, PreFilterResult


def _company_normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    canonical_hash TEXT,

    company TEXT NOT NULL,
    company_display TEXT NOT NULL,
    title TEXT NOT NULL,
    department TEXT,
    team TEXT,

    location_raw TEXT,
    remote_status TEXT,

    description_text TEXT NOT NULL,
    apply_url TEXT NOT NULL,

    posted_at TIMESTAMPTZ,

    comp_min_usd INTEGER,
    comp_max_usd INTEGER,
    comp_disclosed BOOLEAN DEFAULT FALSE,
    comp_currency_original TEXT,

    tags TEXT[] DEFAULT '{}',
    raw JSONB,

    prefilter JSONB,
    prefilter_passed BOOLEAN,
    prefilter_score INTEGER DEFAULT 0,
    role_category TEXT,
    seniority TEXT,

    qualification JSONB,
    verdict TEXT,
    fit_score INTEGER,
    qualified_at TIMESTAMPTZ,
    qualification_resume_hash TEXT,
    -- User feedback loop — populated when Kelyn reacts to a suggestion.
    user_verdict TEXT,             -- apply | shortlist | later | skip
    user_verdict_at TIMESTAMPTZ,

    -- Application state machine
    application_status TEXT,       -- new | drafting | ready | applied | screen | interview | offer | rejected | declined | ghosted
    applied_at TIMESTAMPTZ,
    stages JSONB DEFAULT '[]'::jsonb,   -- [{stage, at, notes}, ...]
    outcome TEXT,                  -- offer | rejection | ghosted | declined | withdrew
    outcome_at TIMESTAMPTZ,
    draft_path TEXT,

    active BOOLEAN DEFAULT TRUE,
    closed_at TIMESTAMPTZ,

    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (source, source_id)
);

-- Add columns if the table pre-existed from an earlier schema.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS canonical_hash TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS comp_currency_original TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS prefilter_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS role_category TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS seniority TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS fit_score INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS qualified_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS qualification_resume_hash TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_verdict TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_verdict_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS application_status TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS stages JSONB DEFAULT '[]'::jsonb;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS outcome_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS draft_path TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS draft_prompt_version TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS draft_model TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS draft_keyword_coverage REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS warm_path_used BOOLEAN;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS notion_page_id TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS drafted_at TIMESTAMPTZ;

-- Company intelligence cache — dossiers built from public probes (GitHub,
-- Wikipedia, engineering blog). TTL-refreshed; consumed by agent-mode
-- qualification, interview prep, and follow-up drafting.
CREATE TABLE IF NOT EXISTS company_intel (
    company_normalized TEXT PRIMARY KEY,
    company_display TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- Memory ledger — the durable system of record for karani's memory layer.
-- Distilled, human-readable facts ("Kelyn skips crypto companies", "GitLab
-- responded in 5 days"). The mem0/vector index is derived from this table
-- and can always be rebuilt from it. See docs/memory.md.
CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,            -- preference | company | outcome | strategy | question
    content TEXT NOT NULL,
    source TEXT NOT NULL,          -- verdict | outcome | manual | agent
    job_id BIGINT,
    company TEXT,                  -- normalized company handle, when scoped
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS memories_kind_idx ON memories (kind) WHERE active;
CREATE INDEX IF NOT EXISTS memories_company_idx ON memories (company) WHERE active;

-- Companies discovered via feed sources; queued for reverse-ATS probing.
CREATE TABLE IF NOT EXISTS discovered_companies (
    id BIGSERIAL PRIMARY KEY,
    company_display TEXT NOT NULL,
    company_normalized TEXT NOT NULL,
    first_seen_source TEXT,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    probed_at TIMESTAMPTZ,
    probe_results JSONB DEFAULT '{}'::jsonb,  -- {greenhouse: bool, lever: bool, ...}
    ats_source TEXT,                          -- best-guess winner
    ats_slug TEXT,
    promoted_at TIMESTAMPTZ,
    UNIQUE (company_normalized)
);

CREATE INDEX IF NOT EXISTS jobs_content_hash_idx ON jobs (content_hash);
CREATE INDEX IF NOT EXISTS jobs_fit_score_idx ON jobs (fit_score DESC)
    WHERE verdict IN ('qualified', 'maybe') AND active = TRUE;
CREATE INDEX IF NOT EXISTS jobs_user_verdict_idx ON jobs (user_verdict);
CREATE INDEX IF NOT EXISTS jobs_application_status_idx ON jobs (application_status);
CREATE INDEX IF NOT EXISTS jobs_applied_at_idx ON jobs (applied_at DESC)
    WHERE applied_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_canonical_hash_idx ON jobs (canonical_hash);
CREATE INDEX IF NOT EXISTS jobs_posted_at_idx ON jobs (posted_at DESC);
CREATE INDEX IF NOT EXISTS jobs_prefilter_passed_idx ON jobs (prefilter_passed)
    WHERE prefilter_passed = TRUE;
CREATE INDEX IF NOT EXISTS jobs_verdict_idx ON jobs (verdict);
CREATE INDEX IF NOT EXISTS jobs_active_idx ON jobs (active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS jobs_score_idx ON jobs (prefilter_score DESC)
    WHERE prefilter_passed = TRUE AND active = TRUE;
"""


UPSERT = """
INSERT INTO jobs (
    source, source_id, content_hash, canonical_hash,
    company, company_display, title, department, team,
    location_raw, remote_status, description_text, apply_url,
    posted_at, comp_min_usd, comp_max_usd, comp_disclosed,
    comp_currency_original, tags, raw,
    prefilter, prefilter_passed, prefilter_score, role_category, seniority,
    active, closed_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
    $14, $15, $16, $17, $18, $19, $20::jsonb,
    $21::jsonb, $22, $23, $24, $25, TRUE, NULL
)
ON CONFLICT (source, source_id) DO UPDATE SET
    last_seen_at = NOW(),
    active = TRUE,
    closed_at = NULL,
    updated_at = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN NOW()
        ELSE jobs.updated_at
    END,
    content_hash = EXCLUDED.content_hash,
    canonical_hash = EXCLUDED.canonical_hash,
    title = EXCLUDED.title,
    description_text = EXCLUDED.description_text,
    location_raw = EXCLUDED.location_raw,
    remote_status = EXCLUDED.remote_status,
    comp_min_usd = EXCLUDED.comp_min_usd,
    comp_max_usd = EXCLUDED.comp_max_usd,
    comp_disclosed = EXCLUDED.comp_disclosed,
    comp_currency_original = EXCLUDED.comp_currency_original,
    tags = EXCLUDED.tags,
    raw = EXCLUDED.raw,
    prefilter = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN EXCLUDED.prefilter
        ELSE jobs.prefilter
    END,
    prefilter_passed = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN EXCLUDED.prefilter_passed
        ELSE jobs.prefilter_passed
    END,
    prefilter_score = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN EXCLUDED.prefilter_score
        ELSE jobs.prefilter_score
    END,
    role_category = EXCLUDED.role_category,
    seniority = EXCLUDED.seniority,
    qualification = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN NULL
        ELSE jobs.qualification
    END,
    verdict = CASE
        WHEN jobs.content_hash <> EXCLUDED.content_hash THEN NULL
        ELSE jobs.verdict
    END
RETURNING id, (xmax = 0) AS inserted;
"""


SWEEP_STALE = """
UPDATE jobs
   SET active = FALSE, closed_at = NOW()
 WHERE active = TRUE
   AND last_seen_at < NOW() - ($1::int || ' days')::interval;
"""


# --- Pure aggregation helpers, shared by the Postgres and in-memory paths ---
# so the two backends cannot drift on funnel/action semantics.

def _parse_qual(row: dict) -> dict:
    q = row.get("qualification") or {}
    if isinstance(q, str):
        try:
            q = json.loads(q)
        except json.JSONDecodeError:
            q = {}
    return q


def _fit_band(fit: int | None) -> str:
    if fit is None:
        return "unscored"
    if fit >= 90:
        return "90+"
    if fit >= 80:
        return "80-89"
    if fit >= 70:
        return "70-79"
    return "<70"


def _days_ago(ts: Any, now: datetime) -> int | None:
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, (now - ts).days)


def _action_item(row: dict, now: datetime) -> dict:
    posted_days = _days_ago(row.get("posted_at"), now)
    fit = row.get("fit_score")
    return {
        "id": row.get("id"),
        "company": row.get("company_display") or row.get("company"),
        "title": row.get("title"),
        "fit_score": fit,
        "apply_url": row.get("apply_url"),
        "posted_days_ago": posted_days,
        "application_status": row.get("application_status"),
        # Response odds decay fast with posting age: high fit + fresh post
        # means apply TODAY, not this week.
        "fast_lane": bool(fit is not None and fit >= 85
                          and posted_days is not None and posted_days <= 3),
    }


def _bucket_actions(
    rows: list[dict], *, now: datetime, follow_up_days: int, review_limit: int,
) -> dict:
    review: list[dict] = []
    to_draft: list[dict] = []
    to_submit: list[dict] = []
    follow_up: list[dict] = []
    for r in rows:
        if not r.get("active", True) or r.get("outcome"):
            continue
        status = r.get("application_status")
        if status == "applied":
            days = _days_ago(r.get("applied_at"), now)
            if days is not None and days >= follow_up_days:
                item = _action_item(r, now)
                item["applied_days_ago"] = days
                follow_up.append(item)
        elif status in ("drafting", "ready"):
            to_submit.append(_action_item(r, now))
        elif status is None and r.get("user_verdict") == "apply":
            to_draft.append(_action_item(r, now))
        elif (status is None and r.get("user_verdict") is None
              and r.get("verdict") in ("qualified", "maybe")):
            review.append(_action_item(r, now))
    # Response odds decay with posting age: rank by fit, then freshness.
    review.sort(key=lambda a: (
        -(a["fit_score"] or 0),
        a["posted_days_ago"] if a["posted_days_ago"] is not None else 9999,
    ))
    follow_up.sort(key=lambda a: -(a.get("applied_days_ago") or 0))
    return {
        "review": review[:review_limit],
        "to_draft": to_draft,
        "to_submit": to_submit,
        "follow_up": follow_up,
    }


# --- Deterministic memory recall (the `basic` mode of the memory layer) ---
# Token-overlap scoring, stopword-filtered. No LLM, no embeddings: works in
# tests and without any memory extra installed. mem0 replaces the *ranking*,
# never the ledger.

_MEMORY_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or that the "
    "this to was we will with our their they you your".split()
)


def _memory_tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(t) > 1 and t not in _MEMORY_STOPWORDS
    }


def _score_memories(query: str, rows: list[dict], limit: int) -> list[dict]:
    q = _memory_tokens(query)
    scored: list[tuple[int, dict]] = []
    for r in rows:
        overlap = len(q & _memory_tokens(r.get("content", "")))
        # Company-scoped memories outrank generic ones for that company.
        if r.get("company") and r["company"] in _company_normalize(query):
            overlap += 3
        if overlap:
            scored.append((overlap, r))
    scored.sort(key=lambda p: (-p[0], -(p[1].get("id") or 0)))
    return [r for _, r in scored[:limit]]


# "Responded" = a human at the company reacted: any post-application stage,
# a rejection, or an offer. Ghosting is the absence of all of these.
_RESPONDED_STATUSES = frozenset({"screen", "interview", "offer", "rejected",
                                 "declined"})
_RESPONDED_OUTCOMES = frozenset({"offer", "rejection", "declined"})


def _posting_age_band(row: dict) -> str:
    """Posting age at application time — the freshness-urgency split."""
    posted, applied = row.get("posted_at"), row.get("applied_at")
    if not isinstance(posted, datetime) or not isinstance(applied, datetime):
        return "unknown"
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    if applied.tzinfo is None:
        applied = applied.replace(tzinfo=timezone.utc)
    days = max(0, (applied - posted).days)
    if days <= 3:
        return "0-3d"
    if days <= 7:
        return "4-7d"
    if days <= 14:
        return "8-14d"
    return "15d+"


def _aggregate_funnel(rows: list[dict]) -> dict:
    totals = {"applied": 0, "responded": 0, "interviewed": 0,
              "offers": 0, "ghosted": 0}
    by_fit: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    by_qual_prompt: dict[str, dict] = {}
    by_draft_prompt: dict[str, dict] = {}
    by_warm_path: dict[str, dict] = {}
    by_posting_age: dict[str, dict] = {}

    def bump(d: dict, key: str, responded: bool) -> None:
        b = d.setdefault(key, {"applied": 0, "responded": 0})
        b["applied"] += 1
        b["responded"] += int(responded)

    for r in rows:
        status = r.get("application_status")
        outcome = r.get("outcome")
        responded = (status in _RESPONDED_STATUSES
                     or outcome in _RESPONDED_OUTCOMES)
        totals["applied"] += 1
        totals["responded"] += int(responded)
        totals["interviewed"] += int(status in ("interview", "offer")
                                     or outcome == "offer")
        totals["offers"] += int(status == "offer" or outcome == "offer")
        totals["ghosted"] += int(outcome == "ghosted")
        bump(by_fit, _fit_band(r.get("fit_score")), responded)
        bump(by_source, r.get("source") or "unknown", responded)
        bump(by_qual_prompt,
             _parse_qual(r).get("prompt_version") or "none", responded)
        bump(by_draft_prompt,
             r.get("draft_prompt_version") or "none", responded)
        warm = r.get("warm_path_used")
        bump(by_warm_path,
             "warm" if warm else ("cold" if warm is not None else "unmarked"),
             responded)
        bump(by_posting_age, _posting_age_band(r), responded)

    def add_rates(d: dict) -> None:
        for b in d.values():
            b["response_rate"] = (round(b["responded"] / b["applied"], 3)
                                  if b["applied"] else 0.0)

    for d in (by_fit, by_source, by_qual_prompt, by_draft_prompt,
              by_warm_path, by_posting_age):
        add_rates(d)
    n = totals["applied"]
    totals["response_rate"] = round(totals["responded"] / n, 3) if n else 0.0
    totals["interview_rate"] = round(totals["interviewed"] / n, 3) if n else 0.0
    totals["offer_rate"] = round(totals["offers"] / n, 3) if n else 0.0
    return {
        "totals": totals,
        "by_fit_band": by_fit,
        "by_source": by_source,
        "by_qualify_prompt": by_qual_prompt,
        "by_draft_prompt": by_draft_prompt,
        "by_warm_path": by_warm_path,
        "by_posting_age": by_posting_age,
        "autopsy": _autopsy(rows),
    }


def _autopsy(rows: list[dict]) -> dict:
    """Rejection/ghost autopsy: which attributes separate responders from
    silence. Purely descriptive — findings become filter/positioning
    adjustments by hand (roadmap 1.5.7)."""
    by_seniority: dict[str, dict] = {}
    by_remote: dict[str, dict] = {}
    coverage_responded: list[float] = []
    coverage_silent: list[float] = []

    def bump(d: dict, key: str, responded: bool) -> None:
        b = d.setdefault(key, {"applied": 0, "responded": 0})
        b["applied"] += 1
        b["responded"] += int(responded)

    for r in rows:
        responded = (r.get("application_status") in _RESPONDED_STATUSES
                     or r.get("outcome") in _RESPONDED_OUTCOMES)
        bump(by_seniority, r.get("seniority") or "unknown", responded)
        bump(by_remote, r.get("remote_status") or "unknown", responded)
        cov = r.get("draft_keyword_coverage")
        if cov is not None:
            (coverage_responded if responded else coverage_silent).append(cov)

    def rates(d: dict) -> dict:
        for b in d.values():
            b["response_rate"] = (round(b["responded"] / b["applied"], 3)
                                  if b["applied"] else 0.0)
        return d

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 3) if xs else None

    return {
        "by_seniority": rates(by_seniority),
        "by_remote_status": rates(by_remote),
        "keyword_coverage": {
            "responded_avg": avg(coverage_responded),
            "silent_avg": avg(coverage_silent),
        },
    }


class Storage:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None
        self._memory: dict[tuple[str, str], dict[str, Any]] = {}
        self._next_id = 1
        self._memories: list[dict[str, Any]] = []
        self._next_memory_id = 1
        self._company_intel: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        if not self.dsn:
            log.warning("No DATABASE_URL — using in-memory fallback")
            return
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            async with self.pool.acquire() as conn:
                await conn.execute(SCHEMA)
        except Exception as exc:  # pragma: no cover - local fallback path
            self.pool = None
            self._memory = {}
            self._next_id = 1
            log.warning("Postgres unavailable (%s); using in-memory fallback", exc)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def upsert(self, job: Job, prefilter: PreFilterResult) -> dict[str, Any]:
        if self.pool is None:
            return self._upsert_memory(job, prefilter)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                UPSERT,
                job.source.value,
                job.source_id,
                job.content_hash,
                job.canonical_hash,
                job.company,
                job.company_display,
                job.title,
                job.department,
                job.team,
                job.location_raw,
                job.remote_status.value,
                job.description_text,
                job.apply_url,
                job.posted_at,
                job.comp_min_usd,
                job.comp_max_usd,
                job.comp_disclosed,
                job.comp_currency_original,
                job.tags,
                json.dumps(job.raw, default=str),
                prefilter.model_dump_json(),
                prefilter.pass_hard_filters,
                prefilter.score,
                prefilter.role_category.value,
                prefilter.seniority.value,
            )
            return {"id": row["id"], "inserted": row["inserted"]}

    def _upsert_memory(
        self, job: Job, prefilter: PreFilterResult,
    ) -> dict[str, Any]:
        key = (job.source.value, job.source_id)
        existing = self._memory.get(key)
        inserted = existing is None
        row_id = self._next_id if inserted else existing["id"]
        if inserted:
            self._next_id += 1
        self._memory[key] = {
            "id": row_id,
            "source": job.source.value,
            "source_id": job.source_id,
            "content_hash": job.content_hash,
            "canonical_hash": job.canonical_hash,
            "company": job.company,
            "company_display": job.company_display,
            "title": job.title,
            "description_text": job.description_text,
            "location_raw": job.location_raw,
            "remote_status": job.remote_status.value,
            "comp_min_usd": job.comp_min_usd,
            "comp_max_usd": job.comp_max_usd,
            "comp_disclosed": job.comp_disclosed,
            "comp_currency_original": job.comp_currency_original,
            "tags": job.tags,
            "raw": job.raw,
            "posted_at": job.posted_at,
            "prefilter": prefilter.model_dump(),
            "prefilter_passed": prefilter.pass_hard_filters,
            "prefilter_score": prefilter.score,
            "role_category": prefilter.role_category.value,
            "seniority": prefilter.seniority.value,
            "verdict": None,
            "active": True,
        }
        return {"id": row_id, "inserted": inserted}

    # --- Qualification I/O ---

    async def pending_qualification(
        self, *, limit: int = 50, resume_hash: str | None = None,
    ) -> list[dict]:
        """Return top-scored rows that need LLM qualification.

        A row is considered pending if it passed the pre-filter AND is active
        AND either has no qualification yet OR was qualified against a
        different resume hash.
        """
        if self.pool is None:
            rows = [r for r in self._memory.values() if r.get("prefilter_passed")
                    and r.get("active", True)]
            if resume_hash:
                rows = [r for r in rows
                        if not r.get("qualification")
                        or r.get("qualification_resume_hash") != resume_hash]
            else:
                rows = [r for r in rows if not r.get("qualification")]
            rows.sort(key=lambda r: r.get("prefilter_score", 0), reverse=True)
            return rows[:limit]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, source, company, company_display, title,
                       role_category, seniority, location_raw, remote_status,
                       comp_min_usd, comp_max_usd, description_text,
                       apply_url, prefilter_score, prefilter,
                       qualification_resume_hash
                  FROM jobs
                 WHERE prefilter_passed = TRUE
                   AND active = TRUE
                   AND (qualification IS NULL
                        OR ($1::text IS NOT NULL
                            AND qualification_resume_hash IS DISTINCT FROM $1))
                 ORDER BY prefilter_score DESC, posted_at DESC
                 LIMIT $2
                """,
                resume_hash, limit,
            )
            return [dict(r) for r in rows]

    async def store_qualification(self, job_id: int, result) -> None:
        """Write back the QualificationResult. Bumps qualified_at."""
        payload = result.model_dump_json() if hasattr(result, "model_dump_json") \
            else __import__("json").dumps(result)
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["qualification"] = (
                        result.model_dump() if hasattr(result, "model_dump") else result
                    )
                    row["verdict"] = getattr(result, "verdict", None)
                    row["fit_score"] = getattr(result, "fit_score", None)
                    row["qualification_resume_hash"] = getattr(
                        result, "resume_hash", None
                    )
                    return
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                   SET qualification = $2::jsonb,
                       verdict = $3,
                       fit_score = $4,
                       qualification_resume_hash = $5,
                       qualified_at = NOW()
                 WHERE id = $1
                """,
                job_id,
                payload,
                getattr(result, "verdict", None),
                getattr(result, "fit_score", None),
                getattr(result, "resume_hash", None),
            )

    async def set_user_verdict(self, job_id: int, verdict: str) -> None:
        """Kelyn reacted to a suggestion. Used for the feedback loop."""
        allowed = {"apply", "shortlist", "later", "skip", "applied"}
        if verdict not in allowed:
            raise ValueError(f"user_verdict must be one of {allowed}")
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["user_verdict"] = verdict
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET user_verdict = $2, user_verdict_at = NOW() "
                "WHERE id = $1",
                job_id, verdict,
            )

    async def get_job(self, job_id: int) -> dict | None:
        """Fetch a single job row by id, or None if absent."""
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    return row
            return None
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            return dict(r) if r else None

    async def top_qualified(self, limit: int = 20) -> list[dict]:
        """Top ranked qualified/maybe rows for the daily digest."""
        if self.pool is None:
            rows = [r for r in self._memory.values()
                    if r.get("verdict") in {"qualified", "maybe"}
                    and r.get("active", True)
                    and r.get("user_verdict") in (None, "later")]
            rows.sort(key=lambda r: (r.get("fit_score") or 0), reverse=True)
            return rows[:limit]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, company_display, title, apply_url, location_raw,
                       role_category, seniority, comp_min_usd, comp_max_usd,
                       verdict, fit_score, qualification, prefilter_score,
                       posted_at, user_verdict
                  FROM jobs
                 WHERE verdict IN ('qualified', 'maybe')
                   AND active = TRUE
                   AND (user_verdict IS NULL OR user_verdict = 'later')
                 ORDER BY fit_score DESC NULLS LAST, prefilter_score DESC
                 LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]

    # --- Application state machine ---

    APPLICATION_STATUSES = frozenset({
        "new", "drafting", "ready", "applied",
        "screen", "interview", "offer",
        "rejected", "declined", "ghosted",
    })
    OUTCOMES = frozenset({"offer", "rejection", "ghosted", "declined", "withdrew"})

    async def set_application_status(
        self, job_id: int, status: str, draft_path: str | None = None,
        warm_path: bool | None = None,
    ) -> None:
        """`warm_path` marks whether the application went through a warm
        contact (referral/direct outreach) — the warm-vs-cold split in
        `funnel_stats` depends on it. None leaves the flag untouched."""
        if status not in self.APPLICATION_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(self.APPLICATION_STATUSES)}"
            )
        applied_at_expr = "NOW()" if status == "applied" else "applied_at"
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["application_status"] = status
                    if draft_path:
                        row["draft_path"] = draft_path
                    if warm_path is not None:
                        row["warm_path_used"] = warm_path
                    if status == "applied":
                        row["applied_at"] = datetime.now(timezone.utc)
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE jobs
                   SET application_status = $2,
                       draft_path = COALESCE($3, draft_path),
                       warm_path_used = COALESCE($4, warm_path_used),
                       applied_at = {applied_at_expr}
                 WHERE id = $1
                """,
                job_id, status, draft_path, warm_path,
            )

    async def record_draft(
        self, job_id: int, path: str,
        prompt_version: str = "", model: str = "",
        keyword_coverage: float | None = None,
    ) -> None:
        """A draft was generated: move to `drafting` and persist provenance.

        `draft_prompt_version` and `draft_keyword_coverage` are what make
        drafts A/B-measurable in `funnel_stats` — response rate by prompt
        version and by ATS keyword coverage.
        """
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["application_status"] = "drafting"
                    row["draft_path"] = path
                    row["draft_prompt_version"] = prompt_version
                    row["draft_model"] = model
                    row["draft_keyword_coverage"] = keyword_coverage
                    row["drafted_at"] = datetime.now(timezone.utc)
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                   SET application_status = 'drafting',
                       draft_path = $2,
                       draft_prompt_version = $3,
                       draft_model = $4,
                       draft_keyword_coverage = $5,
                       drafted_at = NOW()
                 WHERE id = $1
                """,
                job_id, path, prompt_version, model, keyword_coverage,
            )

    async def drafts_today(self, now: datetime | None = None) -> int:
        """Drafts generated today (UTC) — the autopilot daily-budget gate."""
        now = now or datetime.now(timezone.utc)
        if self.pool is None:
            count = 0
            for row in self._memory.values():
                ts = row.get("drafted_at")
                if isinstance(ts, datetime):
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts.date() == now.date():
                        count += 1
            return count
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM jobs "
                "WHERE drafted_at::date = $1::date",
                now,
            )

    async def add_stage(self, job_id: int, stage: str, notes: str = "") -> None:
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row.setdefault("stages", []).append(
                        {"stage": stage, "notes": notes, "at": "now"}
                    )
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                   SET stages = COALESCE(stages, '[]'::jsonb)
                              || jsonb_build_array(jsonb_build_object(
                                    'stage', $2::text,
                                    'notes', $3::text,
                                    'at', to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SSZ')
                                 ))
                 WHERE id = $1
                """,
                job_id, stage, notes,
            )

    async def set_outcome(self, job_id: int, outcome: str) -> None:
        if outcome not in self.OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(self.OUTCOMES)}")
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["outcome"] = outcome
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET outcome = $2, outcome_at = NOW() WHERE id = $1",
                job_id, outcome,
            )

    async def pipeline_summary(self) -> dict:
        """Funnel counts for the state machine — used by the digest header."""
        if self.pool is None:
            rows = list(self._memory.values())
            counts: dict[str, int] = {}
            for r in rows:
                s = r.get("application_status")
                if s:
                    counts[s] = counts.get(s, 0) + 1
            return counts
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT application_status, COUNT(*) AS n FROM jobs "
                "WHERE application_status IS NOT NULL GROUP BY application_status"
            )
            return {r["application_status"]: r["n"] for r in rows}

    async def next_actions(
        self, *, review_limit: int = 10, follow_up_days: int = 7,
        now: datetime | None = None,
    ) -> dict:
        """Prioritized worklist for the daily loop, bucketed:

        - review: qualified/maybe roles awaiting a user verdict
        - to_draft: verdict was `apply` but no draft started
        - to_submit: drafts sitting in `drafting`/`ready`
        - follow_up: applied >= `follow_up_days` ago, no response yet
        """
        now = now or datetime.now(timezone.utc)
        if self.pool is None:
            rows = list(self._memory.values())
        else:
            async with self.pool.acquire() as conn:
                records = await conn.fetch(
                    """
                    SELECT id, company, company_display, title, fit_score,
                           verdict, user_verdict, application_status, outcome,
                           active, apply_url, posted_at, applied_at
                      FROM jobs
                     WHERE active = TRUE
                       AND outcome IS NULL
                       AND (application_status IN ('applied', 'drafting', 'ready')
                            OR (application_status IS NULL
                                AND (user_verdict = 'apply'
                                     OR (user_verdict IS NULL
                                         AND verdict IN ('qualified', 'maybe')))))
                    """
                )
                rows = [dict(r) for r in records]
        return _bucket_actions(rows, now=now, follow_up_days=follow_up_days,
                               review_limit=review_limit)

    async def funnel_stats(self) -> dict:
        """Conversion funnel over everything ever applied to.

        Response/interview/offer rates overall and split by fit band,
        source, and qualify/draft prompt versions — the measurement layer
        for every downstream intelligence experiment.
        """
        if self.pool is None:
            rows = [r for r in self._memory.values() if r.get("applied_at")]
        else:
            async with self.pool.acquire() as conn:
                records = await conn.fetch(
                    """
                    SELECT source, fit_score, qualification, seniority,
                           remote_status, draft_prompt_version,
                           draft_keyword_coverage, warm_path_used,
                           posted_at, applied_at, application_status, outcome
                      FROM jobs
                     WHERE applied_at IS NOT NULL
                    """
                )
                rows = [dict(r) for r in records]
        return _aggregate_funnel(rows)

    # --- Feedback loop / few-shot examples ---

    async def recent_user_verdicts(self, limit: int = 30) -> list[dict]:
        """Recent {job, user_verdict} pairs for the qualifier's few-shot block."""
        if self.pool is None:
            rows = [r for r in self._memory.values() if r.get("user_verdict")]
            return rows[:limit]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT company_display, title, role_category, seniority,
                       comp_min_usd, comp_max_usd, location_raw,
                       fit_score, verdict, user_verdict,
                       LEFT(description_text, 400) AS description_snippet
                  FROM jobs
                 WHERE user_verdict IS NOT NULL
                 ORDER BY user_verdict_at DESC NULLS LAST
                 LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]

    # --- Re-filtering (config/profile changed; see cli `refilter`) ---

    async def active_jobs(self) -> list[dict]:
        """Every active row with enough fields to rebuild a Job for
        re-running the pre-filter after a rules change."""
        if self.pool is None:
            return [dict(r) for r in self._memory.values()
                    if r.get("active", True)]
        async with self.pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT id, source, source_id, company, company_display,
                       title, location_raw, remote_status, description_text,
                       apply_url, posted_at, comp_min_usd, comp_max_usd,
                       comp_disclosed, comp_currency_original, tags,
                       prefilter_passed
                  FROM jobs
                 WHERE active = TRUE
                """
            )
            return [dict(r) for r in records]

    async def update_prefilter(self, job_id: int, pf) -> None:
        """Write back a re-run PreFilterResult. Qualification is untouched
        — rows that newly pass will be picked up by the next qualify."""
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["prefilter"] = pf.model_dump()
                    row["prefilter_passed"] = pf.pass_hard_filters
                    row["prefilter_score"] = pf.score
                    row["role_category"] = pf.role_category.value
                    row["seniority"] = pf.seniority.value
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                   SET prefilter = $2::jsonb,
                       prefilter_passed = $3,
                       prefilter_score = $4,
                       role_category = $5,
                       seniority = $6
                 WHERE id = $1
                """,
                job_id, pf.model_dump_json(), pf.pass_hard_filters,
                pf.score, pf.role_category.value, pf.seniority.value,
            )

    # --- Autopilot (see autopilot/) ---

    async def autopilot_candidates(self, *, min_fit: int = 85,
                                   limit: int = 3) -> list[dict]:
        """Top qualified roles awaiting nothing but a pack: high fit,
        active, no user verdict yet, not yet in the state machine."""
        def eligible(r: dict) -> bool:
            return (r.get("verdict") == "qualified"
                    and (r.get("fit_score") or 0) >= min_fit
                    and r.get("active", True)
                    and not r.get("user_verdict")
                    and not r.get("application_status"))

        if self.pool is None:
            rows = sorted((dict(r) for r in self._memory.values()
                           if eligible(r)),
                          key=lambda r: -(r.get("fit_score") or 0))
            return rows[:limit]
        async with self.pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT * FROM jobs
                 WHERE verdict = 'qualified'
                   AND fit_score >= $1
                   AND active = TRUE
                   AND user_verdict IS NULL
                   AND application_status IS NULL
                 ORDER BY fit_score DESC, posted_at DESC
                 LIMIT $2
                """,
                min_fit, limit,
            )
            return [dict(r) for r in records]

    # --- Notion mirror (see notionsync/) ---

    async def tracked_jobs(self) -> list[dict]:
        """Everything worth mirroring to the Notion board: any job Kelyn
        reacted to or that entered the application state machine."""
        if self.pool is None:
            return [dict(r) for r in self._memory.values()
                    if r.get("user_verdict") or r.get("application_status")]
        async with self.pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT id, company_display, company, title, apply_url,
                       fit_score, verdict, user_verdict, application_status,
                       applied_at, outcome, warm_path_used, draft_path,
                       draft_keyword_coverage, notion_page_id
                  FROM jobs
                 WHERE user_verdict IS NOT NULL
                    OR application_status IS NOT NULL
                """
            )
            return [dict(r) for r in records]

    async def set_notion_page(self, job_id: int, page_id: str) -> None:
        if self.pool is None:
            for row in self._memory.values():
                if row["id"] == job_id:
                    row["notion_page_id"] = page_id
                    return
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET notion_page_id = $2 WHERE id = $1",
                job_id, page_id,
            )

    # --- Company intel cache (see intel/) ---

    async def get_company_intel(self, company: str) -> dict | None:
        """Cached dossier for a company, with `fetched_at` for TTL checks."""
        normalized = _company_normalize(company)
        if self.pool is None:
            row = self._company_intel.get(normalized)
            return dict(row) if row else None
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT company_display, payload, fetched_at "
                "FROM company_intel WHERE company_normalized = $1",
                normalized,
            )
            if not r:
                return None
            payload = r["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return {"company_display": r["company_display"],
                    "payload": payload, "fetched_at": r["fetched_at"]}

    async def save_company_intel(self, company: str, payload: dict) -> None:
        normalized = _company_normalize(company)
        if self.pool is None:
            self._company_intel[normalized] = {
                "company_display": company, "payload": payload,
                "fetched_at": datetime.now(timezone.utc),
            }
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO company_intel
                    (company_normalized, company_display, payload, fetched_at)
                VALUES ($1, $2, $3::jsonb, NOW())
                ON CONFLICT (company_normalized) DO UPDATE SET
                    company_display = EXCLUDED.company_display,
                    payload = EXCLUDED.payload,
                    fetched_at = NOW()
                """,
                normalized, company, json.dumps(payload, default=str),
            )

    # --- Memory ledger (see docs/memory.md) ---

    MEMORY_KINDS = frozenset({"preference", "company", "outcome",
                              "strategy", "question"})

    async def add_memory(
        self, content: str, kind: str, *,
        source: str = "manual", job_id: int | None = None,
        company: str | None = None,
    ) -> dict:
        """Append a distilled fact to the memory ledger.

        Exact-duplicate active content is touched, not re-inserted, so
        repeated events (same verdict recorded twice) stay one memory.
        """
        if kind not in self.MEMORY_KINDS:
            raise ValueError(f"kind must be one of {sorted(self.MEMORY_KINDS)}")
        if not content.strip():
            raise ValueError("memory content must be non-empty")
        company_norm = _company_normalize(company) if company else None

        if self.pool is None:
            for m in self._memories:
                if m["active"] and m["content"] == content:
                    m["updated_at"] = datetime.now(timezone.utc)
                    return {"id": m["id"], "deduped": True}
            m = {
                "id": self._next_memory_id, "kind": kind, "content": content,
                "source": source, "job_id": job_id, "company": company_norm,
                "active": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            self._next_memory_id += 1
            self._memories.append(m)
            return {"id": m["id"], "deduped": False}

        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM memories WHERE active AND content = $1",
                content,
            )
            if existing:
                await conn.execute(
                    "UPDATE memories SET updated_at = NOW() WHERE id = $1",
                    existing["id"],
                )
                return {"id": existing["id"], "deduped": True}
            row = await conn.fetchrow(
                """
                INSERT INTO memories (kind, content, source, job_id, company)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                kind, content, source, job_id, company_norm,
            )
            return {"id": row["id"], "deduped": False}

    async def deactivate_memory(self, memory_id: int) -> bool:
        """Soft-delete: superseded or wrong memories stay auditable."""
        if self.pool is None:
            for m in self._memories:
                if m["id"] == memory_id and m["active"]:
                    m["active"] = False
                    return True
            return False
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE memories SET active = FALSE, updated_at = NOW() "
                "WHERE id = $1 AND active",
                memory_id,
            )
            return result.endswith("1")

    async def all_memories(self) -> list[dict]:
        """Every active ledger row — the reindex source (docs/memory.md)."""
        if self.pool is None:
            return [dict(m) for m in self._memories if m["active"]]
        async with self.pool.acquire() as conn:
            records = await conn.fetch(
                "SELECT id, kind, content, source, job_id, company "
                "FROM memories WHERE active ORDER BY id"
            )
            return [dict(r) for r in records]

    async def recall_memories(
        self, query: str, *, kind: str | None = None,
        company: str | None = None, limit: int = 5,
    ) -> list[dict]:
        """Deterministic recall: token overlap + company scoping + recency.

        `company` narrows to that company's memories PLUS unscoped ones —
        a preference like "skips crypto" applies everywhere.
        """
        company_norm = _company_normalize(company) if company else None
        if self.pool is None:
            rows = [
                dict(m) for m in self._memories
                if m["active"]
                and (kind is None or m["kind"] == kind)
                and (company_norm is None or m["company"] is None
                     or m["company"] == company_norm)
            ]
        else:
            async with self.pool.acquire() as conn:
                records = await conn.fetch(
                    """
                    SELECT id, kind, content, source, job_id, company,
                           created_at, updated_at
                      FROM memories
                     WHERE active
                       AND ($1::text IS NULL OR kind = $1)
                       AND ($2::text IS NULL OR company IS NULL
                            OR company = $2)
                     ORDER BY updated_at DESC
                     LIMIT 500
                    """,
                    kind, company_norm,
                )
                rows = [dict(r) for r in records]
        return _score_memories(query, rows, limit)

    # --- Discovered companies (auto-promote) ---

    async def record_discovered(
        self, company_display: str, source: str,
    ) -> None:
        """Register a company we saw on a feed but don't have as a target."""
        normalized = _company_normalize(company_display)
        if self.pool is None:
            return  # discovery is a Postgres-only feature for now
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO discovered_companies
                    (company_display, company_normalized, first_seen_source)
                VALUES ($1, $2, $3)
                ON CONFLICT (company_normalized) DO NOTHING
                """,
                company_display, normalized, source,
            )

    async def unprobed_companies(self, limit: int = 20) -> list[dict]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, company_display, company_normalized
                  FROM discovered_companies
                 WHERE probed_at IS NULL AND promoted_at IS NULL
                 ORDER BY first_seen_at DESC
                 LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]

    async def record_probe(
        self, discovered_id: int, probe_results: dict,
        ats_source: str | None = None, ats_slug: str | None = None,
    ) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE discovered_companies
                   SET probed_at = NOW(),
                       probe_results = $2::jsonb,
                       ats_source = $3,
                       ats_slug = $4,
                       promoted_at = CASE WHEN $3 IS NOT NULL
                                          THEN NOW() ELSE NULL END
                 WHERE id = $1
                """,
                discovered_id, __import__("json").dumps(probe_results),
                ats_source, ats_slug,
            )

    async def promoted_companies(self) -> list[dict]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT company_display, company_normalized,
                       ats_source, ats_slug, promoted_at
                  FROM discovered_companies
                 WHERE promoted_at IS NOT NULL
                 ORDER BY promoted_at DESC
                """
            )
            return [dict(r) for r in rows]

    async def sweep_stale(self, days: int) -> int:
        """Mark jobs not seen in `days` as closed. Returns rows affected."""
        if self.pool is None:
            return 0
        async with self.pool.acquire() as conn:
            result = await conn.execute(SWEEP_STALE, days)
            # asyncpg returns "UPDATE N"
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    async def stats(self) -> dict[str, int]:
        if self.pool is None:
            rows = list(self._memory.values())
            return {
                "total": len(rows),
                "passed": sum(1 for r in rows if r.get("prefilter_passed")),
                "qualified": sum(
                    1 for r in rows if r.get("verdict") == "qualified"
                ),
                "active": sum(1 for r in rows if r.get("active", True)),
                "fresh": len(rows),
            }

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE prefilter_passed) AS passed,
                    COUNT(*) FILTER (WHERE verdict = 'qualified') AS qualified,
                    COUNT(*) FILTER (WHERE verdict = 'maybe') AS maybe,
                    COUNT(*) FILTER (WHERE verdict = 'skip') AS skip,
                    COUNT(*) FILTER (WHERE user_verdict = 'applied') AS applied,
                    COUNT(*) FILTER (WHERE user_verdict = 'shortlist') AS shortlisted,
                    COUNT(*) FILTER (WHERE active) AS active,
                    COUNT(*) FILTER (
                        WHERE last_seen_at > NOW() - INTERVAL '24 hours'
                    ) AS fresh
                FROM jobs
            """)
            return dict(row)
