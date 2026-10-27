"""The extended funnel: what converts to a reply, and how fast."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from karani.analytics.funnel import comp_band, conversion_report
from karani.analytics.models import LedgerEntry

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def _entry(job_id: int, *, variant: str | None = None, source: str = "lever",
           company: str = "GitLab", comp: int | None = 210_000,
           fit: int | None = 88, replied_days: float | None = None,
           outcome: str | None = None, angle: str | None = None,
           gaps: list[str] | None = None,
           applied_days_ago: int = 20) -> LedgerEntry:
    applied = NOW - timedelta(days=applied_days_ago)
    return LedgerEntry(
        job_id=job_id, company=company.lower(), company_display=company,
        title="Staff Engineer", source=source, fit_score=fit,
        comp_min_usd=comp, comp_max_usd=comp, variant=variant,
        positioning_angle=angle or variant, gaps=gaps or [],
        applied_at=applied,
        first_response_at=(applied + timedelta(days=replied_days)
                           if replied_days is not None else None),
        first_response_kind="screen" if replied_days is not None else "",
        outcome=outcome,
    )


# --- empty and near-empty ---

def test_report_on_an_empty_ledger_says_so_without_dividing_by_zero():
    report = conversion_report([])
    assert report.totals.applied == 0
    assert report.totals.response_rate == 0.0
    assert report.by_source == []
    assert report.time_to_first_response.n == 0
    assert report.time_to_first_response.median_days is None
    assert report.promotion.status == "no_data"
    assert "no applications" in report.confidence.lower()


def test_a_handful_of_applications_is_labelled_as_too_few():
    report = conversion_report([_entry(i) for i in range(1, 4)])
    assert report.totals.applied == 3
    assert "3 application" in report.confidence
    assert "too few" in report.confidence.lower()


# --- the breakdowns that compound ---

def test_breaks_conversion_down_by_source_company_and_comp_band():
    entries = [
        _entry(1, source="greenhouse", company="GitLab", comp=210_000,
               replied_days=4),
        _entry(2, source="greenhouse", company="GitLab", comp=210_000),
        _entry(3, source="remoteok", company="Supabase", comp=150_000),
        _entry(4, source="remoteok", company="Supabase", comp=None),
    ]
    report = conversion_report(entries)

    assert report.totals.applied == 4
    assert report.totals.responded == 1
    assert report.totals.response_rate == 0.25
    # Even the headline rate carries its interval.
    assert report.totals.ci_low < 0.25 < report.totals.ci_high

    by_source = {b.key: b for b in report.by_source}
    assert by_source["greenhouse"].applied == 2
    assert by_source["greenhouse"].response_rate == 0.5
    assert by_source["remoteok"].response_rate == 0.0

    by_company = {b.key: b for b in report.by_company}
    assert by_company["GitLab"].responded == 1
    assert by_company["Supabase"].applied == 2

    by_comp = {b.key: b for b in report.by_comp_band}
    assert by_comp["200-249k"].applied == 2
    assert by_comp["<160k"].applied == 1
    assert by_comp["undisclosed"].applied == 1


def test_comp_band_edges():
    assert comp_band(None, None) == "undisclosed"
    assert comp_band(120_000, 159_000) == "<160k"
    assert comp_band(160_000, 160_000) == "160-199k"
    assert comp_band(None, 200_000) == "200-249k"
    assert comp_band(250_000, None) == "250-299k"
    assert comp_band(400_000, 500_000) == "300k+"


def test_breaks_conversion_down_by_variant_and_positioning_angle():
    entries = [
        _entry(1, variant="outcome-first", replied_days=3),
        _entry(2, variant="outcome-first"),
        _entry(3, variant="systems-depth"),
        _entry(4),  # untagged
    ]
    report = conversion_report(entries)
    by_variant = {b.key: b for b in report.by_variant}
    assert by_variant["outcome-first"].applied == 2
    assert by_variant["outcome-first"].responded == 1
    assert by_variant["unassigned"].applied == 1
    by_angle = {b.key: b for b in report.by_positioning_angle}
    assert set(by_angle) == {"outcome-first", "systems-depth"}


def test_sections_are_ordered_by_volume_not_by_flattering_rate():
    """A 1/1 company must not top the table over a 4/20 one."""
    entries = [_entry(1, company="Lucky", replied_days=2)]
    entries += [_entry(i + 2, company="Real",
                       replied_days=2 if i < 4 else None) for i in range(20)]
    report = conversion_report(entries)
    assert [b.key for b in report.by_company] == ["Real", "Lucky"]


# --- time to first response ---

def test_time_to_first_response_reports_median_and_what_is_still_open():
    entries = [
        _entry(1, replied_days=2),
        _entry(2, replied_days=6),
        _entry(3, replied_days=10),
        _entry(4),                       # still silent
        _entry(5, outcome="ghosted"),    # silent and written off
    ]
    report = conversion_report(entries)
    ttfr = report.time_to_first_response
    assert ttfr.n == 3
    assert ttfr.median_days == 6.0
    assert ttfr.fastest_days == 2.0
    assert ttfr.slowest_days == 10.0
    assert ttfr.awaiting == 2


def test_time_to_first_response_on_one_reply_is_reported_as_one_reply():
    report = conversion_report([_entry(1, replied_days=4), _entry(2)])
    ttfr = report.time_to_first_response
    assert ttfr.n == 1
    assert ttfr.median_days == 4.0
    assert "1 repl" in ttfr.note


# --- recurring gaps ---

def test_gaps_recur_only_from_applications_that_were_rejected():
    entries = [
        _entry(1, outcome="rejection", gaps=["Kubernetes", "Rust"]),
        _entry(2, outcome="rejection", gaps=["Kubernetes"]),
        _entry(3, replied_days=3, gaps=["Kubernetes", "Terraform"]),
        _entry(4, gaps=["Rust"]),  # no outcome yet — says nothing
    ]
    report = conversion_report(entries)
    assert report.recurring_gaps[0] == ("Kubernetes", 2)
    assert dict(report.recurring_gaps).get("Terraform") is None
    assert dict(report.recurring_gaps).get("Rust") == 1


# --- the promotion verdict rides along ---

def test_the_report_carries_the_promotion_verdict_and_refuses_on_noise():
    entries = [_entry(i, variant="outcome-first", replied_days=1)
               for i in range(1, 4)]
    entries += [_entry(i, variant="systems-depth") for i in range(4, 7)]
    report = conversion_report(entries)
    assert report.promotion.status == "insufficient_data"
    assert report.promotion.winner is None
