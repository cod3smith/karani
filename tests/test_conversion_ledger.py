"""Conversion ledger: one row per application, outcome attached later.

Runs entirely on the in-memory `Storage("")` substrate — no Postgres,
no network. The SQL path has its own coverage in `tests/test_pg_*`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.analytics.ledger import ConversionLedger
from karani.analytics.registry import VariantRegistry
from karani.analytics.models import StrategyVariant
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage
from karani.qualification.models import QualGap, QualificationResult

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def _job(source_id: str, *, company: str = "gitlab",
         display: str = "GitLab") -> Job:
    return Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company=company, company_display=display,
        title="Staff Backend Engineer",
        location_raw="Remote",
        remote_status=RemoteStatus.REMOTE,
        description_text=(
            "We hire globally. Python and Kubernetes required. "
            "Salary: $200,000 - $240,000. Same pay regardless of location."
        ),
        apply_url=f"https://example.com/apply/{source_id}",
        posted_at=NOW - timedelta(days=3),
    ).finalize()


async def _applied_job(storage: Storage, source_id: str, **overrides) -> int:
    """A job carried all the way to `applied`, with draft provenance."""
    job = _job(source_id, **{k: v for k, v in overrides.items()
                             if k in {"company", "display"}})
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    job_id = result["id"]
    await storage.store_qualification(job_id, QualificationResult(
        fit_score=88, verdict="qualified",
        gaps=[QualGap(requirement="Kubernetes at scale", severity="medium")],
        recommended_positioning="Lead with the causal-ML platform work.",
        model="claude-sonnet-4", prompt_version="qual-v5",
    ))
    await storage.record_draft(job_id, "/tmp/pack.md",
                               prompt_version="draft-v5",
                               model="claude-opus-4", keyword_coverage=0.82)
    await storage.set_artifacts(job_id, {
        "Resume.docx": "jobs/1/Resume.docx",
        "Cover_Letter.pdf": "jobs/1/Cover_Letter.pdf",
    })
    await storage.set_application_status(job_id, "applied")
    row = await storage.get_job(job_id)
    row["applied_at"] = overrides.get("applied_at", NOW)
    for key, value in overrides.items():
        if key not in {"company", "display", "applied_at"}:
            row[key] = value
    return job_id


async def _ledger(storage: Storage) -> ConversionLedger:
    ledger = ConversionLedger(storage)
    await ledger.ensure_schema()
    return ledger


# --- the empty case, which is the case today ---

@pytest.mark.asyncio
async def test_empty_ledger_is_a_valid_answer():
    storage = Storage("")
    await storage.connect()
    ledger = await _ledger(storage)

    assert await ledger.sync() == 0
    assert await ledger.entries() == []
    assert await ledger.entry(1) is None


@pytest.mark.asyncio
async def test_a_drafted_but_unsent_application_is_not_in_the_ledger():
    """The ledger measures applications, not intentions."""
    storage = Storage("")
    await storage.connect()
    job = _job("1")
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    await storage.record_draft(result["id"], "/tmp/pack.md",
                               prompt_version="draft-v5", model="m")
    ledger = await _ledger(storage)

    assert await ledger.sync() == 0
    assert await ledger.entries() == []


# --- provenance capture ---

@pytest.mark.asyncio
async def test_sync_captures_the_provenance_of_a_sent_application():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    ledger = await _ledger(storage)

    assert await ledger.sync() == 1
    entry = await ledger.entry(job_id)
    assert entry is not None
    assert entry.company_display == "GitLab"
    assert entry.source == "greenhouse"
    assert entry.fit_score == 88
    assert entry.qualify_prompt_version == "qual-v5"
    assert entry.qualify_model == "claude-sonnet-4"
    assert entry.draft_prompt_version == "draft-v5"
    assert entry.draft_model == "claude-opus-4"
    assert sorted(entry.documents) == ["Cover_Letter.pdf", "Resume.docx"]
    assert entry.gaps == ["Kubernetes at scale"]
    assert "causal-ML platform" in entry.positioning_text
    assert entry.applied_at == NOW
    # Nothing has come back yet, and the ledger says so rather than
    # guessing.
    assert entry.first_response_at is None
    assert entry.responded is False
    assert entry.days_to_first_response is None


@pytest.mark.asyncio
async def test_sync_is_idempotent():
    storage = Storage("")
    await storage.connect()
    await _applied_job(storage, "1")
    ledger = await _ledger(storage)

    assert await ledger.sync() == 1
    assert await ledger.sync() == 0
    assert len(await ledger.entries()) == 1


# --- outcomes arriving later ---

@pytest.mark.asyncio
async def test_a_recorded_reply_sets_time_to_first_response():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    ledger = await _ledger(storage)
    await ledger.sync()

    await ledger.record_response(job_id, at=NOW + timedelta(days=5, hours=12),
                                 kind="screen")
    entry = await ledger.entry(job_id)
    assert entry.responded is True
    assert entry.first_response_kind == "screen"
    assert entry.days_to_first_response == 5.5


@pytest.mark.asyncio
async def test_a_later_sync_never_overwrites_a_recorded_reply():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    ledger = await _ledger(storage)
    await ledger.sync()
    await ledger.record_response(job_id, at=NOW + timedelta(days=2),
                                 kind="screen")

    await storage.set_outcome(job_id, "rejection")
    (await storage.get_job(job_id))["outcome_at"] = NOW + timedelta(days=30)
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.outcome == "rejection"
    assert entry.days_to_first_response == 2.0, \
        "the screen, not the eventual rejection, is the first response"


@pytest.mark.asyncio
async def test_a_rejection_with_no_recorded_reply_is_itself_the_response():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    row = await storage.get_job(job_id)
    await storage.set_outcome(job_id, "rejection")
    row["outcome_at"] = NOW + timedelta(days=9)
    ledger = await _ledger(storage)
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.responded is True
    assert entry.first_response_kind == "rejection"
    assert entry.days_to_first_response == 9.0


@pytest.mark.asyncio
async def test_a_ghosted_application_is_not_a_response():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    row = await storage.get_job(job_id)
    await storage.set_outcome(job_id, "ghosted")
    row["outcome_at"] = NOW + timedelta(days=45)
    ledger = await _ledger(storage)
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.responded is False
    assert entry.days_to_first_response is None


# --- variant tagging ---

@pytest.mark.asyncio
async def test_tagging_an_application_with_a_variant_survives_sync():
    storage = Storage("")
    await storage.connect()
    job_id = await _applied_job(storage, "1")
    ledger = await _ledger(storage)
    await ledger.sync()

    await ledger.tag_variant(job_id, "outcome-first",
                             angle="outcome-first")
    await ledger.sync()

    entry = await ledger.entry(job_id)
    assert entry.variant == "outcome-first"
    assert entry.positioning_angle == "outcome-first"


@pytest.mark.asyncio
async def test_untagged_applications_report_as_unassigned():
    from karani.analytics.models import UNASSIGNED
    storage = Storage("")
    await storage.connect()
    await _applied_job(storage, "1")
    ledger = await _ledger(storage)
    await ledger.sync()

    counts = await ledger.arm_counts()
    assert counts == {UNASSIGNED: {"applied": 1, "responded": 0}}


# --- the variant registry ---

@pytest.mark.asyncio
async def test_registry_round_trips_a_variant():
    storage = Storage("")
    await storage.connect()
    registry = VariantRegistry(storage)
    await registry.ensure_schema()

    await registry.register(StrategyVariant(
        name="outcome-first", dimension="positioning",
        description="Lead with business outcomes",
        guidance="Open on the measured result, not the stack."))
    variants = await registry.active()
    assert [v.name for v in variants] == ["outcome-first"]
    assert variants[0].guidance.startswith("Open on the measured result")

    await registry.deactivate("outcome-first")
    assert await registry.active() == []
    assert len(await registry.all()) == 1


@pytest.mark.asyncio
async def test_assignment_is_deterministic_and_balanced():
    storage = Storage("")
    await storage.connect()
    registry = VariantRegistry(storage)
    await registry.ensure_schema()
    for name in ("outcome-first", "systems-depth"):
        await registry.register(StrategyVariant(name=name))

    picks = [(await registry.assign(job_id)).name for job_id in range(1, 41)]
    assert picks == [(await registry.assign(job_id)).name
                     for job_id in range(1, 41)], "must be reproducible"
    assert set(picks) == {"outcome-first", "systems-depth"}
    assert abs(picks.count("outcome-first") - picks.count("systems-depth")) <= 2


@pytest.mark.asyncio
async def test_assignment_with_no_active_variants_returns_nothing():
    storage = Storage("")
    await storage.connect()
    registry = VariantRegistry(storage)
    await registry.ensure_schema()
    assert await registry.assign(1) is None


@pytest.mark.asyncio
async def test_a_single_active_variant_is_assigned_to_everything():
    """One arm is a default, not a comparison. Assignment still answers;
    refusing to call it a winner is the promotion report's job."""
    storage = Storage("")
    await storage.connect()
    registry = VariantRegistry(storage)
    await registry.ensure_schema()
    await registry.register(StrategyVariant(name="outcome-first"))
    assert (await registry.assign(1)).name == "outcome-first"
    assert (await registry.assign(2)).name == "outcome-first"
