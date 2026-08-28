"""Advisory locks around billed runs (roadmap 0.2).

The scheduler, MCP server, and Slack listener are separate processes on
one database. Without serialization, two concurrent qualify/autopilot
passes pull the same rows and bill twice.
"""
from __future__ import annotations

import pytest

from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage


@pytest.mark.asyncio
async def test_run_lock_blocks_reentry_and_releases():
    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("qualify") as got:
        assert got is True
        async with storage.run_lock("qualify") as second:
            assert second is False          # held -> not acquired
    async with storage.run_lock("qualify") as again:
        assert again is True                # released on exit


@pytest.mark.asyncio
async def test_run_lock_names_are_independent():
    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("qualify") as a:
        async with storage.run_lock("autopilot") as b:
            assert (a, b) == (True, True)


@pytest.mark.asyncio
async def test_run_lock_released_after_exception():
    storage = Storage("")
    await storage.connect()
    with pytest.raises(RuntimeError):
        async with storage.run_lock("qualify"):
            raise RuntimeError("boom")
    async with storage.run_lock("qualify") as got:
        assert got is True                  # not leaked by the raise


@pytest.mark.asyncio
async def test_qualify_pending_skips_when_locked():
    from karani.qualification.runner import qualify_pending

    storage = Storage("")
    await storage.connect()
    resume = ResumeProfile(raw_markdown="# K")

    class ExplodingLLM:
        model_name = "fake"

        async def complete(self, system, user):  # pragma: no cover
            raise AssertionError("must not reach the LLM while locked")

    async with storage.run_lock("qualify"):
        stats = await qualify_pending(storage, ExplodingLLM(), resume,
                                      limit=5)
    assert stats.lock_skipped is True
    assert stats.fetched == 0


@pytest.mark.asyncio
async def test_run_autopilot_skips_when_locked():
    from karani.autopilot.runner import run_autopilot

    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("autopilot"):
        stats = await run_autopilot(storage, slack=None, channel="D1")
    assert stats.lock_skipped is True
    assert stats.drafted == 0
    assert stats.delivered == 0
