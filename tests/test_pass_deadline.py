"""A pass must not be able to run forever (ADR 0025).

karani was down for 40 hours with the scheduler healthy and the job
"running": one hourly pass blocked on a localhost Ollama request that
never returned — Ollama had auto-updated mid-request and the client
had no timeout. launchd will not start a second instance while the
first is alive, so a single wedged call silenced the whole pipeline.
The watchdog correctly alerted every hour and could do nothing about
it, because reporting is all it can do.

Nothing bounded the pass. That is the defect: the specific hanging
call matters less than the absence of a ceiling on any of them.
"""
from __future__ import annotations

import asyncio

import pytest

from karani.orchestration.graph import run_with_deadline


@pytest.mark.asyncio
async def test_a_wedged_pass_is_abandoned_at_the_deadline():
    async def never_returns():
        await asyncio.sleep(3600)

    with pytest.raises(TimeoutError):
        await run_with_deadline(never_returns(), seconds=0.05)


@pytest.mark.asyncio
async def test_a_normal_pass_is_untouched():
    async def quick():
        return {"ingest": {"fetched": 1}}

    assert await run_with_deadline(quick(), seconds=5) == {
        "ingest": {"fetched": 1}}


@pytest.mark.asyncio
async def test_the_deadline_leaves_the_hour_free():
    """It must fire before the next tick, or the scheduler still
    skips a pass — a 60-minute ceiling on an hourly job is useless."""
    from karani.orchestration.graph import pass_deadline_seconds
    assert pass_deadline_seconds() < 3600


def test_the_deadline_is_configurable(monkeypatch):
    from karani.orchestration.graph import pass_deadline_seconds
    monkeypatch.setenv("KARANI_PASS_DEADLINE_SECONDS", "120")
    assert pass_deadline_seconds() == 120


def test_the_hourly_command_reports_a_timeout_as_failure(monkeypatch):
    """Exit non-zero so the launchd record shows it, rather than a
    silent success that hides a lost hour."""
    import karani.cli as cli
    import karani.orchestration.graph as graph

    async def wedged():
        await asyncio.sleep(3600)

    # _hourly drives its own event loop, so this test stays sync.
    monkeypatch.setattr(graph, "run_hunt_once", wedged)
    monkeypatch.setenv("KARANI_PASS_DEADLINE_SECONDS", "0.05")
    assert cli._hourly(None) == 1
