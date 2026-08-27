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
    """mem0 OSS config: pgvector + Ollama, every knob env-overridable.

    The vector store reads MEM0_PG_URL, defaulting to the docker-compose
    Postgres — NOT DATABASE_URL. The ledger (system of record) can live
    in Neon while the derived vector index stays local and disposable;
    pointing them at the same database also works.
    """
    dsn = urlparse(os.getenv(
        "MEM0_PG_URL",
        os.getenv("DATABASE_URL", "postgresql://karani:karani@localhost:5433/karani"),
    ))
    ollama_url = os.getenv("MEM0_OLLAMA_URL", "http://localhost:11434")
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
                # Must match the embedder's output dims (nomic-embed-text
                # = 768); mem0's default assumes OpenAI's 1536.
                "embedding_model_dims": int(os.getenv("MEM0_EMBED_DIMS", "768")),
            },
        },
        "llm": {
            "provider": os.getenv("MEM0_LLM_PROVIDER", "ollama"),
            "config": {
                # Memory extraction is a small-model task; no need for the
                # big qualification model here.
                "model": os.getenv("MEM0_LLM_MODEL", "llama3.2:3b"),
                "ollama_base_url": ollama_url,
            },
        },
        "embedder": {
            "provider": os.getenv("MEM0_EMBEDDER_PROVIDER", "ollama"),
            "config": {
                "model": os.getenv("MEM0_EMBEDDER_MODEL", "nomic-embed-text"),
                "ollama_base_url": ollama_url,
                "embedding_dims": int(os.getenv("MEM0_EMBED_DIMS", "768")),
            },
        },
    }


class _Mem0Backend:
    """Thin async wrapper over the (sync) mem0 OSS client. Import-guarded."""

    def __init__(self) -> None:
        from mem0 import Memory  # optional dep: uv sync --extra memory
        self._client = Memory.from_config(_mem0_config())

    async def add(self, content: str, metadata: dict) -> None:
        # infer=False: the ledger already holds deliberate, distilled
        # facts (ADR 0009) — store them verbatim instead of letting
        # mem0's extraction LLM rephrase (or drop) them.
        def _add():
            try:
                self._client.add(content, user_id=MEM0_USER_ID,
                                 metadata=metadata, infer=False)
            except TypeError:  # older mem0 without infer kwarg
                self._client.add(content, user_id=MEM0_USER_ID,
                                 metadata=metadata)
        await asyncio.to_thread(_add)

    async def search(self, query: str, limit: int) -> list[dict]:
        def _search():
            try:
                # mem0 >= 1.0 API: filters + top_k
                return self._client.search(
                    query, top_k=limit,
                    filters={"user_id": MEM0_USER_ID},
                )
            except (TypeError, ValueError):
                # legacy API: top-level user_id + limit
                return self._client.search(
                    query, user_id=MEM0_USER_ID, limit=limit,
                )
        result = await asyncio.to_thread(_search)
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

    async def remember_question(
        self, job_row: dict, question: str, stage: str = "",
    ) -> dict:
        """A question actually asked in an interview — the question bank.

        Company-scoped `question` memories compound: every future prep
        pack for this company recalls them (roadmap 1.5.6).
        """
        company = job_row.get("company_display") or job_row.get("company") or ""
        where = f" ({stage})" if stage else ""
        content = f"Interview question at {company}{where}: {question}"
        return await self.remember(
            content, "question", source="stage",
            job_id=job_row.get("id"), company=company,
        )

    async def reindex(self) -> dict:
        """Rebuild the derived mem0 index from the ledger.

        The recipe docs/memory.md promises: the ledger is truth, the
        vector index is disposable — this is the restore.
        """
        if self._mem0 is None:
            return {"indexed": 0, "failed": 0, "mode": self.mode,
                    "note": "mem0 not active (KARANI_MEMORY, extra, infra)"}
        rows = await self.storage.all_memories()
        indexed = failed = 0
        for m in rows:
            try:
                await self._mem0.add(m["content"], {
                    "kind": m.get("kind"), "source": m.get("source"),
                    "job_id": m.get("job_id"), "company": m.get("company"),
                })
                indexed += 1
            except Exception as exc:
                failed += 1
                log.warning("reindex failed for memory %s: %s",
                            m.get("id"), exc)
        return {"indexed": indexed, "failed": failed, "mode": self.mode}

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
