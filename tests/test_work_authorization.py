"""Work-authorization answers: declared facts in, deterministic out.

These are legal attestations on a submitted application. A model that
improvises here does not produce a typo, it produces a
misrepresentation — and the drafts already drift toward it ("targeting
SF-band remote roles without visa requirements"). So the yes/no answers
come from a lookup against declared facts, never from an LLM, which is
rule 4.9's doctrine applied to legal claims.

The same question also has different correct answers by role shape: a
globally-remote role engaging through an EOR in Nairobi requires no
visa at all, while a Berlin on-site role requires sponsorship. Answering
one with the other's answer either misstates the facts or gets
auto-filtered for a problem that does not exist.
"""
from __future__ import annotations

import pytest

from karani.config.schema import LegalCfg
from karani.legal import resolve_work_authorization

KELYN = LegalCfg(
    citizenships=("Kenya",),
    based_in_country="Kenya",
    work_authorized_in=("Kenya",),
    remote_engagement=("employer_of_record", "contractor"),
    relocation_open_to=("EU", "Japan"),
    notice_period_days=0,
)


def _job(**prefilter) -> dict:
    base = {"global_hire_eligible": "unclear", "fully_remote": "unclear",
            "relocation_support": "unclear"}
    base.update(prefilter)
    return {"company_display": "Acme", "title": "Staff Engineer",
            "location_raw": base.pop("location", "Remote"),
            "prefilter": base}


# --- the three shapes ---

def test_global_remote_needs_no_sponsorship():
    """The expensive mistake to avoid: saying "yes, I need sponsorship"
    on a role where no visa exists, and being filtered out for it."""
    a = resolve_work_authorization(
        _job(global_hire_eligible="likely_yes", fully_remote="yes"), KELYN)
    assert a.requires_sponsorship is False
    assert a.authorized_in_role_country is None    # no country to be in
    assert a.needs_review is False
    assert "employer of record" in a.note.lower()


def test_relocation_role_requires_sponsorship_and_is_viable():
    a = resolve_work_authorization(
        _job(relocation_support="likely_yes", fully_remote="no",
             location="Berlin, Germany"), KELYN)
    assert a.requires_sponsorship is True
    assert a.authorized_in_role_country is False
    assert a.willing_to_relocate is True
    assert a.viable is True                        # they sponsor, he'd move


def test_region_locked_without_sponsorship_is_flagged_unviable():
    a = resolve_work_authorization(
        _job(global_hire_eligible="likely_no", fully_remote="yes",
             location="United States"), KELYN)
    assert a.requires_sponsorship is True
    assert a.authorized_in_role_country is False
    assert a.viable is False


def test_role_in_a_country_he_is_authorized_in():
    a = resolve_work_authorization(
        _job(fully_remote="no", location="Nairobi, Kenya"), KELYN)
    assert a.authorized_in_role_country is True
    assert a.requires_sponsorship is False


# --- the honesty invariant ---

@pytest.mark.parametrize("location", [
    "United States", "San Francisco, CA", "London, United Kingdom",
    "Berlin, Germany", "Tokyo, Japan", "Toronto, Canada", "Remote (US)",
])
def test_never_claims_authorization_he_does_not_hold(location):
    """The one invariant that matters: no "yes, I am authorized" for a
    country that is not in the declared list. Ever."""
    a = resolve_work_authorization(
        _job(fully_remote="no", location=location), KELYN)
    assert a.authorized_in_role_country is not True


def test_unknown_geography_asks_for_review_instead_of_guessing():
    a = resolve_work_authorization(_job(location="Planet Zog"), KELYN)
    assert a.needs_review is True
    assert a.authorized_in_role_country is not True


# --- the form answers ---

def test_answers_the_questions_ats_forms_actually_ask():
    a = resolve_work_authorization(
        _job(global_hire_eligible="likely_yes", fully_remote="yes"), KELYN)
    forms = a.form_answers()
    assert forms["requires_sponsorship"] == "No"
    assert forms["earliest_start"].lower() == "immediately"
    assert "Kenya" in forms["work_location"]


def test_notice_period_reaches_the_answer():
    thirty = KELYN.model_copy(update={"notice_period_days": 30})
    a = resolve_work_authorization(_job(fully_remote="yes"), thirty)
    assert "30 days" in a.form_answers()["earliest_start"]


# --- the drafting guardrail ---

def test_draft_prompt_forbids_inventing_legal_claims():
    """The drafts already drift here ("without visa requirements"). The
    model must be told these are resolved facts, not its business."""
    from karani.drafting.prompts import system_prompt
    rules = system_prompt().lower()
    assert "work authorization" in rules or "visa" in rules
    assert "never" in rules or "do not" in rules


def test_resolved_facts_reach_the_draft_prompt():
    from karani.drafting.prompts import build_user_prompt
    answers = resolve_work_authorization(
        _job(global_hire_eligible="likely_yes", fully_remote="yes"), KELYN)
    user = build_user_prompt(
        resume="# K", qualification="fit", job_row=_job(),
        keyword_targets=[], work_auth=answers)
    assert "employer of record" in user.lower()
    assert "no visa" in user.lower() or "sponsorship" in user.lower()


def test_draft_prompt_version_bumped():
    from karani.drafting.prompts import DRAFT_PROMPT_VERSION
    assert DRAFT_PROMPT_VERSION == "draft-v4"


# --- region-locked roles, caught by smoke not by unit tests ---

@pytest.mark.parametrize("location", [
    "Remote (US)", "Remote - US only", "US-Remote", "Remote, USA",
    "Anywhere in the United States",
])
def test_us_remote_phrasings_are_recognized(location):
    a = resolve_work_authorization(
        _job(fully_remote="yes", global_hire_eligible="likely_no",
             location=location), KELYN)
    assert a.requires_sponsorship is True
    assert a.viable is False


def test_region_locked_with_unparseable_location_stays_conservative():
    """The pre-filter's region-lock verdict is authoritative even when
    the country cannot be named — answering "no sponsorship needed"
    there would be a false attestation, not a near miss."""
    a = resolve_work_authorization(
        _job(fully_remote="yes", global_hire_eligible="likely_no",
             location="Remote — our core hours only"), KELYN)
    assert a.requires_sponsorship is True
    assert a.authorized_in_role_country is not True
    assert a.needs_review is True
    assert a.viable is False
