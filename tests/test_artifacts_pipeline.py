"""Artifact store (stubbed S3) and the shared application-pack pipeline."""
from __future__ import annotations

import json

import pytest

from artifacts import ArtifactStore
from drafting.pipeline import build_application_pack
from ingestion.filters import pre_filter
from ingestion.models import Job, RemoteStatus, Source
from ingestion.profile import DEFAULT_PROFILE
from ingestion.storage import Storage


# --- artifact store ---

class StubS3:
    def __init__(self, fail_on: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_on = fail_on

    def put_object(self, bucket, key, data, length, content_type=None):
        if self.fail_on and self.fail_on in key:
            raise RuntimeError("upload boom")
        self.objects[key] = data.read()

    def presigned_get_object(self, bucket, key, expires=None):
        return f"https://minio.local/{bucket}/{key}?sig=x"


JOB_ROW = {"id": 7, "company_display": "GitLab", "title": "Senior BE"}


def test_key_layout():
    assert ArtifactStore.key(JOB_ROW, "resume.md") == "00007-gitlab/resume.md"


def test_unconfigured_returns_none(monkeypatch):
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    import asyncio
    assert asyncio.get_event_loop  # silence lint
    assert not ArtifactStore.configured()


@pytest.mark.asyncio
async def test_store_pack_uploads_and_presigns():
    store = ArtifactStore(StubS3(), "karani-applications")
    out = await store.store_pack(JOB_ROW, {
        "resume.md": "# Tailored resume",
        "cover_letter_pack.md": "Dear team",
        "empty.md": "",  # skipped
    })
    assert set(out) == {"resume.md", "cover_letter_pack.md"}
    assert out["resume.md"]["key"] == "00007-gitlab/resume.md"
    assert out["resume.md"]["url"].startswith("https://minio.local/")


@pytest.mark.asyncio
async def test_store_pack_partial_failure_is_contained():
    store = ArtifactStore(StubS3(fail_on="resume"), "b")
    out = await store.store_pack(JOB_ROW, {
        "resume.md": "# R", "cover_letter_pack.md": "Dear",
    })
    assert "resume.md" not in out           # failed upload dropped
    assert "cover_letter_pack.md" in out    # the rest still landed


# --- pipeline ---

DRAFT_JSON = json.dumps({
    "cover_letter": "I am excited to apply. I leverage cutting-edge tools "
                    "with a proven track record. Moreover, Python and Kafka.",
    "tone_note": "", "tailored_bullets": [], "application_answers": [],
    "subject_line": "", "positioning_summary": "",
})
HUMANIZE_JSON = json.dumps({
    "cover_letter": "Eight years of Python and Kafka platforms. Your "
                    "integrations role sits in that lane.",
    "application_answers": [],
})
RESUME_JSON = json.dumps({
    "resume_markdown": "# Kelyn\nPython, Kafka platforms.",
    "changes_summary": "- reordered",
})


class SequencedLLM:
    """Returns scripted responses in call order: draft, humanize, tailor."""
    model_name = "fake"

    def __init__(self):
        self.responses = [DRAFT_JSON, HUMANIZE_JSON, RESUME_JSON]
        self.calls = 0

    async def complete(self, system, user):
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return r


async def _seed(storage: Storage) -> dict:
    job = Job(
        source=Source.GREENHOUSE, source_id="1",
        company="gitlab", company_display="GitLab",
        title="Senior Backend Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python and Kafka required. "
                          "Salary: $180,000 - $220,000. "
                          "Same pay regardless of location."),
        apply_url="https://example.com/1",
    ).finalize()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    return await storage.get_job(result["id"])


@pytest.mark.asyncio
async def test_pipeline_full_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    stub = ArtifactStore(StubS3(), "karani-applications")

    async def fake_create():
        return stub

    import drafting.pipeline as pipeline_mod
    import artifacts
    monkeypatch.setattr(artifacts.ArtifactStore, "create",
                        staticmethod(fake_create))

    storage = Storage("")
    await storage.connect()
    row = await _seed(storage)
    client = SequencedLLM()

    pack = await build_application_pack(
        client, storage, job_row=row, resume_markdown="# Kelyn\nPython.",
    )
    assert not pack.failed
    assert client.calls == 3  # draft + humanize + tailor
    # Humanized letter shipped (higher voice score than the AI draft).
    assert pack.pkg.cover_letter.startswith("Eight years")
    assert pack.voice["kept"] == "rewrite"
    assert pack.voice["after"]["score"] > pack.voice["before"]["score"]
    # Draft file re-rendered with the human voice.
    assert "Eight years" in pack.draft_path.read_text()
    # Full tailored resume written.
    assert pack.resume_path is not None and pack.resume_path.exists()
    # Artifacts uploaded with presigned urls.
    assert set(pack.artifacts) == {"cover_letter_pack.md", "resume.md"}
    assert pack.artifacts["resume.md"]["url"].startswith("https://")
    # Provenance recorded.
    updated = await storage.get_job(row["id"])
    assert updated["application_status"] == "drafting"
    assert updated["artifacts"]["resume.md"].endswith("/resume.md")
    _ = pipeline_mod  # keep import for monkeypatch scope clarity


@pytest.mark.asyncio
async def test_pipeline_flags_disable_extras(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KARANI_HUMANIZE", "off")
    monkeypatch.setenv("KARANI_TAILOR_RESUME", "off")
    storage = Storage("")
    await storage.connect()
    row = await _seed(storage)
    client = SequencedLLM()
    pack = await build_application_pack(
        client, storage, job_row=row, resume_markdown="# K",
    )
    assert client.calls == 1  # draft only
    assert pack.voice["note"] == "humanizer disabled"
    assert pack.resume is None


@pytest.mark.asyncio
async def test_pipeline_failed_draft_records_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class GarbageLLM:
        model_name = "fake"

        async def complete(self, system, user):
            return "not json at all"

    storage = Storage("")
    await storage.connect()
    row = await _seed(storage)
    pack = await build_application_pack(
        GarbageLLM(), storage, job_row=row, resume_markdown="# K",
    )
    assert pack.failed
    assert (await storage.get_job(row["id"])).get("application_status") is None
