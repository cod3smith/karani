"""Small-sample statistics, kept deliberately boring.

Every number karani reports about conversion comes from a handful of
applications. The naive thing — dividing responses by applications and
calling the bigger number better — is how you end up "promoting" a
positioning angle that won three coin flips.

So proportions are always reported with a Wilson score interval. It is
the right tool for a binomial rate with a tiny `n`: unlike the normal
approximation it stays inside [0, 1], it does not collapse to a point
at 0/n or n/n, and at n=3 it is honestly enormous. That width IS the
message, and the callers here are built to show it rather than hide it.
"""
from __future__ import annotations

from math import sqrt

# 95% two-sided normal quantile. Hard-coded rather than imported from
# scipy: one constant is not worth a dependency.
Z_95 = 1.959963984540054


def proportion(successes: int, trials: int) -> float:
    """Point estimate. Zero trials is 0.0, not a divide-by-zero."""
    return (successes / trials) if trials else 0.0


def wilson_interval(successes: int, trials: int,
                    z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    With no trials the honest answer is "the rate could be anything",
    so this returns the whole unit interval rather than (0, 0).
    """
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denom
    halfwidth = (z / denom) * sqrt(p * (1 - p) / trials
                                   + z2 / (4 * trials * trials))
    return (max(0.0, round(centre - halfwidth, 4)),
            min(1.0, round(centre + halfwidth, 4)))


def intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True when two confidence intervals cannot be told apart.

    Non-overlap is a conservative test for a real difference — more
    conservative than a two-proportion z-test, which is the point. We
    would rather keep running an experiment than act on noise.
    """
    return not (a[1] < b[0] or b[1] < a[0])


def median(values: list[float]) -> float | None:
    """Median, or None for an empty sample. No silent zeros."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered),
                      int(round(pct / 100.0 * len(ordered) + 0.5))))
    return float(ordered[rank - 1])


__all__ = ["Z_95", "proportion", "wilson_interval", "intervals_overlap",
           "median", "percentile"]
