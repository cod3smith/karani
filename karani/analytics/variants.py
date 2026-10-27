"""Strategy variants: named arms, and the rule for calling one a winner.

The mental model is a bandit over a handful of arms, not an agent that
rewrites its own prompts. An arm is a *named* way of applying — a
positioning angle, a draft-prompt variant, a resume ordering — recorded
on every application that used it so outcomes attach to something
comparable.

Promotion is deliberately hard to trigger. Two gates:

1. **A stated minimum sample per arm.** Below it the report says
   "insufficient data" and names the shortfall. The default is
   `MIN_SAMPLE_PER_ARM` applications on each of at least two arms.
2. **Non-overlapping 95% Wilson intervals** between the leader and
   every other eligible arm. This is stricter than a two-proportion
   test on purpose: the cost of running an experiment one more month
   is a month, and the cost of acting on noise is every application
   after it.

At ~30 applications a month over two arms, gate 1 alone is roughly six
weeks. That is the honest timeline from the design doc, and nothing
here pretends otherwise.
"""
from __future__ import annotations

from .models import UNASSIGNED, ArmStats, PromotionReport
from .stats import intervals_overlap, proportion, wilson_interval

# The stated minimum. Applications per arm, not in total.
MIN_SAMPLE_PER_ARM = 20


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def promotion_report(
    arms: dict[str, dict],
    *,
    min_sample_per_arm: int = MIN_SAMPLE_PER_ARM,
    guidance: dict[str, str] | None = None,
) -> PromotionReport:
    """Rank arms and decide whether any of them has earned a promotion.

    `arms` maps a variant name to `{"applied": n, "responded": k}`. The
    `UNASSIGNED` bucket is dropped before ranking — untagged
    applications varied nothing, so they cannot win an experiment,
    however many of them replied.
    """
    guidance = guidance or {}
    total_applied = sum(int(c.get("applied", 0)) for c in arms.values())
    contenders = {name: counts for name, counts in arms.items()
                  if name and name != UNASSIGNED}

    if total_applied == 0:
        return PromotionReport(
            status="no_data",
            min_sample_per_arm=min_sample_per_arm,
            headline="No applications on record yet.",
            detail=("The ledger is empty, so there is nothing to compare. "
                    f"Promotion needs {min_sample_per_arm} applications on "
                    "each of at least two variants."),
        )

    if not contenders:
        return PromotionReport(
            status="insufficient_data",
            min_sample_per_arm=min_sample_per_arm,
            headline=f"{total_applied} application(s) sent, none tagged with "
                     "a strategy variant.",
            detail=("Outcomes cannot be attributed to an angle that was "
                    "never recorded. Register variants and tag "
                    "applications, then this needs "
                    f"{min_sample_per_arm} per arm."),
        )

    stats: list[ArmStats] = []
    for name, counts in contenders.items():
        applied = int(counts.get("applied", 0))
        responded = int(counts.get("responded", 0))
        low, high = wilson_interval(responded, applied)
        stats.append(ArmStats(
            name=name,
            applied=applied,
            responded=responded,
            response_rate=round(proportion(responded, applied), 4),
            ci_low=low, ci_high=high,
            eligible=applied >= min_sample_per_arm,
            has_guidance=bool(guidance.get(name, "x")),
        ))
    stats.sort(key=lambda a: (-a.response_rate, -a.applied, a.name))

    eligible = [a for a in stats if a.eligible]
    if len(eligible) < 2:
        best = max(stats, key=lambda a: a.applied)
        shortfall = max(0, min_sample_per_arm - best.applied)
        return PromotionReport(
            status="insufficient_data",
            arms=stats,
            min_sample_per_arm=min_sample_per_arm,
            headline=(f"Not enough data to name a winner — "
                      f"{len(eligible)} of {len(stats)} variant(s) have "
                      f"reached {min_sample_per_arm} applications."),
            detail=(
                f"Promotion requires at least {min_sample_per_arm} "
                f"applications on each of two variants. The best-sampled "
                f"arm, '{best.name}', has {best.applied}"
                + (f" — {shortfall} short." if shortfall
                   else " — a second arm needs to catch up.")
                + " Rates below are reported for information only; at this "
                  "sample size they are not distinguishable from chance."
            ),
        )

    leader, *rest = eligible
    overlapping = [a for a in rest
                   if intervals_overlap(leader.interval, a.interval)]
    leader_ci = f"95% CI {_pct(leader.ci_low)}-{_pct(leader.ci_high)}"
    if overlapping:
        closest = overlapping[0]
        return PromotionReport(
            status="no_clear_winner",
            arms=stats,
            min_sample_per_arm=min_sample_per_arm,
            headline=(f"'{leader.name}' leads at {_pct(leader.response_rate)} "
                      f"({leader_ci}, n={leader.applied}), but the lead is "
                      "not yet distinguishable from noise."),
            detail=(
                f"Its interval overlaps '{closest.name}' "
                f"({_pct(closest.response_rate)}, 95% CI "
                f"{_pct(closest.ci_low)}-{_pct(closest.ci_high)}, "
                f"n={closest.applied}). Overlapping intervals mean the "
                f"difference could be sampling noise. Keep both arms "
                f"running; the minimum of {min_sample_per_arm} per arm is "
                "a floor, not a finish line."
            ),
        )

    runner_up = rest[0]
    return PromotionReport(
        status="promote",
        winner=leader.name,
        arms=stats,
        min_sample_per_arm=min_sample_per_arm,
        headline=(f"Promote '{leader.name}': {_pct(leader.response_rate)} "
                  f"reply rate ({leader_ci}, n={leader.applied}), clear of "
                  "every other arm."),
        detail=(
            f"Runner-up '{runner_up.name}' is at "
            f"{_pct(runner_up.response_rate)} (95% CI "
            f"{_pct(runner_up.ci_low)}-{_pct(runner_up.ci_high)}, "
            f"n={runner_up.applied}); the intervals do not overlap, so the "
            f"gap is unlikely to be sampling noise. Minimum sample per arm "
            f"was {min_sample_per_arm}. This says the winning arm gets more "
            "replies — it says nothing about who gets hired."
        ),
    )


__all__ = ["MIN_SAMPLE_PER_ARM", "promotion_report"]
