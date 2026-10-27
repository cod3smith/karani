"""The coach's stable/volatile split (ADR 0019).

The coach carries the biggest stable prefix in karani — resume, merged
profile, indexed records and a career transcript that can run to
12,000 words. Caching is a prefix match, so one line of job-specific
text above the breakpoint turns every prep into a full-price request
and nothing fails loudly enough to notice. These tests are the alarm.
"""
from __future__ import annotations

from karani.coach.prompts import (PREP_PROMPT_VERSION, STORY_PROMPT_VERSION,
                                  candidate_prefix, prep_system_prompt,
                                  prep_user_prompt, story_system_prompt,
                                  story_user_prompt)

RESUME = "# Kelyn Njeri\nStaff engineer. Python, Kafka, causal ML.\n" * 20
TRANSCRIPT = "So the DataQRL thing started because nobody owned ingest. " * 40
RECORDS = "[p0] Staff Engineer at DataQRL\n    [p0b0] Cut p99 to 180ms"
CANDIDATE = {"resume": RESUME, "transcript": TRANSCRIPT,
             "profile": "<profile>merged</profile>", "records": RECORDS}


def _job(n: int) -> dict:
    return {"company_display": f"Company{n}", "title": f"Role {n}",
            "description_text": f"Description number {n}. " * 40}


def test_candidate_material_is_in_the_system_prompt():
    system = prep_system_prompt(**CANDIDATE)
    assert RESUME in system
    assert TRANSCRIPT in system
    assert "p0b0" in system


def test_the_prefix_is_byte_identical_across_jobs():
    """The actual cache requirement. If this drifts, every prep pays
    full input price and the transcript becomes the most expensive
    thing karani owns."""
    a = prep_system_prompt(**CANDIDATE)
    b = prep_system_prompt(**CANDIDATE)
    assert a == b
    assert story_system_prompt(**CANDIDATE) == story_system_prompt(**CANDIDATE)
    # And long enough to clear the 512-token minimum (anthropic.py).
    assert len(a) / 3.7 > 512


def test_no_job_specific_text_leaks_into_the_prefix():
    system = prep_system_prompt(**CANDIDATE)
    for job in (_job(1), _job(2)):
        assert job["company_display"] not in system
        assert job["description_text"][:30] not in system


def test_both_calls_share_the_same_candidate_block():
    """Two calls per pack: the shared block is written once and read
    once, so it must be the same bytes in both."""
    block = candidate_prefix(**CANDIDATE)
    assert block in prep_system_prompt(**CANDIDATE)
    assert block in story_system_prompt(**CANDIDATE)


def test_volatile_half_carries_the_job_and_the_research():
    user = prep_user_prompt(
        job_row=_job(7), stage="screen",
        qualification='{"gaps": [{"requirement": "Rust"}]}',
        dossier="## Background\nThey build CI runners.",
        stories="### Cutting latency", comp="- Posted band: $200,000",
        question_bank=["Asked about incident ownership"])
    assert "Company7" in user and "Rust" in user
    assert "CI runners" in user and "$200,000" in user
    assert "incident ownership" in user and "screen" in user
    assert RESUME not in user, "candidate material duplicated into the "\
                               "volatile half"


def test_story_user_prompt_stays_job_only():
    user = story_user_prompt(_job(3), ["distributed systems"])
    assert "Company3" in user and "distributed systems" in user
    assert TRANSCRIPT not in user


def test_prompt_versions_track_material_changes():
    # Rule 4.10 — persisted packs carry the version. prep-v3 supersedes
    # the single-call prep-v2 in drafting/prep.py.
    assert PREP_PROMPT_VERSION == "prep-v3"
    assert STORY_PROMPT_VERSION == "star-v1"
