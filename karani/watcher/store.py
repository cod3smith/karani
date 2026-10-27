"""The ledger of messages the watcher has already handled.

Without it a pass is not idempotent: run `karani watch` twice over the
same fortnight and every rejection advances the machine again, every
reply gets re-drafted, and the classifier is billed twice for the same
mail. With it, `seen()` is the first question the runner asks.

It is also where tomorrow's thread continuity comes from — a bare
`Re: next steps` with no company name in it finds its application
because yesterday's message in that thread did.

Two deliberate choices. The table carries no message **body**: it holds
ids, the label and the match, so the ledger stays small and a mailbox's
contents do not get copied into the job database. And its DDL lives
here rather than in `ingestion/storage.py` — `CREATE TABLE IF NOT
EXISTS` is additive and idempotent (CLAUDE.md 4.7), applied on first
use, which keeps a new subsystem out of a file three other workstreams
are editing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from karani.ingestion.storage import Storage

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS email_messages (
    gmail_id TEXT PRIMARY KEY,
    thread_id TEXT,
    from_address TEXT,
    from_domain TEXT,
    subject TEXT,
    received_at TIMESTAMPTZ,
    label TEXT,
    confidence REAL,
    job_id BIGINT,
    matched_by TEXT,
    match_confidence TEXT,
    status_after TEXT,
    reply_drafted BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS email_messages_thread_idx
    ON email_messages (thread_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS email_messages_job_idx
    ON email_messages (job_id);
"""


class WatcherStore:
    """Thin ledger over the same connection pool the rest of karani uses.

    In-memory mode piggybacks on the `Storage` instance so the fallback
    behaves like the real thing within a process (ADR 0006).
    """

    def __init__(self, storage: Storage):
        self._storage = storage
        self._ensured = False

    # --- in-memory fallback state, attached to the Storage instance ---

    @property
    def _rows(self) -> dict[str, dict[str, Any]]:
        rows = getattr(self._storage, "_watcher_messages", None)
        if rows is None:
            rows = {}
            self._storage._watcher_messages = rows
        return rows

    async def ensure_schema(self) -> None:
        if self._ensured or self._storage.pool is None:
            self._ensured = True
            return
        async with self._storage.pool.acquire() as conn:
            await conn.execute(SCHEMA)
        self._ensured = True

    async def seen(self, gmail_id: str) -> bool:
        await self.ensure_schema()
        if self._storage.pool is None:
            return gmail_id in self._rows
        async with self._storage.pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT 1 FROM email_messages WHERE gmail_id = $1", gmail_id))

    async def record(
        self, gmail_id: str, *, thread_id: str = "",
        from_address: str = "", from_domain: str = "", subject: str = "",
        received_at: datetime | None = None, label: str = "unclassified",
        confidence: float = 0.0, job_id: int | None = None,
        matched_by: str = "none", match_confidence: str = "none",
        status_after: str = "", reply_drafted: bool = False,
    ) -> None:
        await self.ensure_schema()
        row = {
            "gmail_id": gmail_id, "thread_id": thread_id,
            "from_address": from_address, "from_domain": from_domain,
            "subject": subject, "received_at": received_at, "label": label,
            "confidence": confidence, "job_id": job_id,
            "matched_by": matched_by, "match_confidence": match_confidence,
            "status_after": status_after, "reply_drafted": reply_drafted,
            "processed_at": datetime.now(timezone.utc),
        }
        if self._storage.pool is None:
            self._rows[gmail_id] = row
            return
        async with self._storage.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO email_messages (
                    gmail_id, thread_id, from_address, from_domain, subject,
                    received_at, label, confidence, job_id, matched_by,
                    match_confidence, status_after, reply_drafted
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (gmail_id) DO UPDATE SET
                    label = EXCLUDED.label,
                    job_id = COALESCE(EXCLUDED.job_id, email_messages.job_id),
                    status_after = EXCLUDED.status_after,
                    processed_at = NOW()
                """,
                gmail_id, thread_id, from_address, from_domain, subject,
                received_at, label, confidence, job_id, matched_by,
                match_confidence, status_after, reply_drafted,
            )

    async def thread_index(self) -> dict[str, int]:
        """thread_id -> job_id for every thread already tied to a job."""
        await self.ensure_schema()
        if self._storage.pool is None:
            return {r["thread_id"]: r["job_id"] for r in self._rows.values()
                    if r.get("thread_id") and r.get("job_id")}
        async with self._storage.pool.acquire() as conn:
            records = await conn.fetch(
                "SELECT DISTINCT ON (thread_id) thread_id, job_id "
                "  FROM email_messages "
                " WHERE job_id IS NOT NULL AND thread_id IS NOT NULL "
                " ORDER BY thread_id, processed_at DESC")
        return {r["thread_id"]: r["job_id"] for r in records}

    async def all_rows(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        if self._storage.pool is None:
            return list(self._rows.values())
        async with self._storage.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(
                "SELECT * FROM email_messages ORDER BY processed_at DESC")]
