"""The prep pack: what the model composed, plus what karani derived.

Half of this object never passes through a model. The comp brief is
arithmetic (coach/comp.py), the STAR stories are verified records
(coach/stories.py), and the dossier is cached public signal
(karani/intel/). Keeping the derived half on the same object is what
lets the pack survive a malformed LLM response with its useful parts
intact, instead of being all or nothing.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .comp import CompBrief, render_comp
from .stories import StarStory, render_stories, story_trace_report

FAILURE_MARKER = "PREP FAILED"


class LikelyQuestion(BaseModel):
    question: str
    why: str = ""
    # The STAR story to answer with. Cleared when it names a story that
    # verification deleted — see pipeline._bind_stories.
    story_title: str = ""
    answer_angle: str = ""


class QuestionToAsk(BaseModel):
    question: str
    source_basis: str = ""


class WarmOpener(BaseModel):
    contact: str
    opener: str = ""


class PrepPack(BaseModel):
    # --- composed by the model ---
    company_brief: str = ""
    likely_questions: list[LikelyQuestion] = Field(default_factory=list)
    questions_to_ask: list[QuestionToAsk] = Field(default_factory=list)
    comp_notes: str = ""
    warm_openers: list[WarmOpener] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    positioning_reminder: str = ""

    # --- derived by karani ---
    stories: list[StarStory] = Field(default_factory=list)
    story_trace: list[dict] = Field(default_factory=list)
    comp: CompBrief | None = None

    # --- provenance ---
    job_id: int = 0
    stage: str = "interview"
    scheduled_at: datetime | None = None
    model: str = ""
    prompt_version: str = ""
    story_prompt_version: str = ""
    dossier_cached: bool = False

    @property
    def failed(self) -> bool:
        return self.company_brief.startswith(FAILURE_MARKER)


def _heading(job_row: dict, pack: PrepPack) -> list[str]:
    company = job_row.get("company_display") or job_row.get("company") or ""
    lines = [f"# Interview prep — {company} · {job_row.get('title', '')}", ""]
    when = ""
    if pack.scheduled_at is not None:
        when = f" · scheduled {pack.scheduled_at.date().isoformat()}"
    lines += [
        f"- **Stage:** {pack.stage}{when}",
        f"- **Job id:** {pack.job_id}",
        f"- **Model:** {pack.model} ({pack.prompt_version}"
        + (f" / {pack.story_prompt_version}" if pack.story_prompt_version
           else "") + ")",
        "",
    ]
    return lines


def render_markdown(pack: PrepPack, job_row: dict, profile) -> str:
    """One document, ordered the way it gets read: who they are, what
    they will ask, what you answer with, what you ask, the money."""
    lines = _heading(job_row, pack)

    if pack.company_brief:
        lines += ["## Company brief", "", pack.company_brief, ""]
    if pack.red_flags:
        lines += ["## Walk in knowing", ""]
        lines += [f"- {flag}" for flag in pack.red_flags]
        lines.append("")

    if pack.likely_questions:
        lines += ["## Likely questions", ""]
        for q in pack.likely_questions:
            lines += [f"### {q.question}", ""]
            if q.why:
                lines.append(f"_Why they will ask: {q.why}_")
            if q.story_title:
                lines.append(f"_Answer with: **{q.story_title}**_")
            if q.answer_angle:
                lines += ["", q.answer_angle]
            lines.append("")

    if pack.stories:
        lines += ["## Your stories (verified against your own records)", "",
                  render_stories(pack.stories, profile), ""]

    if pack.questions_to_ask:
        lines += ["## Questions to ask them", ""]
        for q in pack.questions_to_ask:
            lines.append(f"- {q.question}")
            if q.source_basis:
                lines.append(f"  _basis: {q.source_basis}_")
        lines.append("")

    if pack.comp is not None:
        lines += ["## Comp", "", render_comp(pack.comp), ""]
        if pack.comp_notes:
            lines += [pack.comp_notes, ""]

    if pack.warm_openers:
        lines += ["## Warm-path openers", ""]
        lines += [f"- **{w.contact}**: {w.opener}" for w in pack.warm_openers]
        lines.append("")

    if pack.positioning_reminder:
        lines += ["## Positioning reminder", "", pack.positioning_reminder, ""]

    if pack.story_trace:
        lines += [story_trace_report(pack.story_trace,
                                     heading="## STAR provenance")]

    return "\n".join(lines).rstrip() + "\n"


def default_path(job_row: dict, root: str = "drafts"):
    from pathlib import Path

    from karani.drafting.writers import _slug
    jid = job_row.get("id", "?")
    company = _slug(job_row.get("company_display")
                    or job_row.get("company") or "co")
    return Path(root) / f"prep_{jid:0>5}_{company}.md"
