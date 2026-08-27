"""Pre-filter correctness — word boundaries, comp anchoring, role gate."""
from __future__ import annotations

import pytest

from karani.ingestion.config import settings
from karani.ingestion.filters import _find_signal, _parse_comp, pre_filter
from karani.ingestion.models import Job, RemoteStatus, RoleCategory, Seniority, Source


def _job(**overrides):
    defaults = dict(
        source=Source.GREENHOUSE, source_id="1",
        company="c", company_display="C",
        title="Senior Software Engineer",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=(
            "Python and Go. Salary: $200,000 - $250,000. "
            "Same pay regardless of location. Annual retreat. Hire globally."
        ),
        apply_url="https://x/1",
    )
    defaults.update(overrides)
    return Job(**defaults).finalize()


# --- Word-boundary signal matching ---

def test_signal_word_boundary_us_only_not_matched_in_campus_only():
    assert _find_signal("we hire on campus only in Nairobi",
                        settings.regional_restriction_signals) is None


def test_signal_word_boundary_global_not_matched_in_global_north_only():
    assert _find_signal("global-north-only role",
                        settings.global_hire_positive_signals) is None


def test_signal_positive_match_work_from_anywhere():
    assert _find_signal("You can work from anywhere in the world",
                        settings.global_hire_positive_signals) == "work from anywhere"


def test_signal_negative_match_us_only():
    assert _find_signal("This role is US only.",
                        settings.regional_restriction_signals) == "us only"


# --- Comp parsing ---

@pytest.mark.parametrize("text,expected", [
    ("5-10 years experience required",            (None, None, False)),
    ("3 to 5 direct reports",                      (None, None, False)),
    ("Team of 15 to 20 engineers",                 (None, None, False)),
    ("Salary range: $180,000 - $250,000",          (180000, 250000, True)),
    ("USD 200k - 260k base pay",                   (200000, 260000, True)),
    ("Compensation: $150k+ plus equity",           (150000, None, True)),
    ("Between 150000 and 200000 USD salary",       (150000, 200000, True)),
    ("Base salary 180,000 to 220,000",             (180000, 220000, True)),
])
def test_comp_parsing(text, expected):
    assert _parse_comp(text) == expected


# --- pre_filter end-to-end ---

def test_prefilter_passes_ideal_role():
    pf = pre_filter(_job())
    assert pf.pass_hard_filters
    assert pf.role_category == RoleCategory.SOFTWARE_ENGINEERING
    assert pf.seniority == Seniority.SENIOR
    assert pf.comp_min_usd == 200_000
    assert pf.meets_comp_floor is True
    assert pf.pay_parity == "likely_yes"
    assert "python" in pf.matched_must_haves
    assert pf.score > 60


def test_prefilter_drops_sales_role():
    pf = pre_filter(_job(title="Enterprise Account Executive",
                          description_text="Sales role. No Python."))
    assert not pf.pass_hard_filters
    assert pf.role_category == RoleCategory.SALES_MARKETING


def test_prefilter_drops_hybrid_role():
    pf = pre_filter(_job(remote_status=RemoteStatus.HYBRID,
                          description_text="Hybrid required. Python needed."))
    assert not pf.pass_hard_filters
    assert "hybrid required" in pf.reasons_failed


def test_prefilter_drops_junior():
    pf = pre_filter(_job(title="Junior Python Developer",
                          description_text="Python. Salary $80,000 - $100,000."))
    assert not pf.pass_hard_filters
    assert pf.seniority == Seniority.JUNIOR


def test_prefilter_drops_region_locked():
    pf = pre_filter(_job(
        description_text="US only. Python. Salary $200,000+."
    ))
    assert not pf.pass_hard_filters
    assert any("regional restriction" in r for r in pf.reasons_failed)


def test_prefilter_drops_low_comp_when_disclosed():
    pf = pre_filter(_job(
        description_text="Python + Go. Salary: $80,000 - $100,000. Hire globally."
    ))
    assert not pf.pass_hard_filters
    assert any("below" in r for r in pf.reasons_failed)


# --- Relocation thesis (config.relocation_signals; prompts qual-v3) ---

def test_relocation_softens_region_lock():
    pf = pre_filter(_job(description_text=(
        "Must be based in Berlin. We offer visa sponsorship and a "
        "relocation package. Python. Salary: EUR salary 90,000 - 120,000."
    ), remote_status=RemoteStatus.UNKNOWN))
    assert pf.relocation_support == "likely_yes"
    assert pf.global_hire_eligible == "unclear"  # LLM decides, no veto
    assert not any("regional restriction" in r for r in pf.reasons_failed)


def test_relocation_softens_onsite_and_hybrid():
    onsite = pre_filter(_job(
        remote_status=RemoteStatus.ONSITE,
        description_text=("Onsite in Tokyo. Visa sponsorship and relocation "
                          "assistance provided. Python. Salary: $180,000+."),
    ))
    assert onsite.pass_hard_filters
    assert onsite.relocation_support == "likely_yes"

    hybrid = pre_filter(_job(
        remote_status=RemoteStatus.HYBRID,
        description_text=("Hybrid in Amsterdam. Relocation package offered. "
                          "Python. Salary: $170,000+."),
    ))
    assert hybrid.pass_hard_filters


def test_region_lock_without_relocation_still_drops():
    pf = pre_filter(_job(
        description_text="US only. Python. Salary $200,000+.",
    ))
    assert not pf.pass_hard_filters
    assert pf.relocation_support == "unclear"


def test_relocation_boosts_score():
    base = pre_filter(_job())
    reloc = pre_filter(_job(description_text=(
        "Python and Go. Salary: $200,000 - $250,000. "
        "Same pay regardless of location. Annual retreat. Hire globally. "
        "Relocation support available."
    )))
    assert reloc.score == base.score + 10


def test_comp_bio_titles_excluded():
    for title in ("Senior Computational Biologist",
                  "Bioinformatics Engineer",
                  "Machine Learning Engineer, Genomics"):
        pf = pre_filter(_job(title=title))
        assert not pf.pass_hard_filters, title
        assert any("excluded term" in r for r in pf.reasons_failed), title
    # Plain ML/SWE/research titles still pass.
    for title in ("Senior Machine Learning Engineer",
                  "Research Engineer",
                  "Senior Software Engineer"):
        assert pre_filter(_job(title=title)).pass_hard_filters, title
