"""The state ladder and the dedup ledger.

The ladder only ever climbs: a scheduling note arriving after an onsite
must not drag an application back to `screen`. The ledger is what makes
a pass idempotent — re-reading the same inbox must not re-advance
anything.
"""
from __future__ import annotations

import pytest

from karani.ingestion.storage import Storage
from karani.watcher.state import TERMINAL, next_status
from karani.watcher.store import WatcherStore


# --- the ladder ---

def test_rejection_and_offer_are_recorded_from_any_live_state():
    assert next_status("rejection", "applied") == "rejected"
    assert next_status("rejection", "interview") == "rejected"
    assert next_status("offer", "screen") == "offer"
    assert "rejected" in TERMINAL


def test_an_invite_opens_the_screen_stage():
    assert next_status("interview_invite", "applied") == "screen"
    assert next_status("interview_invite", "ready") == "screen"


def test_a_second_invite_moves_on_to_interview():
    assert next_status("interview_invite", "screen") == "interview"


def test_the_ladder_never_climbs_down():
    assert next_status("interview_invite", "offer") is None
    assert next_status("assessment", "interview") is None
    assert next_status("interview_invite", "interview") is None


def test_an_assessment_counts_as_a_screen():
    assert next_status("assessment", "applied") == "screen"


def test_labels_that_say_nothing_about_state_change_nothing():
    for label in ("scheduling", "recruiter_outreach", "irrelevant",
                  "unclassified"):
        assert next_status(label, "applied") is None


def test_a_terminal_application_is_left_alone():
    """A rejected application that gets a scheduling mail is a human
    problem, not a state transition."""
    assert next_status("interview_invite", "rejected") is None
    assert next_status("scheduling", "rejected") is None


def test_every_status_the_ladder_returns_is_one_storage_accepts():
    produced = {next_status(label, status)
                for label in ("rejection", "offer", "interview_invite",
                              "assessment")
                for status in ("ready", "applied", "screen", "interview")}
    assert {s for s in produced if s} <= Storage.APPLICATION_STATUSES


# --- the ledger ---

@pytest.mark.asyncio
async def test_a_message_is_processed_once():
    storage = Storage("")
    await storage.connect()
    store = WatcherStore(storage)
    await store.ensure_schema()

    assert await store.seen("m1") is False
    await store.record("m1", thread_id="t1", label="rejection", job_id=7,
                       matched_by="ats+company_name", status_after="rejected")
    assert await store.seen("m1") is True


@pytest.mark.asyncio
async def test_recording_the_same_message_twice_keeps_one_row():
    storage = Storage("")
    await storage.connect()
    store = WatcherStore(storage)
    await store.record("m1", thread_id="t1", label="rejection", job_id=7)
    await store.record("m1", thread_id="t1", label="rejection", job_id=7)
    assert len(await store.all_rows()) == 1


@pytest.mark.asyncio
async def test_the_thread_index_comes_back_from_the_ledger():
    """Yesterday's match is how today's bare `Re:` reply finds its job."""
    storage = Storage("")
    await storage.connect()
    store = WatcherStore(storage)
    await store.record("m1", thread_id="t1", label="interview_invite",
                       job_id=7)
    await store.record("m2", thread_id="t2", label="irrelevant", job_id=None)
    assert await store.thread_index() == {"t1": 7}


def test_the_schema_is_additive_only():
    """Kelyn's database has history in it (CLAUDE.md 4.7)."""
    from karani.watcher import store as store_mod

    sql = store_mod.SCHEMA.upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql
    for destructive in ("DROP ", "TRUNCATE", "DELETE FROM", "ALTER COLUMN"):
        assert destructive not in sql
