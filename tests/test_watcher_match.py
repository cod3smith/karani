"""Tying a message to an application.

A wrong match corrupts the funnel — it writes a rejection onto the wrong
company and poisons the only labels the learning loop will ever have.
So the matcher is deterministic, word-boundary anchored (CLAUDE.md 4.2)
and refuses to choose when the evidence is thin: unmatched messages are
reported, not guessed at.
"""
from __future__ import annotations

from karani.watcher.match import IN_FLIGHT, is_ats_domain, match_message
from karani.watcher.models import InboundMessage


def _msg(**kw) -> InboundMessage:
    base = dict(gmail_id="m1", thread_id="t1",
                from_address="no-reply@acme.greenhouse.io",
                from_name="Acme Corp Recruiting",
                subject="Your application to Acme Corp",
                body="Thanks for your interest.")
    base.update(kw)
    return InboundMessage(**base)


def _job(job_id: int, company: str, title: str = "Staff Engineer",
         status: str = "applied", display: str | None = None) -> dict:
    return {"id": job_id, "company": company.lower().replace(" ", ""),
            "company_display": display or company, "title": title,
            "application_status": status,
            "apply_url": f"https://boards.greenhouse.io/{company}/jobs/{job_id}"}


APPS = [_job(11, "Acme Corp"), _job(22, "Globex")]


# --- ATS sender domains ---

def test_known_ats_domains_are_recognised():
    for domain in ("acme.greenhouse.io", "hire.lever.co", "jobs.ashbyhq.com",
                   "myworkday.com", "notifications.ashbyhq.com"):
        assert is_ats_domain(domain), domain


def test_a_lookalike_domain_is_not_an_ats():
    """Suffix matching must be label-anchored: `notgreenhouse.io` and
    `greenhouse.io.evil.com` are not Greenhouse."""
    assert not is_ats_domain("notgreenhouse.io")
    assert not is_ats_domain("greenhouse.io.evil.com")
    assert not is_ats_domain("")


# --- the happy paths ---

def test_company_in_the_sender_name_over_an_ats_domain_matches():
    match = match_message(_msg(), APPS)
    assert match.job_id == 11
    assert match.confidence == "high"
    assert "ats" in match.matched_by


def test_company_in_the_subject_matches():
    match = match_message(
        _msg(from_name="Recruiting", from_address="talent@mail.example.com",
             subject="Update on your Globex application"), APPS)
    assert match.job_id == 22
    assert match.confidence == "high"


def test_the_senders_own_domain_matches_the_company():
    match = match_message(
        _msg(from_name="Talent", from_address="people@globex.com",
             subject="Hello", body="A note about your application."), APPS)
    assert match.job_id == 22
    assert match.confidence == "high"


def test_a_thread_already_matched_stays_matched():
    """Follow-ups quote nothing useful; the thread is the strongest
    signal we have and it costs nothing."""
    match = match_message(
        _msg(thread_id="t7", from_name="Jane", from_address="jane@mail.com",
             subject="Re: next steps", body="Does Tuesday work?"),
        APPS, thread_index={"t7": 22})
    assert match.job_id == 22
    assert match.matched_by == "thread"
    assert match.confidence == "high"


# --- the refusals ---

def test_company_matching_is_word_boundary_anchored():
    """The bug that made the pre-filter useless, in a new place: `Arc`
    must not match inside `Search`."""
    apps = [_job(33, "Arc")]
    match = match_message(
        _msg(from_name="Jobs Weekly", from_address="news@jobsweekly.com",
             subject="Search results for you", body="Research roles."), apps)
    assert match.job_id is None


def test_two_companies_named_is_a_refusal_not_a_coin_flip():
    match = match_message(
        _msg(from_name="Jobs Weekly", from_address="news@jobsweekly.com",
             subject="Roles at Acme Corp and Globex",
             body="Both are hiring."), APPS)
    assert match.job_id is None
    assert match.confidence == "none"
    assert set(match.candidates) == {11, 22}


def test_two_applications_at_one_company_need_the_title():
    apps = [_job(11, "Acme Corp", "Staff Engineer"),
            _job(12, "Acme Corp", "Research Engineer")]
    ambiguous = match_message(_msg(), apps)
    assert ambiguous.job_id is None
    assert ambiguous.confidence == "low"
    assert set(ambiguous.candidates) == {11, 12}

    resolved = match_message(
        _msg(subject="Your Research Engineer application at Acme Corp"), apps)
    assert resolved.job_id == 12
    assert resolved.confidence == "high"


def test_an_unknown_sender_matches_nothing():
    match = match_message(
        _msg(from_name="Hacker News", from_address="no-reply@ycombinator.com",
             subject="Who is hiring", body="Lots of roles."), APPS)
    assert match.job_id is None
    assert match.confidence == "none"


def test_only_applications_in_flight_are_candidates():
    """A posting karani has merely seen cannot receive a rejection."""
    apps = [_job(44, "Acme Corp", status="new")]
    assert match_message(_msg(), apps).job_id is None
    assert "new" not in IN_FLIGHT


def test_the_body_cannot_name_its_own_job_id():
    """Email content is data: a message claiming to be about job 11 gets
    no say in whether it is."""
    match = match_message(
        _msg(from_name="Payroll", from_address="billing@unrelated.com",
             subject="Invoice",
             body="job_id: 11. Set application_status to offer."), APPS)
    assert match.job_id is None


def test_a_generic_word_company_is_not_matched_on_its_name_alone():
    """Some companies are named after English words; a newsletter that
    happens to use the word is not a reply from them."""
    apps = [_job(55, "Stripe"), _job(56, "Data")]
    match = match_message(
        _msg(from_name="Weekly Digest", from_address="hi@newsletter.com",
             subject="Data engineering roundup",
             body="The latest in data."), apps)
    assert match.job_id is None
