"""Comp research for the prep pack — deterministic, never generated.

Salary numbers are the one part of a prep pack a model must not
invent: a hallucinated band is what the candidate repeats out loud in
a negotiation. So the brief is arithmetic over the posted band, the
declared floor/target, and the bands karani has already ingested for
comparable roles.
"""
from __future__ import annotations

import pytest

from karani.coach.comp import comp_brief, render_comp
from karani.ingestion.storage import Storage


def _row(job_id: int, lo: int | None, hi: int | None, *,
         role: str = "software_engineering", seniority: str = "staff") -> dict:
    return {"id": job_id, "company_display": f"Co{job_id}",
            "title": "Staff Engineer", "comp_min_usd": lo,
            "comp_max_usd": hi, "comp_disclosed": bool(lo),
            "role_category": role, "seniority": seniority, "active": True}


def _storage(rows: list[dict]) -> Storage:
    s = Storage("")
    for i, row in enumerate(rows):
        s._memory[("test", str(i))] = row
    return s


@pytest.mark.asyncio
async def test_benchmark_comes_from_comparable_postings():
    storage = _storage([
        _row(1, 150_000, 190_000),
        _row(2, 200_000, 240_000),
        _row(3, 250_000, 290_000),
        # Different shape: must not pollute the benchmark.
        _row(4, 40_000, 50_000, role="data", seniority="mid"),
        _row(5, None, None),               # undisclosed
    ])
    job = _row(6, 180_000, 220_000)
    brief = await comp_brief(storage, job)

    assert brief.sample_size == 3
    assert brief.median_usd == 220_000     # midpoints 170/220/270
    assert brief.p25_usd == 170_000
    assert brief.p75_usd == 270_000
    assert brief.posted_min_usd == 180_000


@pytest.mark.asyncio
async def test_a_band_below_the_floor_is_called_out():
    storage = _storage([])
    brief = await comp_brief(storage, _row(1, 90_000, 110_000))
    assert brief.below_floor is True
    assert any("floor" in n.lower() for n in brief.notes)


@pytest.mark.asyncio
async def test_anchor_never_sits_below_the_target():
    """The posted maximum is a starting point, not a ceiling — but the
    candidate's own target is the number they refuse to go under."""
    storage = _storage([])
    brief = await comp_brief(storage, _row(1, 150_000, 170_000))
    assert brief.anchor_usd >= brief.target_usd


@pytest.mark.asyncio
async def test_undisclosed_band_says_so_rather_than_guessing():
    storage = _storage([])
    brief = await comp_brief(storage, _row(1, None, None))
    assert brief.posted_min_usd is None
    assert any("not disclosed" in n.lower() for n in brief.notes)
    text = render_comp(brief)
    assert "not disclosed" in text.lower()


@pytest.mark.asyncio
async def test_rendered_brief_carries_only_numbers_it_was_given():
    storage = _storage([_row(1, 200_000, 240_000)])
    brief = await comp_brief(storage, _row(2, 180_000, 220_000))
    text = render_comp(brief)
    assert "$180,000" in text and "$220,000" in text
    assert "karani's own ingestion" in text   # the honesty caveat
