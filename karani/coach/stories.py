"""STAR stories mined from the candidate's own records, with provenance.

The tailorer established the pattern (drafting/resume_plan.py): the
model does not return prose, it returns records citing the ids they
came from, and karani verifies the citations before anything ships.
The same discipline applies here, and the stakes are higher — a
tailored bullet that overstates gets skimmed, a fabricated STAR story
gets probed for ten minutes by someone who does this for a living.

Two provenance kinds, both checkable without a model:

- `source_ids` index into the profile core (`p0`, `p0b1`), exactly the
  ids `resume_plan.index_profile` already mints. An id that does not
  exist is dropped.
- `transcript_quote` must be a VERBATIM span of the career transcript.
  Whitespace and case are normalized; nothing else is. A paraphrase
  fails, which is the point — the model cannot launder an invention
  through a plausible-sounding quote.

A story left with neither after verification is discarded, not
repaired. Every drop is recorded in the trace that ships with the pack.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from karani.profile.model import Profile
from karani.qualification.client import QualifierClient, _extract_json

from .prompts import (STORY_PROMPT_VERSION, story_system_prompt,
                      story_user_prompt)

log = logging.getLogger(__name__)

__all__ = ["StarStory", "StoryBank", "mine_stories", "render_stories",
           "story_trace_report", "verify_stories", "STORY_PROMPT_VERSION"]

# Short quotes match by accident. A span this small is not evidence.
MIN_QUOTE_CHARS = 20


class StarStory(BaseModel):
    title: str
    competencies: list[str] = Field(default_factory=list)
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    source_ids: list[str] = Field(default_factory=list)
    transcript_quote: str = ""


class StoryBank(BaseModel):
    stories: list[StarStory] = Field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def verify_stories(
    stories: list[StarStory], *, profile: Profile, transcript: str = "",
) -> tuple[list[StarStory], list[dict]]:
    """Keep only what traces back to real material. Returns (kept, trace)."""
    from karani.drafting.resume_plan import index_profile

    index = index_profile(profile)
    haystack = _normalize(transcript)
    kept: list[StarStory] = []
    trace: list[dict] = []

    for story in stories:
        known = [i for i in story.source_ids if i in index]
        unknown = [i for i in story.source_ids if i not in index]
        quote = story.transcript_quote.strip()
        quote_ok = bool(
            quote and len(quote) >= MIN_QUOTE_CHARS and haystack
            and _normalize(quote) in haystack
        )
        entry: dict = {"title": story.title, "source_ids": known,
                       "dropped_ids": unknown}
        if quote and not quote_ok:
            entry["dropped_quote"] = quote
        if not known and not quote_ok:
            entry["status"] = "dropped: no verifiable provenance"
            log.warning("story %r cited nothing real (%s) — dropped",
                        story.title, unknown or "no ids")
            trace.append(entry)
            continue
        entry["status"] = "verified"
        trace.append(entry)
        kept.append(story.model_copy(update={
            "source_ids": known,
            "transcript_quote": quote if quote_ok else "",
        }))
    return kept, trace


def source_text(profile: Profile, source_id: str) -> str:
    """The cited record, named by the role it sits in — the candidate
    needs to know which job a bullet came from, not just its text."""
    from karani.drafting.resume_plan import index_profile

    index = index_profile(profile)
    record = index.get(source_id)
    if record is None:
        return ""
    if not isinstance(record, str):
        return f"{record.company} — {record.title or 'role'}"
    position = index.get(source_id.split("b")[0])
    where = (f"{position.company} — " if position is not None
             and hasattr(position, "company") else "")
    return f"{where}{record}"


def render_stories(stories: list[StarStory], profile: Profile) -> str:
    """Pack-ready and prompt-ready: the story plus what it rests on."""
    out: list[str] = []
    for story in stories:
        out.append(f"### {story.title}")
        if story.competencies:
            out.append(f"_Proves: {', '.join(story.competencies)}_")
        for label, value in (("Situation", story.situation),
                             ("Task", story.task),
                             ("Action", story.action),
                             ("Result", story.result)):
            if value:
                out.append(f"- **{label}:** {value}")
        evidence = [source_text(profile, i) for i in story.source_ids]
        evidence = [e for e in evidence if e]
        if evidence:
            out.append("- **From your record:** "
                       + " · ".join(f"[{i}] {t}" for i, t
                                    in zip(story.source_ids, evidence)))
        if story.transcript_quote:
            out.append(f"- **In your words:** \"{story.transcript_quote}\"")
        out.append("")
    return "\n".join(out).strip()


def story_trace_report(trace: list[dict]) -> str:
    lines = ["# STAR provenance", "",
             "Every story and the record it was mined from. Dropped "
             "entries are stories the model could not source.", ""]
    for entry in trace:
        lines.append(f"- {entry.get('title', '?')}: {entry.get('status')}"
                     + (f" · ids {entry['source_ids']}"
                        if entry.get("source_ids") else ""))
        if entry.get("dropped_ids"):
            lines.append(f"    unknown ids dropped: {entry['dropped_ids']}")
        if entry.get("dropped_quote"):
            lines.append("    quote not found in the transcript: "
                         f"{entry['dropped_quote'][:120]!r}")
    return "\n".join(lines) + "\n"


async def mine_stories(
    client: QualifierClient, *,
    job_row: dict,
    profile: Profile,
    transcript: str = "",
    resume: str = "",
    profile_context: str = "",
    competencies: list[str] | None = None,
) -> tuple[list[StarStory], list[dict]]:
    """One LLM call, then verification. Never raises on bad output."""
    from karani.drafting.resume_plan import render_records

    system = story_system_prompt(resume=resume, profile=profile_context,
                                 transcript=transcript,
                                 records=render_records(profile))
    raw = await client.complete(system, story_user_prompt(job_row,
                                                          competencies))
    try:
        bank = StoryBank.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        # A prep pack with no stories is thin; one with invented stories
        # is harmful. Degrade to thin (CLAUDE.md section 5).
        log.warning("story mining returned malformed JSON: %s -- raw: %r",
                    exc, raw[:400])
        return [], []
    return verify_stories(bank.stories, profile=profile,
                          transcript=transcript)
