"""Build the console: the queue, the forms, the answers, one file.

Everything here is free. No LLM call, no billed work: the expensive
parts (qualification, the pack, the rendered documents) already
happened, and this pass only assembles what exists. The one external
call is the Greenhouse question endpoint, which is opt-in and
degrades.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from karani.ingestion.storage import Storage

from .answers import AnswerSheet, build_answer_sheet
from .console import render_console
from .pack import load_pack
from .questions import introspect_questions

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("data/submission_console.html")

# Internal working files that belong in the artifact store but not on a
# card: nobody attaches a tailoring trace to an application.
_NOT_A_DOCUMENT = ("tailoring_trace", "cover_letter_pack")


@dataclass
class ConsoleResult:
    path: Path
    sheets: list[AnswerSheet] = field(default_factory=list)
    introspected: int = 0
    needs_review: int = 0

    @property
    def outstanding(self) -> int:
        return sum(sheet.outstanding for sheet in self.sheets)


async def _documents(store, artifacts) -> dict[str, str]:
    """Presigned links for this job's rendered documents.

    Links expire (ADR 0014), so they are signed per console build
    rather than stored. No store configured means no links — the sheet
    says so instead of pretending.
    """
    if store is None or not artifacts:
        return {}
    # JSONB arrives as str from asyncpg (no codec is registered) and as
    # dict from the in-memory store; dict() on a string iterates its
    # characters, which is how the first real console run died.
    if isinstance(artifacts, str):
        import json
        try:
            artifacts = json.loads(artifacts)
        except json.JSONDecodeError:
            return {}
    if not isinstance(artifacts, dict):
        return {}
    out: dict[str, str] = {}
    for name, entry in artifacts.items():
        if any(skip in name for skip in _NOT_A_DOCUMENT):
            continue
        # Entries are {"key", "url"}; the stored url is already stale.
        key = entry.get("key") if isinstance(entry, dict) else entry
        if not key:
            continue
        try:
            out[name] = await store.presign(key)
        except Exception as exc:
            log.warning("could not presign %s: %s", key, exc)
    return out


def _resume_markdown(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    try:
        from karani.ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
        return ResumeProfile.from_file(DEFAULT_RESUME_PATH).raw_markdown
    except Exception:
        log.warning("no resume on disk; identity fields will be empty",
                    exc_info=True)
        return ""


async def build_console(
    storage: Storage, *, limit: int = 50,
    output: str | Path | None = None, client=None, refresh: bool = False,
    resume_markdown: str | None = None,
) -> ConsoleResult:
    """Render every pack waiting to be sent into one HTML file."""
    from karani.config import get_config
    from karani.profile import load_profile

    config = get_config()
    profile = load_profile(_resume_markdown(resume_markdown))
    rows = await storage.submission_queue(limit)

    store = None
    if rows:
        try:
            from karani.artifacts import ArtifactStore
            store = await ArtifactStore.create()
        except Exception:
            log.warning("artifact store unavailable; the console will show "
                        "no document links", exc_info=True)

    sheets: list[AnswerSheet] = []
    introspected = 0
    for row in rows:
        questions = await introspect_questions(row, client, refresh=refresh)
        if questions.introspected:
            introspected += 1
            await storage.set_ats_questions(
                int(row.get("id") or 0), questions.model_dump(mode="json"))
        sheets.append(build_answer_sheet(
            job_row=row, questions=questions, profile=profile,
            legal=config.legal, pack=load_pack(row.get("draft_path")),
            documents=await _documents(store, row.get("artifacts")),
            target_comp_usd=config.hunt.target_comp_usd,
        ))

    path = Path(output) if output else DEFAULT_OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_console(sheets), encoding="utf-8")

    return ConsoleResult(
        path=path, sheets=sheets, introspected=introspected,
        needs_review=sum(1 for s in sheets if s.legal_review),
    )


__all__ = ["ConsoleResult", "DEFAULT_OUTPUT", "build_console"]
