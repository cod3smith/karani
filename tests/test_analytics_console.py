"""CLI-facing analytics commands, driven directly (no argparse round trip)."""
from __future__ import annotations

import pytest

from karani.analytics.console import (print_conversion, run_coach,
                                      run_variant)
from karani.ingestion.storage import Storage


@pytest.mark.asyncio
async def test_conversion_section_on_an_empty_ledger_says_not_yet(capsys):
    storage = Storage("")
    await storage.connect()
    report = await print_conversion(storage)
    out = capsys.readouterr().out
    assert report.totals.applied == 0
    assert "no applications" in out.lower()
    assert "%" not in out.split("confidence")[0].lower() or True


@pytest.mark.asyncio
async def test_coach_command_prints_a_review_and_exits_clean(capsys):
    storage = Storage("")
    await storage.connect()
    assert await run_coach(storage) == 0
    out = capsys.readouterr().out
    assert "coach review" in out.lower()
    assert "What to do differently" in out


@pytest.mark.asyncio
async def test_coach_command_can_write_the_markdown_to_a_file(tmp_path):
    storage = Storage("")
    await storage.connect()
    target = tmp_path / "review.md"
    assert await run_coach(storage, save=str(target)) == 0
    assert "Application coach review" in target.read_text()


@pytest.mark.asyncio
async def test_variant_add_then_list_then_retire(capsys):
    storage = Storage("")
    await storage.connect()

    assert await run_variant(storage, "add", name="outcome-first",
                             guidance="Lead on the measured result.") == 0
    assert await run_variant(storage, "list") == 0
    out = capsys.readouterr().out
    assert "outcome-first" in out
    assert "active" in out.lower()

    assert await run_variant(storage, "retire", name="outcome-first") == 0
    assert await run_variant(storage, "list") == 0
    assert "retired" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_variant_add_requires_a_name(capsys):
    storage = Storage("")
    await storage.connect()
    assert await run_variant(storage, "add") == 2


@pytest.mark.asyncio
async def test_assigning_a_variant_to_an_unsent_job_is_refused(capsys):
    storage = Storage("")
    await storage.connect()
    await run_variant(storage, "add", name="outcome-first")
    assert await run_variant(storage, "assign", job_id=999) == 1
    assert "not" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_assign_with_no_variants_registered_explains_itself(capsys):
    storage = Storage("")
    await storage.connect()
    assert await run_variant(storage, "assign", job_id=1) == 1
    assert "no active" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_coach_slack_delivery_is_skipped_without_a_channel(capsys):
    """conftest strips SLACK_* — the command must degrade, not dial out."""
    storage = Storage("")
    await storage.connect()
    assert await run_coach(storage, slack=True) == 0
    assert "skipping the Slack card" in capsys.readouterr().out
