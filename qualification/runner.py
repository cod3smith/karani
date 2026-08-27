"""Runner: pull pending rows → qualify → write back.

Concurrency-bounded (default 5 parallel calls). Skips rows already qualified
against the current resume hash so re-runs are idempotent unless the resume
changes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from typing import TYPE_CHECKING

from ingestion.resume import ResumeProfile
from ingestion.storage import Storage

if TYPE_CHECKING:  # avoid a runtime import cycle with the memory package
    from memory import MemoryManager

from .agent import qualify_one_agent
from .client import QualifierClient, qualify_one

log = logging.getLogger(__name__)


@dataclass
class QualifyStats:
    fetched: int = 0
    qualified: int = 0     # verdict = "qualified"
    maybe: int = 0
    skipped: int = 0       # verdict = "skip"
    errors: list[str] = field(default_factory=list)


async def _qualify_and_store(
    client: QualifierClient,
    storage: Storage,
    resume: ResumeProfile,
    row: dict,
    stats: QualifyStats,
    sem: asyncio.Semaphore,
    agent_mode: bool = False,
    past_verdicts: list[dict] | None = None,
    memory: "MemoryManager | None" = None,
) -> None:
    async with sem:
        try:
            memories: list[str] | None = None
            if memory is not None:
                memories = await memory.recall_for_job(row)
            if agent_mode:
                result = await qualify_one_agent(
                    client,  # type: ignore[arg-type]
                    resume=resume.raw_markdown,
                    resume_hash=resume.hash,
                    hints=resume.hints,
                    job_row=row,
                    past_verdicts=past_verdicts,
                    memories=memories,
                )
            else:
                result = await qualify_one(
                    client,
                    resume=resume.raw_markdown,
                    resume_hash=resume.hash,
                    hints=resume.hints,
                    job_row=row,
                    past_verdicts=past_verdicts,
                    memories=memories,
                )
            await storage.store_qualification(row["id"], result)
            if result.verdict == "qualified":
                stats.qualified += 1
            elif result.verdict == "maybe":
                stats.maybe += 1
            else:
                stats.skipped += 1
        except Exception as e:
            stats.errors.append(f"job_id={row.get('id')}: {e}")
            log.exception("qualify failed for job_id=%s", row.get("id"))


async def qualify_pending(
    storage: Storage,
    client: QualifierClient,
    resume: ResumeProfile,
    *,
    limit: int = 50,
    concurrency: int = 3,
    agent_mode: bool = False,
    memory: "MemoryManager | None" = None,
) -> QualifyStats:
    """Qualify up to `limit` pending rows. Idempotent per resume hash.

    Concurrency default is 3 — thinking models are slow and hitting them
    at 5–10 in parallel usually just triggers rate limits.

    When `agent_mode=True`, each row goes through the tool-using agent loop
    (multi-turn, evidence-gathering). Much more expensive per row; use only
    on top-tier candidates.
    """
    rows = await storage.pending_qualification(limit=limit, resume_hash=resume.hash)
    stats = QualifyStats(fetched=len(rows))
    if not rows:
        log.info("no pending qualifications")
        return stats

    # Pull recent user reactions as few-shot taste signal.
    past_verdicts = await storage.recent_user_verdicts(limit=30)
    if past_verdicts:
        log.info("using %d past user_verdict pairs for taste calibration",
                 len(past_verdicts))

    mode = "AGENT" if agent_mode else "single-turn"
    log.info("qualifying %d rows [%s] (concurrency=%d, model=%s)",
             len(rows), mode, concurrency, getattr(client, "model_name", "unknown"))
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(
        _qualify_and_store(client, storage, resume, row, stats, sem,
                           agent_mode, past_verdicts, memory)
        for row in rows
    ))
    return stats
