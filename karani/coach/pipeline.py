"""Build one prep pack: assemble -> mine -> compose -> render -> store.

The old prep was a single LLM call handed a resume and a dossier. Most
of what makes a prep pack useful is not generation at all, so this
pipeline spends its effort on assembly:

1. **Refresh the dossier.** The cached company intel (karani/intel/)
   runs a 14-day TTL, which is right for a hunt and wrong for an
   interview — a pack built on three-week-old signal invites a
   question about a product that shipped since. Prep uses a short TTL.
2. **Derive the comp brief** (coach/comp.py). Arithmetic, not opinion.
3. **Mine STAR stories** from the profile core and the career
   transcript, then verify every citation (coach/stories.py).
4. **Compose the pack** from all of it, with the verified stories as
   the only experiences the model may cite.
5. **Render and store** — markdown beside the pack, ATS-safe .docx and
   PDF into the per-job MinIO prefix (ADR 0014/0020).

Every external step is best-effort. No dossier, no memory bank, no
object store and no PDF renderer each cost a section, never the pack.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from karani.ingestion.storage import Storage
from karani.qualification.client import QualifierClient, _extract_json

from .comp import comp_brief, render_comp
from .pack import FAILURE_MARKER, PrepPack, default_path, render_markdown
from .prompts import (PREP_PROMPT_VERSION, STORY_PROMPT_VERSION,
                      prep_system_prompt, prep_user_prompt)
from .stories import mine_stories, render_stories

log = logging.getLogger(__name__)

# A dossier good enough for a hunt is stale for an interview: three
# weeks of company news is exactly what an interviewer expects you to
# have read. Refresh anything older than this.
DOSSIER_TTL_DAYS = 3


@dataclass
class PrepResult:
    pack: PrepPack
    path: Path
    artifacts: dict = field(default_factory=dict)   # {name: {key, url}}
    documents: dict[str, Path] = field(default_factory=dict)


def _file_stem(job_row: dict, candidate: str = "") -> str:
    import re

    def slug(text: str, cap: int = 40) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:cap]

    parts = [slug(candidate, 30),
             slug(job_row.get("company_display") or job_row.get("company")
                  or ""),
             slug(job_row.get("title") or "")]
    return "_".join(p for p in parts if p) or "interview"


def _bind_stories(pack: PrepPack) -> None:
    """A question may only point at a story that survived verification.

    The model sees the stories *after* mining, but it can still name
    one that verification deleted in the same response, or invent a
    title outright. Clearing the citation leaves the question (it is
    still a question they will ask) without implying the candidate has
    an answer they never had.
    """
    titles = {s.title for s in pack.stories}
    for question in pack.likely_questions:
        if question.story_title and question.story_title not in titles:
            log.info("question %r cited unverified story %r — citation "
                     "cleared", question.question[:60], question.story_title)
            question.story_title = ""


async def _question_bank(storage: Storage, company: str) -> list[str]:
    """Questions this company has actually asked before (memory layer)."""
    if not company:
        return []
    try:
        from karani.memory import MemoryManager
        rows = await MemoryManager(storage).recall(
            f"{company} interview questions", kind="question", limit=10)
        return [r["content"] for r in rows]
    except Exception:
        log.warning("question bank unavailable for %s", company,
                    exc_info=True)
        return []


async def _dossier(storage: Storage, company: str) -> tuple[str, bool]:
    if not company:
        return "", False
    try:
        from karani.intel import dossier_text, get_company_intel
        intel = await get_company_intel(storage, company,
                                        ttl_days=DOSSIER_TTL_DAYS)
        return dossier_text(intel), bool(intel.get("cached"))
    except Exception:
        log.warning("company intel unavailable for %s; prepping without a "
                    "dossier", company, exc_info=True)
        return "", False


def _qualification_text(job_row: dict) -> str:
    qualification = job_row.get("qualification")
    if isinstance(qualification, dict):
        return json.dumps(qualification, indent=2, default=str)
    return str(qualification or "")


async def build_prep_pack(
    storage: Storage, *,
    job_row: dict,
    resume_markdown: str,
    stage: str = "interview",
    client: QualifierClient | None = None,
    scheduled_at: datetime | None = None,
    competencies: list[str] | None = None,
    output_path: str | Path | None = None,
) -> PrepResult:
    """`client=None` routes both calls through `[llm.prep]` (ADR 0017)."""
    from karani.candidate import candidate_context
    from karani.profile import load_profile
    from karani.qualification import get_qualifier

    # One client for both calls: the cached prefix is shared between
    # them, and the pack records a single model.
    if client is None:
        client = get_qualifier(task="prep")

    company = job_row.get("company_display") or job_row.get("company") or ""
    profile = load_profile(resume_markdown)
    ctx = candidate_context(resume_markdown)
    candidate = {"resume": resume_markdown, "profile": ctx["profile"],
                 "transcript": ctx["experience"]}

    dossier, cached = await _dossier(storage, company)
    comp = await comp_brief(storage, job_row)
    bank = await _question_bank(storage, company)

    stories, trace = await mine_stories(
        client, job_row=job_row, profile=profile,
        transcript=ctx["experience"], resume=resume_markdown,
        profile_context=ctx["profile"], competencies=competencies)

    pack = PrepPack(
        job_id=int(job_row.get("id") or 0), stage=stage,
        scheduled_at=scheduled_at, stories=stories, story_trace=trace,
        comp=comp, dossier_cached=cached,
        model=getattr(client, "model_name", "unknown"),
        prompt_version=PREP_PROMPT_VERSION,
        story_prompt_version=STORY_PROMPT_VERSION if stories else "",
    )

    from karani.drafting.resume_plan import render_records
    system = prep_system_prompt(records=render_records(profile), **candidate)
    user = prep_user_prompt(
        job_row=job_row, stage=stage,
        qualification=_qualification_text(job_row), dossier=dossier,
        stories=render_stories(stories, profile), comp=render_comp(comp),
        question_bank=bank)
    raw = await client.complete(system, user)

    try:
        composed = PrepPack.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("prep returned malformed JSON: %s -- raw: %r",
                    exc, raw[:400])
        pack.company_brief = (
            f"{FAILURE_MARKER}: the model did not return valid JSON. The "
            f"research below is still good — rerun `karani prep` for the "
            f"composed half.")
    else:
        for name in ("company_brief", "likely_questions", "questions_to_ask",
                     "comp_notes", "warm_openers", "red_flags",
                     "positioning_reminder"):
            setattr(pack, name, getattr(composed, name))
        _bind_stories(pack)

    path = Path(output_path) if output_path else default_path(job_row)
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(pack, job_row, profile)
    path.write_text(markdown, encoding="utf-8")

    result = PrepResult(pack=pack, path=path)
    await _store(result, storage, job_row=job_row, markdown=markdown,
                 candidate_name=profile.name)
    return result


async def _store(result: PrepResult, storage: Storage, *, job_row: dict,
                 markdown: str, candidate_name: str = "") -> None:
    """Documents into the per-job MinIO prefix. Entirely best-effort:
    unconfigured or unreachable object storage leaves the markdown on
    disk, which is where the candidate can still read it."""
    from karani.artifacts import ArtifactStore

    try:
        store = await ArtifactStore.create()
    except Exception:
        log.warning("artifact store unavailable for prep", exc_info=True)
        return
    if store is None:
        return

    files: dict[str, str | bytes] = {"interview_prep.md": markdown}
    stem = _file_stem(job_row, candidate_name)
    try:
        from karani.documents import render_all
        for ext, blob in render_all(
                markdown, title=f"{candidate_name} interview prep").items():
            name = f"{stem}_Interview_Prep.{ext}"
            files[name] = blob
            local = result.path.with_name(name)
            local.write_bytes(blob)
            result.documents[ext] = local
    except Exception:
        log.warning("prep document rendering failed; shipping markdown only",
                    exc_info=True)

    try:
        result.artifacts = await store.store_pack(job_row, files)
    except Exception:
        log.warning("prep artifact upload failed", exc_info=True)
