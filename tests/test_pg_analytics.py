"""Real-Postgres coverage for the analytics SQL.

Everything else in the analytics suite runs on `Storage("")`, which
means the `application_ledger` and `strategy_variants` statements —
the additive DDL, the upsert's COALESCE on the variant tag, and the
LEAST/CASE that keeps the EARLIEST reply as the first response — have
no coverage there. These run against a throwaway database on the
compose Postgres: `make test-pg` / `pytest -m pg`. They skip, never
fail, when it is unreachable.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from karani.analytics.ledger import ConversionLedger
from karani.analytics.models import StrategyVariant
from karani.analytics.registry import VariantRegistry
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage
from karani.qualification.models import QualGap, QualificationResult

pytestmark = pytest.mark.pg

ADMIN_URL = os.getenv(
    "KARANI_TEST_PG_URL",
    "postgresql://karani:karani@localhost:5433/postgres")

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def pg():
    try:
        import asyncpg
        admin = await asyncio.wait_for(asyncpg.connect(ADMIN_URL), timeout=5)
    except Exception as exc:                      # pragma: no cover
        pytest.skip(f"postgres unreachable at {ADMIN_URL}: {exc}")

    dbname = f"karani_test_{uuid.uuid4().hex[:10]}"
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    storage = Storage(ADMIN_URL.rsplit("/", 1)[0] + f"/{dbname}")
    await storage.connect()
    assert storage.pool is not None, "fell back to memory — check the DSN"
    try:
        yield storage
    finally:
        await storage.close()
        await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
        await admin.close()


def _job(source_id: str) -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title="Staff Backend Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. "
                          "Salary: $200,000 - $240,000. Same pay "
                          "regardless of location."),
        apply_url=f"https://example.com/apply/{source_id}",
        posted_at=NOW - timedelta(days=3),
    ).finalize()


async def _applied(pg: Storage, source_id: str) -> int:
    job = _job(source_id)
    job_id = (await pg.upsert(job, pre_filter(job, DEFAULT_PROFILE)))["id"]
    await pg.store_qualification(job_id, QualificationResult(
        fit_score=88, verdict="qualified",
        gaps=[QualGap(requirement="Kubernetes at scale")],
        recommended_positioning="Lead with the platform work.",
        model="kimi-k2", prompt_version="qual-v5"))
    await pg.record_draft(job_id, "/tmp/pack.md", prompt_version="draft-v5",
                          model="opus-4", keyword_coverage=0.8)
    await pg.set_artifacts(job_id, {"Resume.docx": "jobs/x/Resume.docx"})
    await pg.set_application_status(job_id, "applied")
    return job_id


@pytest.mark.asyncio
async def test_schema_is_idempotent_and_additive(pg):
    """Re-running the DDL must be a no-op, the way SCHEMA is."""
    ledger = ConversionLedger(pg)
    registry = VariantRegistry(pg)
    for _ in range(3):
        ledger._schema_ready = False
        registry._schema_ready = False
        await ledger.ensure_schema()
        await registry.ensure_schema()
    async with pg.pool.acquire() as conn:
        tables = {r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            " WHERE table_schema = 'public'")}
    assert {"application_ledger", "strategy_variants"} <= tables


@pytest.mark.asyncio
async def test_sync_round_trips_provenance_through_sql(pg):
    job_id = await _applied(pg, "1")
    ledger = ConversionLedger(pg)
    await ledger.ensure_schema()

    assert await ledger.sync() == 1
    assert await ledger.sync() == 0, "second pass must find nothing new"

    entry = await ledger.entry(job_id)
    assert entry.qualify_model == "kimi-k2"
    assert entry.qualify_prompt_version == "qual-v5"
    assert entry.draft_prompt_version == "draft-v5"
    assert entry.documents == ["Resume.docx"]
    assert entry.gaps == ["Kubernetes at scale"]
    assert entry.applied_at is not None


@pytest.mark.asyncio
async def test_the_upsert_preserves_a_variant_tag_across_syncs(pg):
    job_id = await _applied(pg, "1")
    ledger = ConversionLedger(pg)
    await ledger.ensure_schema()
    await ledger.sync()

    await ledger.tag_variant(job_id, "outcome-first", angle="outcome-first")
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.variant == "outcome-first"
    assert entry.positioning_angle == "outcome-first"
    assert (await ledger.arm_counts())["outcome-first"]["applied"] == 1


@pytest.mark.asyncio
async def test_the_earliest_reply_wins_in_sql(pg):
    """The screen, not the rejection that follows it, is first contact."""
    job_id = await _applied(pg, "1")
    ledger = ConversionLedger(pg)
    await ledger.ensure_schema()
    await ledger.sync()
    applied_at = (await ledger.entry(job_id)).applied_at

    await ledger.record_response(job_id, at=applied_at + timedelta(days=3),
                                 kind="screen")
    # A later call cannot push the timestamp forward.
    assert await ledger.record_response(
        job_id, at=applied_at + timedelta(days=20), kind="rejection") is False

    await pg.set_outcome(job_id, "rejection")
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.outcome == "rejection"
    assert entry.first_response_kind == "screen"
    assert entry.days_to_first_response == 3.0


@pytest.mark.asyncio
async def test_variant_registry_round_trips_through_sql(pg):
    registry = VariantRegistry(pg)
    await registry.ensure_schema()
    await registry.register(StrategyVariant(
        name="outcome-first", dimension="positioning",
        description="Lead with outcomes", guidance="Open on the result."))
    await registry.register(StrategyVariant(name="systems-depth"))

    assert [v.name for v in await registry.active()] == ["outcome-first",
                                                         "systems-depth"]
    assert (await registry.assign(2)).name in {"outcome-first",
                                               "systems-depth"}
    await registry.deactivate("systems-depth")
    assert [v.name for v in await registry.active()] == ["outcome-first"]
    assert len(await registry.all()) == 2
