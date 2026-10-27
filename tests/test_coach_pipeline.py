"""The prep pipeline: assemble -> mine -> compose -> render -> deliver.

Two LLM calls, both faked. KARANI_INTEL=off in conftest keeps the
dossier probe off the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from karani.coach.pipeline import build_prep_pack
from karani.coach.prompts import PREP_PROMPT_VERSION
from karani.ingestion.storage import Storage

RESUME = """# Kelyn Njeri
kelyn@example.com

## Experience

### DataQRL — Staff Engineer
*2022 - 2025*

- Cut p99 ingest latency from 1.4s to 180ms across 7 pipelines
- Led a team of 4 through a Kafka migration
"""

STORY_JSON = json.dumps({"stories": [
    {"title": "Cutting ingest latency", "competencies": ["performance"],
     "situation": "Seven pipelines missing SLA", "task": "Own p99",
     "action": "Rewrote the batcher", "result": "1.4s to 180ms",
     "source_ids": ["p0b0"]},
    {"title": "Invented heroics", "situation": "x", "task": "x",
     "action": "x", "result": "x", "source_ids": ["p9b9"]},
]})

PREP_JSON = json.dumps({
    "company_brief": "GitLab builds a devops platform and is all-remote.",
    "likely_questions": [
        {"question": "Walk me through a latency problem you owned.",
         "why": "gap: no Rust", "story_title": "Cutting ingest latency",
         "answer_angle": "Lead with the batcher rewrite."},
        {"question": "Tell me about scaling to 10M QPS.",
         "why": "scale", "story_title": "Invented heroics",
         "answer_angle": "x"},
    ],
    "questions_to_ask": [
        {"question": "How did the runner-v2 rollout land?",
         "source_basis": "their engineering blog"}],
    "comp_notes": "Anchor at the top of the posted band.",
    "warm_openers": [{"contact": "alice", "opener": "Your repo overlaps."}],
    "red_flags": ["Recent layoffs"],
    "positioning_reminder": "Lead with data-platform scale.",
})


class SequencedLLM:
    """Scripted in call order: story mining, then the pack."""
    model_name = "fake-sonnet"

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses[min(len(self.calls) - 1,
                                  len(self.responses) - 1)]


JOB = {"id": 42, "company": "gitlab", "company_display": "GitLab",
       "title": "Staff Backend Engineer",
       "description_text": "Distributed systems, Kafka, latency.",
       "location_raw": "Remote", "remote_status": "remote",
       "comp_min_usd": 200_000, "comp_max_usd": 240_000,
       "role_category": "software_engineering", "seniority": "staff",
       "qualification": {"gaps": [{"requirement": "Rust"}]},
       "active": True}


def _storage() -> Storage:
    s = Storage("")
    s._memory[("greenhouse", "1")] = dict(JOB)
    return s


@pytest.mark.asyncio
async def test_pack_assembles_every_section(tmp_path):
    client = SequencedLLM([STORY_JSON, PREP_JSON])
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME, stage="screen",
        client=client, output_path=tmp_path / "prep.md")

    pack = result.pack
    assert client.calls and len(client.calls) == 2
    assert pack.prompt_version == PREP_PROMPT_VERSION
    assert pack.model == "fake-sonnet"
    assert pack.stage == "screen"
    assert pack.company_brief.startswith("GitLab builds")
    assert pack.comp is not None and pack.comp.posted_max_usd == 240_000
    assert pack.comp_notes
    assert [s.title for s in pack.stories] == ["Cutting ingest latency"]


@pytest.mark.asyncio
async def test_a_question_cannot_point_at_an_unverified_story(tmp_path):
    """The model cited a story that verification deleted. The question
    survives; the citation does not, because answering it from a story
    that was never real is the whole failure mode."""
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, PREP_JSON]),
        output_path=tmp_path / "prep.md")
    by_q = {q.question: q for q in result.pack.likely_questions}
    assert by_q["Walk me through a latency problem you owned."].story_title \
        == "Cutting ingest latency"
    assert by_q["Tell me about scaling to 10M QPS."].story_title == ""


@pytest.mark.asyncio
async def test_the_markdown_file_is_written_and_readable(tmp_path):
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, PREP_JSON]),
        output_path=tmp_path / "prep.md")
    text = result.path.read_text()
    assert "Cutting ingest latency" in text
    assert "1.4s to 180ms" in text            # the sourced record
    assert "$200,000" in text                 # derived comp, verbatim
    assert "runner-v2" in text
    assert "STAR provenance" in text or "p0b0" in text


@pytest.mark.asyncio
async def test_comp_research_reaches_the_model_as_resolved_facts(tmp_path):
    client = SequencedLLM([STORY_JSON, PREP_JSON])
    await build_prep_pack(_storage(), job_row=JOB, resume_markdown=RESUME,
                          client=client, output_path=tmp_path / "prep.md")
    _, prep_user = client.calls[1]
    assert "$200,000" in prep_user
    assert "comp_research" in prep_user
    # The verified stories are the only experiences the pack may cite.
    assert "Cutting ingest latency" in prep_user
    assert "Invented heroics" not in prep_user


@pytest.mark.asyncio
async def test_malformed_pack_output_degrades_without_raising(tmp_path):
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, "sorry, no"]),
        output_path=tmp_path / "prep.md")
    assert result.pack.failed
    # The derived half survives a model failure — it never needed one.
    assert result.pack.comp is not None
    assert result.pack.stories


@pytest.mark.asyncio
async def test_scheduled_date_rides_along(tmp_path):
    when = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME, stage="interview",
        scheduled_at=when, client=SequencedLLM([STORY_JSON, PREP_JSON]),
        output_path=tmp_path / "prep.md")
    assert result.pack.scheduled_at == when
    assert "2026-11-03" in result.path.read_text()


# --- delivery: documents into the per-job object-store prefix ---

def _resolved(value):
    import asyncio
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    future.set_result(value)
    return future


class StubS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, bucket, key, data, length, content_type=None):
        self.objects[key] = data.read()

    def presigned_get_object(self, bucket, key, expires=None):
        return f"https://minio.local/{bucket}/{key}?sig=x"


@pytest.mark.asyncio
async def test_documents_land_beside_the_pack_in_object_storage(
        tmp_path, monkeypatch):
    """An ATS-safe .docx and a PDF, in the same per-job prefix the
    application pack uses. The renderer is stubbed — it has its own
    tests, and this one is about the wiring."""
    from karani.artifacts import ArtifactStore

    store = ArtifactStore(StubS3(), "karani-applications")
    monkeypatch.setattr(ArtifactStore, "create",
                        classmethod(lambda cls: _resolved(store)))
    monkeypatch.setattr("karani.documents.render_all",
                        lambda md, title="": {"docx": b"DOCX", "pdf": b"PDF"})

    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, PREP_JSON]),
        output_path=tmp_path / "prep.md")

    names = set(result.artifacts)
    assert "interview_prep.md" in names
    assert any(n.endswith("_Interview_Prep.docx") for n in names)
    assert any(n.endswith("_Interview_Prep.pdf") for n in names)
    assert all(meta["key"].startswith("00042-gitlab/")
               for meta in result.artifacts.values())
    # And on disk, for the candidate who just wants to open the file.
    assert result.documents["docx"].read_bytes() == b"DOCX"


@pytest.mark.asyncio
async def test_a_dead_object_store_never_costs_the_pack(tmp_path, monkeypatch):
    from karani.artifacts import ArtifactStore

    async def boom(cls):
        raise RuntimeError("minio is down")

    monkeypatch.setattr(ArtifactStore, "create", classmethod(boom))
    result = await build_prep_pack(
        _storage(), job_row=JOB, resume_markdown=RESUME,
        client=SequencedLLM([STORY_JSON, PREP_JSON]),
        output_path=tmp_path / "prep.md")
    assert result.artifacts == {}
    assert result.path.exists()
