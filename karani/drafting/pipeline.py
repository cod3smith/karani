"""The full application-pack pipeline — one implementation, every surface.

    draft (letter + bullets + answers)
      -> humanize (scrub AI voice; keep original if it scores worse)
      -> tailor full resume (anti-tell rules baked into its prompt)
      -> upload artifacts to MinIO (best-effort; presigned links)
      -> record provenance on the job row

CLI `draft`, MCP `draft`, and autopilot all call `build_application_pack`
so the flow can never drift between surfaces. Knobs: KARANI_HUMANIZE
(on|off, default on) and KARANI_TAILOR_RESUME (on|off, default on) —
each toggles one LLM call per pack.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from karani.ingestion.storage import Storage
from karani.qualification.client import QualifierClient

from .humanize import humanize_package, package_text, voice_report
from .models import DraftPackage
from .resume_tailor import TailoredResume, tailor_resume
from .runner import draft_for_job
from .writers import write_markdown

log = logging.getLogger(__name__)


def _flag(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).lower() not in ("off", "0", "false")


@dataclass
class ApplicationPack:
    pkg: DraftPackage
    draft_path: Path
    resume: TailoredResume | None = None
    resume_path: Path | None = None
    voice: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)  # {name: {key, url}}

    @property
    def failed(self) -> bool:
        return self.pkg.cover_letter.startswith("DRAFTING FAILED")


async def build_application_pack(
    client: QualifierClient | None,
    storage: Storage,
    *,
    job_row: dict,
    resume_markdown: str,
    output_path: str | Path | None = None,
) -> ApplicationPack:
    """Draft -> humanize -> tailor resume -> store. Records provenance.

    `client=None` routes each stage through `[llm.<task>]` config
    (draft/humanize/tailor may use different providers); an explicit
    client is used for every stage (tests, provider overrides).
    """
    from karani.qualification import get_qualifier

    def for_task(task: str) -> QualifierClient:
        return client if client is not None else get_qualifier(task=task)

    job_id = int(job_row.get("id") or 0)

    pkg, path = await draft_for_job(
        for_task("draft"), resume=resume_markdown, job_row=job_row,
        qualification=job_row.get("qualification"),
        output_path=output_path,
    )
    pack = ApplicationPack(pkg=pkg, draft_path=path)
    if pack.failed:
        return pack  # caller decides; never persist or deliver a failure

    # Humanize the prose; the detector arbitrates which version ships.
    if _flag("KARANI_HUMANIZE"):
        pack.pkg, pack.voice = await humanize_package(
            for_task("humanize"), pkg, voice_sample=resume_markdown,
        )
        if pack.voice.get("kept") == "rewrite":
            path.write_text(  # rewrite the draft file with the human voice
                _rerender(pack.pkg, job_row), encoding="utf-8")
    else:
        report = voice_report(package_text(pkg))
        pack.voice = {"before": report, "after": report, "kept": "original",
                      "note": "humanizer disabled"}

    # Full tailored resume for this role.
    if _flag("KARANI_TAILOR_RESUME"):
        try:
            pack.resume, pack.resume_path = await tailor_resume(
                for_task("tailor"), resume=resume_markdown, job_row=job_row,
                qualification=job_row.get("qualification"),
            )
        except Exception as exc:
            log.warning("resume tailoring failed for job %s: %s",
                        job_id, exc)

    # Object storage (best-effort).
    from karani.artifacts import ArtifactStore
    store = await ArtifactStore.create()
    if store is not None:
        files = {"cover_letter_pack.md": pack.draft_path.read_text()}
        if pack.resume and pack.resume.resume_markdown:
            files["resume.md"] = pack.resume.resume_markdown
        pack.artifacts = await store.store_pack(job_row, files)

    await storage.record_draft(
        job_id, str(pack.draft_path),
        prompt_version=pack.pkg.prompt_version,
        model=pack.pkg.model,
        keyword_coverage=pack.pkg.keyword_coverage,
    )
    if pack.artifacts:
        await storage.set_artifacts(job_id, {
            name: meta["key"] for name, meta in pack.artifacts.items()
        })
    return pack


def _rerender(pkg: DraftPackage, job_row: dict) -> str:
    from .writers import render_markdown
    return render_markdown(pkg, job_row)


__all__ = ["ApplicationPack", "build_application_pack", "write_markdown"]
