"""MemoryManager — the one interface every surface uses to remember/recall.

Modes (env `KARANI_MEMORY`, default `basic`):

- `off`   — recall returns nothing, remember is a no-op. Kill switch.
- `basic` — deterministic: writes go to the `memories` ledger in Postgres
            (or the in-memory fallback); recall is token-overlap + company
            scoping + recency. No LLM, no embeddings, works in tests.
- `mem0`  — writes go to the ledger AND a mem0 semantic index (pgvector in
            the same database, embeddings + extraction via a local Ollama
            model by default — zero token cost). Recall is semantic search,
            falling back to `basic` on any mem0 failure.

Invariant: the `memories` table is the system of record. mem0 is a derived
index — it can be dropped and rebuilt from the ledger at any time, and a
mem0 outage degrades recall quality, never data.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

from ingestion.storage import Storage

log = logging.getLogger(__name__)

DEFAULT_MODE = os.getenv("KARANI_MEMORY", "basic")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "kelyn")


def _mem0_config() -> dict[str, Any]:
    """mem0 OSS config: pgvector in karani's own DB, Ollama for LLM+embeds.

    Every knob is env-overridable; defaults assume the docker-compose stack
    (db on :5433, ollama on :11434).
    """
    dsn = urlparse(os.getenv("DATABASE_URL", ""))
    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": dsn.hostname or "localhost",
                "port": dsn.port or 5433,
                "user": dsn.username or "karani",
                "password": dsn.password or "karani",
                "dbname": (dsn.path or "/karani").lstrip("/"),
                "collection_name": os.getenv("MEM0_COLLECTION", "karani_memories"),
            },
        },
        "llm": {
            "provider": os.getenv("MEM0_LLM_PROVIDER", "ollama"),
            "config": {
                "model": os.getenv("MEM0_LLM_MODEL",
                                   os.getenv("LOCAL_LLM_MODEL", "qwen3:32b")),
            },
        },
        "embedder": {
            "provider": os.getenv("MEM0_EMBEDDER_PROVIDER", "ollama"),
            "config": {
                "model": os.getenv("MEM0_EMBEDDER_MODEL", "nomic-embed-text"),
            },
        },
    }


class _Mem0Backend:
    """Thin async wrapper over the (sync) mem0 OSS client. Import-guarded."""

    def __init__(self) -> None:
        from mem0 import Memory  # optional dep: uv sync --extra memory
        self._client = Memory.from_config(_mem0_config())

    async def add(self, content: str, metadata: dict) -> None:
        await asyncio.to_thread(
            self._client.add, content,
            user_id=MEM0_USER_ID, metadata=metadata,
        )

    async def search(self, query: str, limit: int) -> list[dict]:
        result = await asyncio.to_thread(
            self._client.search, query, user_id=MEM0_USER_ID, limit=limit,
        )
        hits = result.get("results", result) if isinstance(result, dict) else result
        return [
            {"content": h.get("memory", ""), "kind": "mem0",
             "score": h.get("score")}
            for h in hits or []
            if h.get("memory")
        ]


class MemoryManager:
    def __init__(self, storage: Storage, mode: str | None = None):
        self.storage = storage
        self.mode = (mode or DEFAULT_MODE).lower()
        if self.mode not in ("off", "basic", "mem0"):
            raise ValueError("KARANI_MEMORY must be off, basic, or mem0")
        self._mem0: _Mem0Backend | None = None
        if self.mode == "mem0":
            try:
                self._mem0 = _Mem0Backend()
            except Exception as exc:
                # Missing extra, no vector DB, no ollama — degrade, don't die.
                log.warning("mem0 unavailable (%s); falling back to basic", exc)
                self.mode = "basic"

    # --- write paths ---

    async def remember(
        self, content: str, kind: str, *,
        source: str = "manual", job_id: int | None = None,
        company: str | None = None,
    ) -> dict:
        if self.mode == "off":
            return {"id": None, "deduped": False, "mode": "off"}
        result = await self.storage.add_memory(
            content, kind, source=source, job_id=job_id, company=company,
        )
        if self._mem0 is not None and not result.get("deduped"):
            try:
                await self._mem0.add(content, {
                    "kind": kind, "source": source,
                    "job_id": job_id, "company": company,
                })
            except Exception as exc:
                log.warning("mem0 index write failed (%s); ledger has it", exc)
        result["mode"] = self.mode
        return result

    async def remember_verdict(self, job_row: dict, verdict: str) -> dict:
        company = job_row.get("company_display") or job_row.get("company") or ""
        fit = job_row.get("fit_score")
        content = (
            f"User verdict '{verdict}' on {company} — "
            f"{job_row.get('title', '')}"
            + (f" (fit_score={fit})" if fit is not None else "")
        )
        return await self.remember(
            content, "preference", source="verdict",
            job_id=job_row.get("id"), company=company,
        )

    async def remember_outcome(self, job_row: dict, outcome: str) -> dict:
        company = job_row.get("company_display") or job_row.get("company") or ""
        content = (
            f"Application outcome '{outcome}' at {company} for "
            f"{job_row.get('title', '')}"
        )
        return await self.remember(
            content, "outcome", source="outcome",
            job_id=job_row.get("id"), company=company,
        )

    # --- read paths ---

    async def recall(
        self, query: str, *, kind: str | None = None,
        company: str | None = None, limit: int = 5,
    ) -> list[dict]:
        if self.mode == "off":
            return []
        if self._mem0 is not None:
            try:
                hits = await self._mem0.search(query, limit)
                if hits:
                    return hits
            except Exception as exc:
                log.warning("mem0 search failed (%s); using basic recall", exc)
        return await self.storage.recall_memories(
            query, kind=kind, company=company, limit=limit,
        )

    async def recall_for_job(self, job_row: dict, limit: int = 5) -> list[str]:
        """Working context for one qualify/draft decision, as plain strings."""
        company = job_row.get("company_display") or job_row.get("company") or ""
        query = " ".join(filter(None, [
            company,
            job_row.get("title", ""),
            str(job_row.get("role_category") or ""),
            str(job_row.get("seniority") or ""),
        ]))
        rows = await self.recall(query, company=company, limit=limit)
        return [r["content"] for r in rows]
