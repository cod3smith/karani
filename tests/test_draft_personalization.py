"""What makes a cover letter specific to one company.

The prompt told the model not to mention a company's projects "without
real reason" while never giving it any material to have a reason — the
dossier karani already builds and caches (karani/intel/) reached
followup.py but never the draft. So letters were written from the job
description alone, which is the definition of generic.
"""
from __future__ import annotations

from karani.drafting.prompts import (
    DRAFT_PROMPT_VERSION, build_user_prompt, system_prompt,
)

DOSSIER = """## Background
ClickHouse is a column-oriented OLAP database company.

## Public engineering presence
clickhouse/clickhouse — C++, 37k stars, very active.
"""


def _job():
    return {"company_display": "ClickHouse", "title": "Control Plane Engineer",
            "description_text": "Build the control plane.", "location_raw": "Remote"}


def test_dossier_reaches_the_draft_prompt():
    user = build_user_prompt(resume="# K", qualification="strong",
                             job_row=_job(), keyword_targets=[],
                             dossier=DOSSIER)
    assert "company_dossier" in user
    assert "column-oriented OLAP" in user


def test_prompt_survives_an_empty_dossier():
    """Intel is best-effort — an unknown company must not break drafting
    or invite the model to invent facts to fill the gap."""
    user = build_user_prompt(resume="# K", qualification="s",
                             job_row=_job(), keyword_targets=[], dossier="")
    assert "column-oriented" not in user
    rules = system_prompt().lower()
    assert "invent" in rules or "do not fabricate" in rules


def test_rules_demand_company_specificity():
    rules = system_prompt().lower()
    # The anti-generic heuristic: would this letter survive a find and
    # replace of the company name?
    assert "swap" in rules or "another company" in rules
    assert "dossier" in rules


def test_version_bumped_for_the_personalization_pass():
    assert DRAFT_PROMPT_VERSION == "draft-v5"
