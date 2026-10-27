"""The answer sheet: every field on the form, with where the answer came from."""
from __future__ import annotations

import pytest

from karani.config.schema import LegalCfg
from karani.profile import Profile
from karani.submission.answers import build_answer_sheet
from karani.submission.pack import PackContent
from karani.submission.questions import (
    Choice, Question, QuestionSet, standard_questions,
)

LEGAL = LegalCfg(citizenships=("Kenya",), based_in_country="Kenya",
                 work_authorized_in=("Kenya",),
                 remote_engagement=("employer_of_record", "contractor"),
                 relocation_open_to=("EU", "Japan"), notice_period_days=30)

PROFILE = Profile(
    name="Kelyn Njeri",
    location="Nairobi, Kenya",
    contacts=("Nairobi, Kenya · kelyn@example.com · +254 700 000 000",
              "linkedin.com/in/kelynnjeri · github.com/kelyn · "
              "https://kelyn.example"),
)

GLOBAL_REMOTE_JOB = {
    "id": 12, "source": "greenhouse", "company": "acme",
    "company_display": "Acme", "title": "Staff Backend Engineer",
    "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
    "location_raw": "Remote - Global",
    "comp_min_usd": 180000, "comp_max_usd": 220000, "comp_disclosed": True,
    "prefilter": {"fully_remote": "yes", "global_hire_eligible": "likely_yes"},
}


def _sheet(questions, *, job=None, pack=None, documents=None):
    return build_answer_sheet(
        job_row=job or GLOBAL_REMOTE_JOB,
        questions=QuestionSet(ats="greenhouse", introspected=True,
                              questions=tuple(questions)),
        profile=PROFILE, legal=LEGAL, pack=pack or PackContent(),
        documents=documents or {},
    )


def _by_key(sheet):
    return {a.key: a for a in sheet.answers}


# --- the invariant: legal answers are resolved, never written --------

def test_work_authorization_comes_from_the_resolver_not_the_pack():
    pack = PackContent(answers={
        "Will you now or in the future require visa sponsorship?":
            "Yes, I would need sponsorship and I am very excited about it.",
    })
    q = Question(key="question_1",
                 label="Will you now or in the future require visa "
                       "sponsorship?",
                 kind="select", required=True,
                 choices=(Choice(label="Yes"), Choice(label="No")))
    answer = _by_key(_sheet([q], pack=pack))["question_1"]

    # Globally-remote role: no visa exists in the transaction.
    assert answer.value == "No"
    assert answer.source == "legal"
    assert "sponsorship" not in answer.value.lower()


def test_select_answers_use_the_forms_own_option_wording():
    q = Question(key="q_auth", label="Are you legally authorized to work?",
                 kind="select",
                 choices=(Choice(label="Yes, I am authorized"),
                          Choice(label="No, I need sponsorship")))
    job = dict(GLOBAL_REMOTE_JOB, location_raw="Berlin, Germany",
               prefilter={"fully_remote": "no",
                          "global_hire_eligible": "likely_no",
                          "relocation_support": "likely_yes"})
    answer = _by_key(_sheet([q], job=job))["q_auth"]
    assert answer.value == "No, I need sponsorship"
    assert answer.source == "legal"


def test_relocation_and_start_date_are_derived_too():
    sheet = _sheet(standard_questions("greenhouse").questions)
    answers = _by_key(sheet)
    assert answers["willing_to_relocate"].source == "legal"
    assert answers["earliest_start"].value == "30 days from offer"
    assert answers["earliest_start"].source == "legal"


def test_an_unclear_geography_flags_the_sheet_for_review():
    job = dict(GLOBAL_REMOTE_JOB, location_raw="",
               prefilter={"fully_remote": "no", "global_hire_eligible": None})
    sheet = _sheet(standard_questions("greenhouse").questions, job=job)
    assert sheet.legal_review is True
    assert sheet.needs_review is True
    assert "confirm" in sheet.legal_note.lower()
    assert _by_key(sheet)["work_authorization"].needs_input is True


# --- identity and documents -----------------------------------------

def test_identity_fields_are_filled_from_the_profile():
    answers = _by_key(_sheet(standard_questions("greenhouse").questions))
    assert answers["first_name"].value == "Kelyn"
    assert answers["last_name"].value == "Njeri"
    assert answers["email"].value == "kelyn@example.com"
    assert answers["phone"].value == "+254 700 000 000"
    assert answers["linkedin"].value == "https://linkedin.com/in/kelynnjeri"
    assert answers["github"].value == "https://github.com/kelyn"
    assert answers["website"].value == "https://kelyn.example"
    assert answers["location"].value == "Nairobi, Kenya"
    assert answers["first_name"].source == "profile"


def test_file_questions_become_document_links():
    docs = {"Kelyn_Njeri_Acme_Resume.pdf": "https://minio/resume.pdf?sig=1",
            "Kelyn_Njeri_Acme_Cover_Letter.pdf": "https://minio/cl.pdf?sig=2"}
    answers = _by_key(_sheet(standard_questions("greenhouse").questions,
                             documents=docs))
    assert answers["resume"].value == "https://minio/resume.pdf?sig=1"
    assert answers["resume"].kind == "file"
    assert answers["resume"].source == "document"
    assert answers["cover_letter"].value == "https://minio/cl.pdf?sig=2"


def test_a_missing_document_is_flagged_rather_than_faked():
    answers = _by_key(_sheet(standard_questions("greenhouse").questions))
    assert answers["resume"].needs_input is True
    assert answers["resume"].value == ""


# --- prose answers ---------------------------------------------------

def test_a_drafted_answer_is_matched_to_the_form_question():
    pack = PackContent(
        cover_letter="Dear Acme, ...",
        positioning="Data-platform engineer who ships causal ML.",
        answers={"Why do you want to work at Acme?":
                 "Because the data platform is the product."})
    q = Question(key="question_42", kind="textarea",
                 label="Why do you want to work at Acme?")
    answer = _by_key(_sheet([q], pack=pack))["question_42"]
    assert answer.value == "Because the data platform is the product."
    assert answer.source == "pack"


def test_an_unmatched_free_text_question_asks_for_input():
    pack = PackContent(answers={"Why Acme?": "Because."})
    q = Question(key="question_7", kind="textarea", required=True,
                 label="Describe your experience with Kubernetes operators")
    sheet = _sheet([q], pack=pack)
    answer = _by_key(sheet)["question_7"]
    assert answer.value == ""
    assert answer.needs_input is True
    # An unanswered required field blocks sending, but it is not the
    # legal flag - that one means karani could not answer honestly.
    assert sheet.needs_review is True
    assert sheet.legal_review is False


def test_drafted_prose_the_form_has_no_field_for_still_reaches_the_sheet():
    pack = PackContent(
        cover_letter="Dear Acme, I built the thing.",
        answers={"Describe a system you are proud of": "A 400-node job."})
    sheet = _sheet([Question(key="first_name", label="First name")],
                   pack=pack)
    values = {a.label: a for a in sheet.answers}
    extra = values["Describe a system you are proud of"]
    assert extra.value == "A 400-node job."
    assert extra.source == "pack" and extra.needs_input is False
    assert values["Cover letter (text)"].value == "Dear Acme, I built the thing."


def test_a_matched_answer_is_not_also_appended_as_an_extra():
    pack = PackContent(answers={"Why Acme?": "Because."})
    sheet = _sheet([Question(key="q", label="Why Acme?", kind="textarea")],
                   pack=pack)
    assert [a.value for a in sheet.answers] == ["Because."]


def test_a_why_question_falls_back_to_the_positioning_line():
    pack = PackContent(positioning="Nine years of data platforms.")
    q = Question(key="q_why", kind="textarea",
                 label="Why are you interested in this role?")
    answer = _by_key(_sheet([q], pack=pack))["q_why"]
    assert answer.value == "Nine years of data platforms."
    assert answer.source == "pack"


# --- things karani must not answer -----------------------------------

@pytest.mark.parametrize("label", [
    "Gender", "Race / Ethnicity", "Veteran status",
    "Disability status (voluntary self-identification)",
    "What are your pronouns?",
])
def test_demographic_questions_are_left_to_the_human(label):
    q = Question(key="eeo", label=label, kind="select")
    sheet = _sheet([q])
    answer = _by_key(sheet)["eeo"]
    assert answer.value == ""
    assert answer.source == "voluntary"
    assert answer.needs_input is False       # voluntary: never blocking
    assert sheet.needs_review is False


def test_salary_expectation_anchors_on_the_posted_band():
    answers = _by_key(_sheet(standard_questions("greenhouse").questions))
    salary = answers["salary_expectation"]
    assert "180,000" in salary.value and "220,000" in salary.value
    assert salary.source == "job"


def test_salary_expectation_without_a_posted_band_asks_for_input():
    job = dict(GLOBAL_REMOTE_JOB, comp_min_usd=None, comp_max_usd=None,
               comp_disclosed=False)
    answers = _by_key(_sheet(standard_questions("greenhouse").questions,
                             job=job))
    assert answers["salary_expectation"].needs_input is True


def test_the_sheet_carries_the_job_and_counts_what_is_outstanding():
    sheet = _sheet(standard_questions("greenhouse").questions)
    assert sheet.job_id == 12
    assert sheet.company == "Acme"
    assert sheet.apply_url == "https://boards.greenhouse.io/acme/jobs/1"
    assert sheet.outstanding == sum(1 for a in sheet.answers if a.needs_input)
    assert sheet.answered + sheet.outstanding + sum(
        1 for a in sheet.answers if a.source == "voluntary"
    ) == len(sheet.answers)
