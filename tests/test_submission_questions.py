"""ATS question introspection — the real form, or an honest fallback."""
from __future__ import annotations

import json

import httpx
import pytest

from karani.submission.questions import (
    QuestionSet, introspect_questions, questions_from_payload,
    standard_questions,
)

GREENHOUSE_DETAIL = {
    "id": 4567,
    "title": "Staff Backend Engineer",
    "questions": [
        {"label": "First Name", "required": True,
         "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Resume", "required": True,
         "fields": [{"name": "resume", "type": "input_file", "values": []}]},
        {"label": "Why do you want to work here?", "required": False,
         "fields": [{"name": "question_900", "type": "textarea", "values": []}]},
        {"label": "Are you legally authorized to work in the United States?",
         "required": True,
         "fields": [{"name": "question_901",
                     "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1},
                                {"label": "No", "value": 0}]}]},
        {"label": "Hidden tracking", "required": False,
         "fields": [{"name": "utm", "type": "input_hidden", "values": []}]},
    ],
}


def _job(source="greenhouse", **kw):
    row = {"id": 1, "source": source, "source_id": "4567", "company": "acme",
           "company_display": "Acme", "title": "Staff Backend Engineer",
           "apply_url": "https://boards.greenhouse.io/acme/jobs/4567",
           "raw": {}}
    row.update(kw)
    return row


# --- fallback -------------------------------------------------------

def test_standard_set_covers_the_fields_every_form_asks():
    qs = standard_questions("workable")
    keys = {q.key for q in qs.questions}
    assert {"first_name", "last_name", "email", "resume",
            "work_authorization", "requires_sponsorship"} <= keys
    assert qs.introspected is False
    assert qs.note


def test_unknown_ats_degrades_rather_than_raising():
    qs = questions_from_payload("remoteok", {"anything": True})
    assert qs is None


# --- greenhouse -----------------------------------------------------

def test_greenhouse_payload_yields_the_real_questions():
    qs = questions_from_payload("greenhouse", GREENHOUSE_DETAIL)
    assert qs is not None and qs.introspected is True
    by_key = {q.key: q for q in qs.questions}
    assert by_key["first_name"].kind == "text"
    assert by_key["resume"].kind == "file"
    assert by_key["question_900"].kind == "textarea"
    auth = by_key["question_901"]
    assert auth.kind == "select" and auth.required is True
    assert [c.label for c in auth.choices] == ["Yes", "No"]
    assert "authorized to work" in auth.label
    # Hidden tracking fields are not questions a human answers.
    assert "utm" not in by_key


@pytest.mark.asyncio
async def test_greenhouse_questions_are_fetched_through_get_with_retry(
        monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "on")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=GREENHOUSE_DETAIL)

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        qs = await introspect_questions(_job(), client=client)

    assert seen == ["https://boards-api.greenhouse.io/v1/boards/acme/"
                    "jobs/4567?questions=true"]
    assert qs.introspected is True
    assert any(q.key == "question_901" for q in qs.questions)


@pytest.mark.asyncio
async def test_a_dead_ats_endpoint_degrades_to_the_standard_set(monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "on")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        qs = await introspect_questions(_job(), client=client)

    assert qs.introspected is False
    assert any(q.key == "work_authorization" for q in qs.questions)


@pytest.mark.asyncio
async def test_the_flag_off_means_no_http_at_all(monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "off")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("introspection must not fetch when disabled")

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        qs = await introspect_questions(_job(), client=client)
    assert qs.introspected is False


@pytest.mark.asyncio
async def test_no_client_means_no_http_at_all():
    qs = await introspect_questions(_job())
    assert qs.introspected is False
    assert qs.ats == "greenhouse"


# --- lever / ashby: parse when exposed, degrade when not ------------

def test_lever_custom_questions_come_off_the_stored_payload():
    payload = {"id": "abc", "text": "Staff Engineer", "customQuestions": [
        {"text": "Work eligibility", "fields": [
            {"type": "text", "text": "Which country are you based in?",
             "required": True},
            {"type": "multiple-choice", "text": "Do you need sponsorship?",
             "required": True,
             "options": [{"text": "Yes"}, {"text": "No"}]},
        ]},
    ]}
    qs = questions_from_payload("lever", payload)
    assert qs is not None and qs.introspected is True
    labels = [q.label for q in qs.questions]
    assert "Which country are you based in?" in labels
    sponsor = next(q for q in qs.questions
                   if q.label == "Do you need sponsorship?")
    assert sponsor.kind == "select"
    assert [c.label for c in sponsor.choices] == ["Yes", "No"]
    # Lever's own standard fields still have to be filled.
    assert any(q.key == "email" for q in qs.questions)


def test_lever_without_custom_questions_degrades():
    assert questions_from_payload("lever", {"id": "abc"}) is None


def test_ashby_application_form_is_parsed_when_present():
    payload = {"id": "a1", "applicationFormDefinition": {"sections": [
        {"fields": [
            {"field": {"type": "String", "path": "_systemfield_name",
                       "title": "Name", "isRequired": True}},
            {"field": {"type": "ValueSelect", "path": "sponsorship",
                       "title": "Will you require sponsorship?",
                       "isRequired": True,
                       "selectableValues": [{"label": "Yes", "value": "yes"},
                                            {"label": "No", "value": "no"}]}},
            {"field": {"type": "File", "path": "_systemfield_resume",
                       "title": "Resume", "isRequired": True}},
        ]},
    ]}}
    qs = questions_from_payload("ashby", payload)
    assert qs is not None and qs.introspected is True
    by_key = {q.key: q for q in qs.questions}
    assert by_key["_systemfield_resume"].kind == "file"
    assert by_key["sponsorship"].kind == "select"
    assert by_key["sponsorship"].choices[0].label == "Yes"


@pytest.mark.asyncio
async def test_stored_raw_payload_is_used_before_any_fetch(monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "on")
    row = _job(source="lever", raw=json.dumps({
        "id": "abc", "customQuestions": [
            {"fields": [{"type": "textarea", "text": "Why Lever?"}]}]}))

    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("lever questions are already in the raw payload")

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        qs = await introspect_questions(row, client=client)
    assert qs.introspected is True
    assert any(q.label == "Why Lever?" for q in qs.questions)


@pytest.mark.asyncio
async def test_a_cached_question_set_is_reused(monkeypatch):
    monkeypatch.setenv("KARANI_ATS_QUESTIONS", "on")
    cached = QuestionSet(ats="greenhouse", introspected=True,
                         questions=(
                             {"key": "q1", "label": "Cached?",
                              "kind": "text"},))
    row = _job(ats_questions=cached.model_dump(mode="json"))

    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("cached question sets are not re-fetched")

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)) as client:
        qs = await introspect_questions(row, client=client)
    assert [q.label for q in qs.questions] == ["Cached?"]
