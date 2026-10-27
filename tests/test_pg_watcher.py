"""Real-Postgres coverage for the watcher ledger's SQL.

Everything else runs on `Storage("")`, which hides the statements that
actually ship: the idempotent DDL, the ON CONFLICT upsert, and the
DISTINCT ON thread index. `make test-pg` / `pytest -m pg`.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from karani.ingestion.storage import Storage
from karani.watcher.store import WatcherStore

pytestmark = pytest.mark.pg

ADMIN_URL = os.getenv(
    "KARANI_TEST_PG_URL",
    "postgresql://karani:karani@localhost:5433/postgres")


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


@pytest.mark.asyncio
async def test_schema_is_idempotent(pg):
    store = WatcherStore(pg)
    await store.ensure_schema()
    await WatcherStore(pg).ensure_schema()      # second connect, same DDL
    assert await store.seen("nothing") is False


@pytest.mark.asyncio
async def test_record_is_an_upsert_not_a_duplicate_key_error(pg):
    store = WatcherStore(pg)
    await store.record("m1", thread_id="t1", label="interview_invite",
                       job_id=7, match_confidence="high")
    await store.record("m1", thread_id="t1", label="rejection", job_id=7,
                       status_after="rejected")
    rows = await store.all_rows()
    assert len(rows) == 1
    assert rows[0]["label"] == "rejection"
    assert rows[0]["status_after"] == "rejected"


@pytest.mark.asyncio
async def test_thread_index_keeps_the_latest_match_per_thread(pg):
    store = WatcherStore(pg)
    await store.record("m1", thread_id="t1", label="interview_invite",
                       job_id=7)
    await store.record("m2", thread_id="t1", label="scheduling", job_id=7)
    await store.record("m3", thread_id="t2", label="irrelevant")
    assert await store.thread_index() == {"t1": 7}
