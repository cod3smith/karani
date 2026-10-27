"""Honest statistics: Wilson intervals and a promotion rule that refuses.

The failure mode this suite exists to prevent is naming a "winning"
positioning angle off three applications. Every assertion here is about
the system saying "not enough data" when that is the true answer.
"""
from __future__ import annotations

from karani.analytics.stats import wilson_interval
from karani.analytics.variants import MIN_SAMPLE_PER_ARM, promotion_report


# --- Wilson interval ---

def test_wilson_with_no_trials_is_total_ignorance():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_is_wide_on_a_small_sample():
    low, high = wilson_interval(3, 3)
    assert low < 0.5, "3/3 must not claim a rate above 50% with confidence"
    assert high == 1.0


def test_wilson_narrows_as_the_sample_grows():
    small = wilson_interval(15, 30)
    large = wilson_interval(150, 300)
    assert (small[1] - small[0]) > (large[1] - large[0])
    for low, high in (small, large):
        assert low < 0.5 < high


def test_wilson_never_leaves_the_unit_interval():
    for k, n in ((0, 5), (5, 5), (1, 200), (199, 200)):
        low, high = wilson_interval(k, n)
        assert 0.0 <= low <= high <= 1.0


# --- promotion rule ---

def test_no_applications_at_all_reports_no_data():
    report = promotion_report({})
    assert report.status == "no_data"
    assert report.winner is None
    assert "no applications" in report.headline.lower()


def test_three_applications_do_not_promote_a_winner():
    """The pinned behaviour: a perfect 3/3 arm beside a 0/3 arm is noise."""
    report = promotion_report({
        "outcome-first": {"applied": 3, "responded": 3},
        "systems-depth": {"applied": 3, "responded": 0},
    })
    assert report.status == "insufficient_data"
    assert report.winner is None
    assert str(MIN_SAMPLE_PER_ARM) in report.detail
    # The arms are still reported — you can see the counts, just not a verdict.
    assert {a.name for a in report.arms} == {"outcome-first", "systems-depth"}
    assert [a.applied for a in report.arms if a.name == "outcome-first"] == [3]


def test_one_well_sampled_arm_cannot_win_by_default():
    report = promotion_report({
        "outcome-first": {"applied": 60, "responded": 20},
        "systems-depth": {"applied": 2, "responded": 0},
    })
    assert report.status == "insufficient_data"
    assert report.winner is None


def test_overlapping_intervals_report_no_clear_winner():
    report = promotion_report({
        "outcome-first": {"applied": 40, "responded": 10},
        "systems-depth": {"applied": 40, "responded": 8},
    })
    assert report.status == "no_clear_winner"
    assert report.winner is None
    assert "overlap" in report.detail.lower()


def test_a_separated_leader_is_promoted_with_its_interval():
    report = promotion_report({
        "outcome-first": {"applied": 60, "responded": 36},
        "systems-depth": {"applied": 60, "responded": 6},
    })
    assert report.status == "promote"
    assert report.winner == "outcome-first"
    winner = next(a for a in report.arms if a.name == "outcome-first")
    assert winner.response_rate == 0.6
    assert winner.ci_low > 0.4
    # The headline always carries the uncertainty, never a bare rate.
    assert "%" in report.headline and "95%" in report.headline


def test_minimum_sample_is_tunable_but_always_stated():
    arms = {"a": {"applied": 10, "responded": 8},
            "b": {"applied": 10, "responded": 0}}
    strict = promotion_report(arms)
    assert strict.status == "insufficient_data"
    loose = promotion_report(arms, min_sample_per_arm=10)
    assert loose.status == "promote"
    assert loose.min_sample_per_arm == 10
    assert "10" in loose.detail


def test_unlabelled_applications_are_never_an_arm():
    """Applications with no variant tag are a bucket, not a contender."""
    report = promotion_report({
        "unassigned": {"applied": 100, "responded": 50},
        "outcome-first": {"applied": 30, "responded": 3},
    })
    assert report.status == "insufficient_data"
    assert report.winner is None
    assert all(a.name != "unassigned" for a in report.arms)
