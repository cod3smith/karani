"""Orchestrator: fetch → pre-filter → store → sweep."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import httpx

from .config import settings
from .filters import pre_filter
from .models import Job, Source
from .profile import DEFAULT_PROFILE, UserProfile
from . import FETCHERS
from .storage import Storage
from .targets import FEED_SOURCES, TARGETS

log = logging.getLogger(__name__)


@dataclass
class SourceOutcome:
    fetched: int = 0
    errors: int = 0
    error_msgs: list[str] = field(default_factory=list)


@dataclass
class RunStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    passed_prefilter: int = 0
    dropped_by_reason: Counter = field(default_factory=Counter)
    per_source: dict[str, SourceOutcome] = field(default_factory=dict)
    stale_closed: int = 0
    errors: list[str] = field(default_factory=list)


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    source: Source,
    slug: str | None,
    outcomes: dict[str, SourceOutcome],
) -> list[Job]:
    key = f"{source.value}/{slug or '-'}"
    outcomes.setdefault(key, SourceOutcome())
    async with sem:
        fetcher = FETCHERS[source]
        try:
            jobs = await fetcher.fetch(client, slug)
            outcomes[key].fetched = len(jobs)
            log.info("fetched %s: %d jobs", key, len(jobs))
            return jobs
        except httpx.HTTPStatusError as e:
            msg = f"http {e.response.status_code}"
            outcomes[key].errors = 1
            outcomes[key].error_msgs.append(msg)
            log.warning("%s failed: %s", key, msg)
        except Exception as e:
            outcomes[key].errors = 1
            outcomes[key].error_msgs.append(str(e))
            log.exception("%s failed", key)
        return []


async def _upsert_one(
    storage: Storage, job: Job, stats: RunStats, profile: UserProfile,
) -> None:
    try:
        pf = pre_filter(job, profile)
        result = await storage.upsert(job, pf)
        if result["inserted"]:
            stats.inserted += 1
        else:
            stats.updated += 1
        if pf.pass_hard_filters:
            stats.passed_prefilter += 1
        else:
            for reason in pf.reasons_failed:
                # Bucket by the first colon-delimited prefix for tidy stats.
                bucket = reason.split(":", 1)[0].strip()
                stats.dropped_by_reason[bucket] += 1
    except Exception as e:
        stats.errors.append(f"{job.source.value}/{job.source_id}: {e}")
        log.exception("upsert failed")


async def run(
    storage: Storage, profile: UserProfile | None = None,
) -> RunStats:
    profile = profile or DEFAULT_PROFILE
    stats = RunStats()
    outcomes: dict[str, SourceOutcome] = {}
    sem = asyncio.Semaphore(settings.http_concurrency)

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
    ) as client:
        tasks: list[asyncio.Task[list[Job]]] = []
        for t in TARGETS:
            tasks.append(asyncio.create_task(
                _fetch_one(client, sem, t.source, t.slug, outcomes)
            ))
        for src in FEED_SOURCES:
            tasks.append(asyncio.create_task(
                _fetch_one(client, sem, src, None, outcomes)
            ))
        results = await asyncio.gather(*tasks, return_exceptions=False)

    all_jobs = [j for batch in results for j in batch]

    # Deduplicate within this run — same canonical_hash across sources.
    # Keep the first observed (which will typically be the ATS entry since we
    # queue those first).
    by_canonical: dict[str, Job] = {}
    duplicates_in_run = 0
    for job in all_jobs:
        if job.canonical_hash and job.canonical_hash in by_canonical:
            duplicates_in_run += 1
            continue
        by_canonical[job.canonical_hash] = job

    stats.fetched = len(all_jobs)
    stats.per_source = outcomes
    if duplicates_in_run:
        log.info("suppressed %d cross-source duplicates", duplicates_in_run)

    # Bounded concurrency for the upsert phase — respects the pool max_size.
    upsert_sem = asyncio.Semaphore(5)

    async def _bounded_upsert(j: Job) -> None:
        async with upsert_sem:
            await _upsert_one(storage, j, stats, profile)

    await asyncio.gather(
        *(_bounded_upsert(j) for j in by_canonical.values()),
        return_exceptions=False,
    )

    # Close jobs that haven't been observed for a while.
    stats.stale_closed = await storage.sweep_stale(settings.stale_job_days)

    # Register companies we saw on feed sources but don't already track. The
    # `discover` CLI will probe them for ATS presence and promote hits.
    ats_companies = {
        (j.company or "").lower()
        for j in by_canonical.values()
        if j.source in (Source.GREENHOUSE, Source.LEVER, Source.ASHBY, Source.WORKABLE)
    }
    for j in by_canonical.values():
        if j.source in FEED_SOURCES and j.company_display:
            try:
                await storage.record_discovered(j.company_display, j.source.value)
            except Exception:  # pragma: no cover - best-effort
                pass
    _ = ats_companies  # reserved for future dedup

    return stats
