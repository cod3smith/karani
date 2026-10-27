"""The conversion ledger: one durable row per application sent.

Why a separate table rather than more columns on `jobs`:

- A job row is mutable and gets re-upserted on every ingest pass. An
  application is a historical fact — what was sent, on which prompt
  version, with which documents, on a given day. Those must not drift
  when the posting is edited or the qualification is recomputed.
- The attribution question ("which angle converts?") is asked over
  applications, and a table of applications is the natural shape for
  it. `jobs` stays the discovery index.

The table is created with `CREATE TABLE IF NOT EXISTS` and widened with
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` only (CLAUDE.md 4.7). The
production database has real history; nothing here drops or rewrites.

`sync()` reconciles the ledger from the jobs table, so it works without
any other surface being wired up: every job that has an `applied_at`
gets an entry, and fields a human or the watcher has supplied — the
variant tag, the first-response timestamp — are preserved across
reconciliations with COALESCE semantics on both backends.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .models import UNASSIGNED, LedgerEntry

log = logging.getLogger(__name__)

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_ledger (
    job_id BIGINT PRIMARY KEY,
    company TEXT,
    company_display TEXT,
    title TEXT,
    source TEXT,
    fit_score INTEGER,
    comp_min_usd INTEGER,
    comp_max_usd INTEGER,

    -- Which strategy this application was sent under.
    variant TEXT,
    positioning_angle TEXT,
    positioning_text TEXT,

    -- Provenance of the materials.
    qualify_prompt_version TEXT,
    qualify_model TEXT,
    draft_prompt_version TEXT,
    draft_model TEXT,
    documents JSONB DEFAULT '[]'::jsonb,
    gaps JSONB DEFAULT '[]'::jsonb,

    applied_at TIMESTAMPTZ,
    first_response_at TIMESTAMPTZ,
    first_response_kind TEXT,
    outcome TEXT,
    outcome_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS variant TEXT;
ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS positioning_angle TEXT;
ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS positioning_text TEXT;
ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS gaps JSONB
    DEFAULT '[]'::jsonb;
ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS first_response_at
    TIMESTAMPTZ;
ALTER TABLE application_ledger ADD COLUMN IF NOT EXISTS first_response_kind
    TEXT;

CREATE INDEX IF NOT EXISTS application_ledger_variant_idx
    ON application_ledger (variant);
CREATE INDEX IF NOT EXISTS application_ledger_applied_idx
    ON application_ledger (applied_at DESC);
"""

# Outcomes that prove a human read the application. `ghosted` is the
# absence of one, and `withdrew` was our move, not theirs.
RESPONSE_OUTCOMES = frozenset({"offer", "rejection", "declined"})

# Columns `sync` refreshes from the jobs table. Everything else on the
# row was supplied out of band and is left alone.
_SYNCED = (
    "company", "company_display", "title", "source", "fit_score",
    "comp_min_usd", "comp_max_usd", "positioning_text",
    "qualify_prompt_version", "qualify_model", "draft_prompt_version",
    "draft_model", "documents", "gaps", "applied_at", "outcome",
    "outcome_at",
)


def _as_dict(value: Any) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def entry_from_job(row: dict) -> LedgerEntry:
    """Project a job row onto the ledger's shape. Pure, so both the
    Postgres and in-memory paths produce identical entries."""
    qual = _as_dict(row.get("qualification"))
    artifacts = _as_dict(row.get("artifacts"))
    gaps = [g.get("requirement", "") for g in (qual.get("gaps") or [])
            if isinstance(g, dict) and g.get("requirement")]
    return LedgerEntry(
        job_id=int(row["id"]),
        company=row.get("company") or "",
        company_display=row.get("company_display") or "",
        title=row.get("title") or "",
        source=str(row.get("source") or ""),
        fit_score=row.get("fit_score"),
        comp_min_usd=row.get("comp_min_usd"),
        comp_max_usd=row.get("comp_max_usd"),
        positioning_text=qual.get("recommended_positioning") or "",
        qualify_prompt_version=qual.get("prompt_version") or "",
        qualify_model=qual.get("model") or "",
        draft_prompt_version=row.get("draft_prompt_version") or "",
        draft_model=row.get("draft_model") or "",
        documents=sorted(artifacts.keys()),
        gaps=gaps,
        applied_at=_aware(row.get("applied_at")),
        outcome=row.get("outcome"),
        outcome_at=_aware(row.get("outcome_at")),
    )


def derive_first_response(entry: LedgerEntry) -> LedgerEntry:
    """Fill `first_response_at` from the outcome when nothing better is
    known. A rejection IS a response — slower and less welcome than a
    screen, but it proves a human looked. Silence (`ghosted`) is not,
    and neither is withdrawing."""
    if entry.first_response_at or entry.outcome not in RESPONSE_OUTCOMES:
        return entry
    if entry.outcome_at:
        entry.first_response_at = entry.outcome_at
        entry.first_response_kind = entry.outcome or ""
    return entry


class ConversionLedger:
    """Reads and writes `application_ledger` over either Storage backend."""

    def __init__(self, storage) -> None:
        self.storage = storage
        self._schema_ready = False

    # --- backend plumbing ---

    @property
    def _pool(self):
        return getattr(self.storage, "pool", None)

    def _mem(self) -> dict[int, LedgerEntry]:
        """In-memory ledger, hung off the Storage instance so it shares
        the object's lifetime instead of a per-call ledger's."""
        rows = getattr(self.storage, "_analytics_ledger", None)
        if rows is None:
            rows = {}
            self.storage._analytics_ledger = rows
        return rows

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                await conn.execute(LEDGER_SCHEMA)
        self._schema_ready = True

    # --- reads ---

    async def entries(self) -> list[LedgerEntry]:
        await self.ensure_schema()
        if self._pool is None:
            rows = list(self._mem().values())
        else:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(
                    "SELECT * FROM application_ledger ORDER BY applied_at")
            rows = [_entry_from_record(dict(r)) for r in records]
        rows.sort(key=lambda e: (e.applied_at or datetime.min.replace(
            tzinfo=timezone.utc), e.job_id))
        return rows

    async def entry(self, job_id: int) -> LedgerEntry | None:
        await self.ensure_schema()
        if self._pool is None:
            return self._mem().get(int(job_id))
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                "SELECT * FROM application_ledger WHERE job_id = $1", job_id)
        return _entry_from_record(dict(record)) if record else None

    async def arm_counts(self) -> dict[str, dict[str, int]]:
        """`{variant: {applied, responded}}` — the input to the
        promotion rule. Untagged applications land in `unassigned`."""
        counts: dict[str, dict[str, int]] = {}
        for entry in await self.entries():
            bucket = counts.setdefault(entry.variant or UNASSIGNED,
                                       {"applied": 0, "responded": 0})
            bucket["applied"] += 1
            bucket["responded"] += int(entry.responded)
        return counts

    # --- writes ---

    async def sync(self) -> int:
        """Reconcile from the jobs table. Returns the number of NEW
        applications recorded (amended rows do not count — the number
        answers "how many applications appeared since last time")."""
        await self.ensure_schema()
        rows = await self._applied_job_rows()
        existing = {e.job_id: e for e in await self.entries()}
        added = 0
        for row in rows:
            fresh = derive_first_response(entry_from_job(row))
            prior = existing.get(fresh.job_id)
            if prior is None:
                await self._write(fresh)
                added += 1
                continue
            merged = prior.model_copy(update={
                field: getattr(fresh, field) for field in _SYNCED})
            # A reply already on the books outranks anything derived
            # from an outcome timestamp: the screen came before the
            # rejection, and it is the signal we can actually move.
            if prior.first_response_at:
                merged.first_response_at = prior.first_response_at
                merged.first_response_kind = prior.first_response_kind
            else:
                merged = derive_first_response(merged)
            if merged != prior:
                await self._write(merged)
        return added

    async def tag_variant(self, job_id: int, variant: str,
                          *, angle: str | None = None) -> bool:
        """Record which arm this application was sent under.

        Separate from `sync` on purpose: a variant is a fact about how
        the pack was written, which no amount of reading the jobs table
        can recover.
        """
        entry = await self.entry(job_id)
        if entry is None:
            row = await self.storage.get_job(job_id)
            if row is None or not row.get("applied_at"):
                return False
            entry = derive_first_response(entry_from_job(row))
        entry.variant = variant
        if angle is not None:
            entry.positioning_angle = angle
        await self._write(entry)
        return True

    async def record_response(self, job_id: int, *, at: datetime,
                              kind: str = "") -> bool:
        """A human replied. Idempotent on the FIRST response: a later
        call cannot move the timestamp earlier-is-better metric around."""
        entry = await self.entry(job_id)
        if entry is None:
            return False
        at = _aware(at) or at
        if entry.first_response_at and entry.first_response_at <= at:
            return False
        entry.first_response_at = at
        entry.first_response_kind = kind or entry.first_response_kind
        await self._write(entry)
        return True

    # --- internals ---

    async def _applied_job_rows(self) -> list[dict]:
        if self._pool is None:
            return [r for r in self.storage._memory.values()
                    if r.get("applied_at")]
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT id, company, company_display, title, source,
                       fit_score, comp_min_usd, comp_max_usd, qualification,
                       artifacts, draft_prompt_version, draft_model,
                       applied_at, outcome, outcome_at
                  FROM jobs
                 WHERE applied_at IS NOT NULL
                """
            )
        return [dict(r) for r in records]

    async def _write(self, entry: LedgerEntry) -> None:
        if self._pool is None:
            self._mem()[entry.job_id] = entry
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO application_ledger (
                    job_id, company, company_display, title, source,
                    fit_score, comp_min_usd, comp_max_usd,
                    variant, positioning_angle, positioning_text,
                    qualify_prompt_version, qualify_model,
                    draft_prompt_version, draft_model,
                    documents, gaps, applied_at, first_response_at,
                    first_response_kind, outcome, outcome_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16::jsonb, $17::jsonb, $18, $19, $20, $21, $22
                )
                ON CONFLICT (job_id) DO UPDATE SET
                    company = EXCLUDED.company,
                    company_display = EXCLUDED.company_display,
                    title = EXCLUDED.title,
                    source = EXCLUDED.source,
                    fit_score = EXCLUDED.fit_score,
                    comp_min_usd = EXCLUDED.comp_min_usd,
                    comp_max_usd = EXCLUDED.comp_max_usd,
                    variant = COALESCE(EXCLUDED.variant,
                                       application_ledger.variant),
                    positioning_angle = COALESCE(
                        EXCLUDED.positioning_angle,
                        application_ledger.positioning_angle),
                    positioning_text = EXCLUDED.positioning_text,
                    qualify_prompt_version = EXCLUDED.qualify_prompt_version,
                    qualify_model = EXCLUDED.qualify_model,
                    draft_prompt_version = EXCLUDED.draft_prompt_version,
                    draft_model = EXCLUDED.draft_model,
                    documents = EXCLUDED.documents,
                    gaps = EXCLUDED.gaps,
                    applied_at = EXCLUDED.applied_at,
                    first_response_at = LEAST(
                        COALESCE(application_ledger.first_response_at,
                                 EXCLUDED.first_response_at),
                        COALESCE(EXCLUDED.first_response_at,
                                 application_ledger.first_response_at)),
                    first_response_kind = CASE
                        WHEN application_ledger.first_response_at IS NULL
                          OR (EXCLUDED.first_response_at IS NOT NULL
                              AND EXCLUDED.first_response_at
                                  < application_ledger.first_response_at)
                        THEN EXCLUDED.first_response_kind
                        ELSE application_ledger.first_response_kind
                    END,
                    outcome = EXCLUDED.outcome,
                    outcome_at = EXCLUDED.outcome_at,
                    updated_at = NOW()
                """,
                entry.job_id, entry.company, entry.company_display,
                entry.title, entry.source, entry.fit_score,
                entry.comp_min_usd, entry.comp_max_usd,
                entry.variant, entry.positioning_angle, entry.positioning_text,
                entry.qualify_prompt_version, entry.qualify_model,
                entry.draft_prompt_version, entry.draft_model,
                json.dumps(entry.documents), json.dumps(entry.gaps),
                entry.applied_at, entry.first_response_at,
                entry.first_response_kind, entry.outcome, entry.outcome_at,
            )


def _entry_from_record(record: dict) -> LedgerEntry:
    data = dict(record)
    data.pop("created_at", None)
    data.pop("updated_at", None)
    for key in ("documents", "gaps"):
        value = data.get(key)
        if isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = []
        elif value is None:
            data[key] = []
    for key in ("positioning_text", "qualify_prompt_version", "qualify_model",
                "draft_prompt_version", "draft_model", "first_response_kind",
                "company", "company_display", "title", "source"):
        if data.get(key) is None:
            data[key] = ""
    return LedgerEntry.model_validate(data)


__all__ = ["LEDGER_SCHEMA", "RESPONSE_OUTCOMES", "ConversionLedger",
           "derive_first_response", "entry_from_job"]
