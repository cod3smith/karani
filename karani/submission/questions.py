"""What this job's application form actually asks.

Greenhouse, Lever and Ashby publish the application form alongside the
posting, on the same public endpoints karani already ingests from. That
is the difference between a generic field sheet and one that answers
*this* form: a Greenhouse board returns every question with its exact
label, type and allowed values, including the custom ones a company
wrote itself.

Coverage is uneven and the degradation is deliberate:

- **Greenhouse** exposes the full question set on the per-job endpoint
  (`?questions=true`). One extra GET per job, through `get_with_retry`
  like every other fetch (rule 4.5).
- **Lever** exposes custom question cards on the posting payload karani
  already stored in `jobs.raw` — no fetch at all when they are there.
- **Ashby** sometimes carries an `applicationFormDefinition` in the same
  stored payload; usually it does not.

When nothing is exposed the set degrades to the fields every ATS asks
anyway. An unanswerable form is still a form the candidate can fill —
the sheet just stops claiming it knows the exact wording.

Network is opt-in: no client, or `KARANI_ATS_QUESTIONS=off`, means
nothing is fetched and the stored payload is all that is read.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import BaseModel

log = logging.getLogger(__name__)

GREENHOUSE_JOB_URL = ("https://boards-api.greenhouse.io/v1/boards/"
                      "{slug}/jobs/{job_id}?questions=true")


class Choice(BaseModel, frozen=True):
    label: str
    value: str = ""


class Question(BaseModel, frozen=True):
    """One field on the form. `key` is the ATS's own field name where it
    has one, so an answer can be matched back to the field it fills."""
    key: str
    label: str
    kind: str = "text"      # text|textarea|select|multiselect|file|
    #                         email|phone|url|date|number|boolean
    required: bool = False
    choices: tuple[Choice, ...] = ()


class QuestionSet(BaseModel, frozen=True):
    ats: str = ""
    introspected: bool = False   # False == the standard fallback set
    note: str = ""
    questions: tuple[Question, ...] = ()


YES_NO = (Choice(label="Yes", value="yes"), Choice(label="No", value="no"))


def _q(key: str, label: str, kind: str = "text", required: bool = False,
       choices: tuple[Choice, ...] = ()) -> Question:
    return Question(key=key, label=label, kind=kind, required=required,
                    choices=choices)


# The fields every ATS asks, in the order forms ask them. Not a guess at
# a specific company's form — a floor, so the sheet is never empty.
_COMMON: tuple[Question, ...] = (
    _q("first_name", "First name", required=True),
    _q("last_name", "Last name", required=True),
    _q("email", "Email", "email", required=True),
    _q("phone", "Phone", "phone"),
    _q("location", "Current location"),
    _q("resume", "Resume / CV", "file", required=True),
    _q("cover_letter", "Cover letter", "file"),
    _q("linkedin", "LinkedIn profile", "url"),
    _q("website", "Website / portfolio", "url"),
    _q("work_authorization",
       "Are you legally authorized to work in the country of this role?",
       "select", choices=YES_NO),
    _q("requires_sponsorship",
       "Will you now or in the future require visa sponsorship?",
       "select", choices=YES_NO),
    _q("willing_to_relocate", "Are you willing to relocate?", "select",
       choices=YES_NO),
    _q("earliest_start", "Earliest start date"),
    _q("salary_expectation", "Salary expectation"),
)

# Per-ATS extras to the floor above: these are the fields each product's
# default form carries, which is better than guessing nothing.
_ATS_EXTRA: dict[str, tuple[Question, ...]] = {
    "lever": (
        _q("github", "GitHub profile", "url"),
        # Lever's form also has an "other URL" box. It is left out: the
        # profile holds one site, and offering the same link twice is
        # the kind of detail that reads as a mail merge.
        _q("comments", "Additional information", "textarea"),
    ),
    "greenhouse": (
        _q("github", "GitHub profile", "url"),
        _q("how_did_you_hear", "How did you hear about this job?"),
    ),
    "ashby": (
        _q("github", "GitHub profile", "url"),
    ),
}

_FALLBACK_NOTE = ("This ATS does not publish its question set — these "
                  "are the fields application forms ask anyway. Check "
                  "the form for anything extra.")


def standard_questions(ats: str) -> QuestionSet:
    return QuestionSet(ats=ats, introspected=False, note=_FALLBACK_NOTE,
                       questions=_COMMON + _ATS_EXTRA.get(ats, ()))


# --- Greenhouse -----------------------------------------------------

_GREENHOUSE_KINDS = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
    "input_tel": "phone",
}


def _from_greenhouse(payload: dict) -> tuple[Question, ...]:
    out: list[Question] = []
    for entry in payload.get("questions") or ():
        label = (entry.get("label") or "").strip()
        required = bool(entry.get("required"))
        for field in entry.get("fields") or ():
            name = (field.get("name") or "").strip()
            ftype = (field.get("type") or "").strip()
            if not name or ftype == "input_hidden":
                continue
            choices = tuple(
                Choice(label=str(v.get("label", "")), value=str(v.get("value", "")))
                for v in (field.get("values") or ()) if v.get("label")
            )
            out.append(_q(name, label or name,
                          _GREENHOUSE_KINDS.get(ftype, "text"),
                          required, choices))
    return tuple(out)


# --- Lever ----------------------------------------------------------

_LEVER_KINDS = {
    "text": "text",
    "textarea": "textarea",
    "multiple-choice": "select",
    "dropdown": "select",
    "multiple-select": "multiselect",
    "file": "file",
    "date": "date",
    "url": "url",
}


def _from_lever(payload: dict) -> tuple[Question, ...]:
    out: list[Question] = []
    for card in payload.get("customQuestions") or ():
        for i, field in enumerate(card.get("fields") or ()):
            label = (field.get("text") or "").strip()
            if not label:
                continue
            choices = tuple(
                Choice(label=str(o.get("text", o) if isinstance(o, dict) else o))
                for o in (field.get("options") or ())
            )
            out.append(_q(
                f"custom_{card.get('id') or card.get('text') or 'q'}_{i}",
                label, _LEVER_KINDS.get(field.get("type") or "", "text"),
                bool(field.get("required")), choices))
    return tuple(out)


# --- Ashby ----------------------------------------------------------

_ASHBY_KINDS = {
    "String": "text",
    "LongText": "textarea",
    "Email": "email",
    "Phone": "phone",
    "File": "file",
    "ValueSelect": "select",
    "MultiValueSelect": "multiselect",
    "Boolean": "boolean",
    "Date": "date",
    "Number": "number",
    "Score": "number",
}


def _from_ashby(payload: dict) -> tuple[Question, ...]:
    form = payload.get("applicationFormDefinition") or \
        payload.get("applicationForm") or {}
    out: list[Question] = []
    for section in form.get("sections") or ():
        for entry in section.get("fields") or ():
            field = entry.get("field") or entry
            path = (field.get("path") or field.get("title") or "").strip()
            title = (field.get("title") or path).strip()
            if not path:
                continue
            choices = tuple(
                Choice(label=str(v.get("label", "")), value=str(v.get("value", "")))
                for v in (field.get("selectableValues") or ()) if v.get("label")
            )
            out.append(_q(path, title,
                          _ASHBY_KINDS.get(field.get("type") or "", "text"),
                          bool(field.get("isRequired")), choices))
    return tuple(out)


_PARSERS = {"greenhouse": _from_greenhouse, "lever": _from_lever,
            "ashby": _from_ashby}


def questions_from_payload(ats: str, payload: Any) -> QuestionSet | None:
    """Parse an ATS payload. None when it exposes no form — the caller
    decides whether to fetch more or fall back."""
    payload = _as_dict(payload)
    parser = _PARSERS.get(ats)
    if parser is None or not payload:
        return None
    try:
        questions = parser(payload)
    except Exception:            # a payload shape we have not seen
        log.warning("could not read %s application questions", ats,
                    exc_info=True)
        return None
    if not questions:
        return None
    if ats in ("lever", "ashby"):
        # Custom cards are additive to the product's own standard form,
        # which these payloads never describe.
        known = {q.key for q in questions}
        questions = tuple(
            q for q in standard_questions(ats).questions if q.key not in known
        ) + questions
    return QuestionSet(ats=ats, introspected=True, questions=questions,
                       note=f"read from the public {ats} API")


def _as_dict(value: Any) -> dict:
    """JSONB arrives as str from asyncpg, dict from the in-memory store."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def introspection_enabled() -> bool:
    """KARANI_ATS_QUESTIONS=off means the console never touches the wire."""
    return os.getenv("KARANI_ATS_QUESTIONS", "on").lower() not in (
        "off", "0", "false")


async def introspect_questions(job_row: dict, client=None, *,
                               refresh: bool = False) -> QuestionSet:
    """The form this job asks. Never raises, never returns empty.

    Order: the cached set, then the payload already stored on the row,
    then (Greenhouse only, and only with a client) one HTTP GET, then
    the standard set.
    """
    ats = (job_row.get("source") or "").lower()

    if not refresh:
        cached = _as_dict(job_row.get("ats_questions"))
        if cached.get("questions"):
            try:
                return QuestionSet.model_validate(cached)
            except Exception:
                log.warning("stored question set for job %s is unreadable",
                            job_row.get("id"), exc_info=True)

    from_raw = questions_from_payload(ats, job_row.get("raw"))
    if from_raw is not None:
        return from_raw

    if ats == "greenhouse" and client is not None and introspection_enabled():
        fetched = await _fetch_greenhouse(job_row, client)
        if fetched is not None:
            return fetched

    return standard_questions(ats)


async def _fetch_greenhouse(job_row: dict, client) -> QuestionSet | None:
    from karani.ingestion.base import get_with_retry

    slug = job_row.get("company")
    job_id = job_row.get("source_id")
    if not slug or not job_id:
        return None
    url = GREENHOUSE_JOB_URL.format(slug=slug, job_id=job_id)
    try:
        response = await get_with_retry(client, url)
        return questions_from_payload("greenhouse", response.json())
    except Exception as exc:
        # A closed posting or a board that moved is routine: the sheet
        # degrades, the session continues.
        log.info("greenhouse questions unavailable for job %s (%s)",
                 job_row.get("id"), exc)
        return None


__all__ = ["Choice", "Question", "QuestionSet", "GREENHOUSE_JOB_URL",
           "introspection_enabled",
           "introspect_questions", "questions_from_payload",
           "standard_questions"]
