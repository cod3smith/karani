"""A fit score is only meaningful against the scorer that produced it.

Measured on 2026-10 over a 598-row qualification run: Haiku 5.5
averaged 38 with 2 rows above 80, qwen3:4b averaged 47 with 31 above
80, Kimi averaged 51. The autopilot's fit floor had been calibrated
against Kimi and was silently matching almost nothing.

Pooling those three into one "90+ band converts at X%" would be
confident nonsense, so the ledger segments every fit-based comparison
by (qualify_model, qualify_prompt_version) and refuses to pool across
scales. This file pins that refusal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from karani.analytics.funnel import conversion_report
from karani.analytics.models import LedgerEntry
from karani.analytics.review import build_review, render_markdown

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def _entry(job_id: int, *, fit: int, qualify_model: str = "kimi-k2",
           qualify_prompt: str = "qual-v5", draft_model: str = "opus-4",
           draft_prompt: str = "draft-v5", replied: bool = False,
           variant: str | None = None) -> LedgerEntry:
    applied = NOW - timedelta(days=20)
    return LedgerEntry(
        job_id=job_id, company="gitlab", company_display="GitLab",
        title="Staff Engineer", source="greenhouse", fit_score=fit,
        qualify_model=qualify_model, qualify_prompt_version=qualify_prompt,
        draft_model=draft_model, draft_prompt_version=draft_prompt,
        variant=variant, positioning_angle=variant, applied_at=applied,
        first_response_at=applied + timedelta(days=4) if replied else None,
        first_response_kind="screen" if replied else "",
    )


# --- segmentation ---

def test_one_scorer_pools_fit_bands_normally():
    report = conversion_report([
        _entry(1, fit=92, replied=True), _entry(2, fit=91), _entry(3, fit=72),
    ])
    assert {b.key for b in report.by_fit_band} == {"90+", "70-79"}
    assert report.scale_warning == ""
    assert len(report.fit_scales) == 1
    scale = report.fit_scales[0]
    assert scale.qualify_model == "kimi-k2"
    assert scale.qualify_prompt_version == "qual-v5"
    assert scale.n == 3
    assert scale.mean_fit == 85.0


def test_two_scorers_refuse_to_pool_fit_bands():
    """The same number from two models is two different numbers."""
    report = conversion_report([
        _entry(1, fit=85, qualify_model="kimi-k2", replied=True),
        _entry(2, fit=85, qualify_model="claude-haiku-5.5"),
    ])
    assert report.by_fit_band == [], \
        "pooling fit bands across scorers is the mistake this prevents"
    assert "kimi-k2" in report.scale_warning
    assert "claude-haiku-5.5" in report.scale_warning
    assert "not comparable" in report.scale_warning.lower()


def test_a_prompt_version_bump_is_also_a_new_scale():
    report = conversion_report([
        _entry(1, fit=85, qualify_prompt="qual-v4"),
        _entry(2, fit=85, qualify_prompt="qual-v5"),
    ])
    assert report.by_fit_band == []
    assert "qual-v4" in report.scale_warning
    assert "qual-v5" in report.scale_warning


def test_each_scale_keeps_its_own_bands_and_its_own_distribution():
    entries = [
        _entry(1, fit=92, qualify_model="kimi-k2", replied=True),
        _entry(2, fit=88, qualify_model="kimi-k2"),
        _entry(3, fit=38, qualify_model="claude-haiku-5.5"),
        _entry(4, fit=42, qualify_model="claude-haiku-5.5", replied=True),
    ]
    report = conversion_report(entries)
    scales = {s.qualify_model: s for s in report.fit_scales}
    assert scales["kimi-k2"].mean_fit == 90.0
    assert scales["claude-haiku-5.5"].mean_fit == 40.0
    assert {b.key for b in scales["kimi-k2"].bands} == {"90+", "80-89"}
    assert {b.key for b in scales["claude-haiku-5.5"].bands} == {"<70"}
    # Scales are ordered by volume so the dominant scorer reads first.
    assert [s.n for s in report.fit_scales] == [2, 2]


def test_scales_are_reported_even_when_there_is_only_one_application():
    report = conversion_report([_entry(1, fit=50)])
    assert len(report.fit_scales) == 1
    assert report.fit_scales[0].n == 1
    assert report.scale_warning == ""


def test_model_and_prompt_version_are_their_own_breakdowns():
    report = conversion_report([
        _entry(1, qualify_model="kimi-k2", draft_model="opus-4", fit=80,
               replied=True),
        _entry(2, qualify_model="claude-haiku-5.5", draft_model="opus-4",
               fit=40),
    ])
    by_qualify = {b.key: b for b in report.by_qualify_model}
    assert by_qualify["kimi-k2"].applied == 1
    assert by_qualify["claude-haiku-5.5"].applied == 1
    assert [b.key for b in report.by_draft_model] == ["opus-4"]


# --- confounds on the experiment ---

def test_an_experiment_split_across_draft_models_is_flagged_as_confounded():
    """If the arms also differ by model, the arm is not what varied."""
    entries = [_entry(i, fit=80, variant="outcome-first",
                      draft_model="opus-4") for i in range(1, 25)]
    entries += [_entry(i, fit=80, variant="systems-depth",
                       draft_model="sonnet-4") for i in range(25, 49)]
    report = conversion_report(entries)
    assert report.confounds, "a model split across arms must be called out"
    joined = " ".join(report.confounds)
    assert "opus-4" in joined and "sonnet-4" in joined
    assert "outcome-first" in joined or "arm" in joined.lower()


def test_an_experiment_on_one_model_is_not_flagged():
    entries = [_entry(i, fit=80, variant="outcome-first") for i in range(1, 25)]
    entries += [_entry(i, fit=80, variant="systems-depth")
                for i in range(25, 49)]
    assert conversion_report(entries).confounds == []


# --- the review surfaces it ---

def test_the_review_surfaces_a_mixed_scale_rather_than_hiding_it():
    entries = [
        _entry(1, fit=92, qualify_model="kimi-k2", replied=True),
        _entry(2, fit=38, qualify_model="claude-haiku-5.5"),
    ]
    review = build_review(entries, now=NOW)
    assert any("not comparable" in o.lower() for o in review.observations)
    text = render_markdown(review)
    assert "kimi-k2" in text and "claude-haiku-5.5" in text
