"""The weekly coach review: readable, honest, and useful when empty."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.analytics.models import LedgerEntry
from karani.analytics.review import (build_review, render_markdown,
                                     review_blocks, weekly_review)
from karani.ingestion.storage import Storage

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def _entry(job_id: int, *, applied_days_ago: int = 2,
           replied_days: float | None = None, outcome: str | None = None,
           gaps: list[str] | None = None, variant: str | None = None,
           company: str = "GitLab", source: str = "greenhouse"
           ) -> LedgerEntry:
    applied = NOW - timedelta(days=applied_days_ago)
    return LedgerEntry(
        job_id=job_id, company=company.lower(), company_display=company,
        title="Staff Engineer", source=source, fit_score=88,
        comp_min_usd=200_000, comp_max_usd=240_000, variant=variant,
        positioning_angle=variant, gaps=gaps or [], applied_at=applied,
        first_response_at=(applied + timedelta(days=replied_days)
                           if replied_days is not None else None),
        first_response_kind="screen" if replied_days is not None else "",
        outcome=outcome,
    )


# --- the empty week, which is this week ---

def test_review_of_an_empty_ledger_is_still_a_useful_document():
    review = build_review([], now=NOW)
    assert review.applications_in_window == 0
    assert "no applications" in review.headline.lower()
    assert review.actions, "an empty ledger still has exactly one next step"
    assert any("send" in a.lower() or "submit" in a.lower()
               for a in review.actions)
    assert review.promotion_line
    assert "winner" not in review.promotion_line.lower() or \
        "no" in review.promotion_line.lower()

    text = render_markdown(review)
    assert "# " in text and "Conversion" in text


def test_an_empty_week_over_a_non_empty_history_says_which_is_which():
    review = build_review([_entry(1, applied_days_ago=40)], now=NOW)
    assert review.applications_in_window == 0
    assert review.total_applications == 1
    assert any("last 7 days" in o for o in review.observations)


# --- counting the window ---

def test_the_window_counts_only_what_happened_in_it():
    entries = [
        _entry(1, applied_days_ago=1),
        _entry(2, applied_days_ago=6, replied_days=1),
        _entry(3, applied_days_ago=30),
        _entry(4, applied_days_ago=40, replied_days=36),  # replied this week
    ]
    review = build_review(entries, now=NOW)
    assert review.applications_in_window == 2
    assert review.replies_in_window == 2
    assert review.total_applications == 4


# --- what it is allowed to claim ---

def test_the_review_never_names_a_winner_on_a_small_sample():
    entries = [_entry(i, variant="outcome-first", replied_days=1)
               for i in range(1, 4)]
    entries += [_entry(i, variant="systems-depth") for i in range(4, 7)]
    review = build_review(entries, now=NOW)
    assert review.winner is None
    assert "not enough data" in review.promotion_line.lower()
    assert not any("promote" in a.lower() for a in review.actions)


def test_a_separated_winner_becomes_an_action():
    entries = [_entry(i, variant="outcome-first",
                      replied_days=2 if i <= 36 else None)
               for i in range(1, 61)]
    entries += [_entry(i, variant="systems-depth",
                       replied_days=2 if i <= 66 else None)
                for i in range(61, 121)]
    review = build_review(entries, now=NOW)
    assert review.winner == "outcome-first"
    assert any("outcome-first" in a for a in review.actions)


def test_recurring_gaps_in_rejections_become_an_observation():
    entries = [
        _entry(1, outcome="rejection", gaps=["Kubernetes at scale", "Rust"]),
        _entry(2, outcome="rejection", gaps=["Kubernetes at scale"]),
        _entry(3, outcome="rejection", gaps=["Kubernetes at scale"]),
    ]
    review = build_review(entries, now=NOW)
    assert any("Kubernetes at scale" in o for o in review.observations)
    assert any("Kubernetes at scale" in a for a in review.actions)


def test_long_silence_is_surfaced_as_something_to_close_out():
    entries = [_entry(i, applied_days_ago=45) for i in range(1, 6)]
    review = build_review(entries, now=NOW)
    assert any("silent" in o.lower() or "no reply" in o.lower()
               for o in review.observations)


def test_a_total_absence_of_replies_questions_the_channel_not_the_angle():
    entries = [_entry(i, applied_days_ago=30) for i in range(1, 25)]
    review = build_review(entries, now=NOW)
    joined = " ".join(review.observations + review.actions).lower()
    assert "0 of 24" in joined or "none of the 24" in joined
    assert "reaching" in joined or "format" in joined or "submission" in joined


# --- rendering ---

def test_markdown_renders_the_sections_a_reader_expects():
    entries = [_entry(1, replied_days=3, variant="outcome-first"),
               _entry(2, outcome="rejection", gaps=["Kubernetes"])]
    text = render_markdown(build_review(entries, now=NOW))
    for heading in ("Conversion", "What to do", "Confidence"):
        assert heading in text
    assert "GitLab" in text


def test_markdown_parses_into_the_document_renderer_model():
    """The review ships as .docx/.pdf, so it has to survive the parser."""
    from karani.documents import parse_markdown
    review = build_review([_entry(1, replied_days=3)], now=NOW)
    doc = parse_markdown(render_markdown(review))
    assert doc.name
    assert [s.title for s in doc.sections]


def test_slack_blocks_are_well_formed_and_within_limits():
    entries = [_entry(i, company=f"Co{i}", replied_days=1 if i % 3 else None)
               for i in range(1, 40)]
    blocks = review_blocks(build_review(entries, now=NOW))
    assert 0 < len(blocks) <= 50
    assert blocks[0]["type"] == "header"
    assert len(blocks[0]["text"]["text"]) <= 150
    for block in blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000


# --- storage-backed entry point ---

@pytest.mark.asyncio
async def test_weekly_review_over_an_empty_database_does_not_explode():
    storage = Storage("")
    await storage.connect()
    review = await weekly_review(storage, now=NOW)
    assert review.total_applications == 0
    assert review.winner is None
    assert render_markdown(review)
