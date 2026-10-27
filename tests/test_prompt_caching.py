"""The stable/volatile split that makes prompt caching possible.

Caching is a prefix match: anything before the breakpoint must be
byte-identical across rows or nothing is reused. Two thirds of a
qualification request qualifies — instructions, the resume, and the
taste-calibration examples are the same for every job in a pass — but
only if they sit in the system prompt rather than the per-row user
prompt, which is where the resume used to live.
"""
from __future__ import annotations

from karani.qualification.prompts import (
    PROMPT_VERSION, build_user_prompt, system_prompt,
)

RESUME = "# Kelyn Njeri\nStaff engineer. Python, Kafka, causal ML." * 20
VERDICTS = [{"title": "Staff Backend", "company_display": "GitLab",
             "fit_score": 82, "verdict": "qualified",
             "user_verdict": "apply"}]


def _job(n: int) -> dict:
    return {"company_display": f"Company{n}", "title": f"Role {n}",
            "description_text": f"Description number {n}. " * 50,
            "location_raw": "Remote", "remote_status": "remote"}


def test_stable_half_lives_in_the_system_prompt():
    sys_p = system_prompt(resume=RESUME, past_verdicts=VERDICTS)
    assert RESUME in sys_p, "resume must be cacheable, not per-row"
    assert "GitLab" in sys_p, "taste calibration is stable within a pass"


def test_volatile_half_stays_out_of_the_prefix():
    user = build_user_prompt(resume=RESUME, hints=[], job_row=_job(1),
                             past_verdicts=VERDICTS,
                             memories=["prefers remote"])
    assert "Description number 1" in user
    assert "prefers remote" in user          # per-row recall
    assert RESUME not in user, "resume duplicated into the volatile half"


def test_prefix_is_byte_identical_across_jobs():
    """The actual cache requirement — if this drifts, every row pays
    full input price and the caching work bought nothing."""
    a = system_prompt(resume=RESUME, past_verdicts=VERDICTS)
    b = system_prompt(resume=RESUME, past_verdicts=VERDICTS)
    assert a == b
    # And it must be big enough for the 512-token minimum to pass.
    assert len(a) / 3.7 > 512


def test_system_prompt_still_works_without_a_resume():
    """Callers that only want the instructions (config tests, drafting)
    must not break on the new signature."""
    assert "career copilot" in system_prompt().lower()


def test_prompt_version_tracks_material_changes():
    # Rule 4.10: persisted rows carry the version, so cohorts stay
    # distinguishable across the restructure (v5) and the career
    # transcript (v6).
    assert PROMPT_VERSION == "qual-v6"
