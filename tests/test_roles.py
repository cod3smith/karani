"""Role + seniority classifier coverage."""
from __future__ import annotations

import pytest

from ingestion.models import RoleCategory, Seniority
from ingestion.roles import classify


CASES = [
    ("Senior Software Engineer, Backend",  RoleCategory.SOFTWARE_ENGINEERING, Seniority.SENIOR),
    ("Staff Machine Learning Engineer",    RoleCategory.ML_AI,                Seniority.STAFF),
    ("Principal SRE",                       RoleCategory.DEVOPS_SRE,           Seniority.PRINCIPAL),
    ("Junior React Developer",              RoleCategory.SOFTWARE_ENGINEERING, Seniority.JUNIOR),
    ("Head of Sales",                        RoleCategory.SALES_MARKETING,      Seniority.MANAGER),
    ("Product Designer",                     RoleCategory.DESIGN,               Seniority.UNKNOWN),
    ("Data Engineer",                        RoleCategory.DATA,                 Seniority.UNKNOWN),
    ("Applied Scientist, LLM",               RoleCategory.ML_AI,                Seniority.UNKNOWN),
    ("Engineering Manager, Platform",        RoleCategory.ENGINEERING_LEADERSHIP, Seniority.MANAGER),
    ("Application Security Engineer",        RoleCategory.SECURITY,             Seniority.UNKNOWN),
    ("Customer Success Manager",             RoleCategory.SALES_MARKETING,      Seniority.MANAGER),
]


@pytest.mark.parametrize("title,cat,sen", CASES)
def test_classifier(title, cat, sen):
    got = classify(title, [], "")
    assert got.category == cat, f"{title!r} → {got.category}"
    assert got.seniority == sen, f"{title!r} → {got.seniority}"


def test_classifier_tag_fallback_hits_ml():
    # Description carries a category-anchor phrase; classifier should catch it
    # in the second-pass fallback, with lower confidence than a title hit.
    got = classify(
        "Contributor",
        ["ai", "python"],
        "Applied Scientist role focused on LLM Engineer work.",
    )
    assert got.category == RoleCategory.ML_AI
    assert got.confidence < 1.0


def test_classifier_falls_through_to_other():
    got = classify("Widget Wrangler", [], "assorted duties")
    assert got.category == RoleCategory.OTHER
