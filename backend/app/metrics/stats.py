"""Descriptive and inferential statistics used by metrics and experiment comparison."""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (same method as numpy's default), p in [0, 100]."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


@dataclass(frozen=True)
class Summary:
    count: int
    mean: float | None
    median: float | None
    p95: float | None
    p99: float | None
    min: float | None
    max: float | None


def summarize(values: Sequence[float | Decimal]) -> Summary:
    vals = [float(v) for v in values]
    if not vals:
        return Summary(0, None, None, None, None, None, None)
    return Summary(
        count=len(vals),
        mean=sum(vals) / len(vals),
        median=percentile(vals, 50),
        p95=percentile(vals, 95),
        p99=percentile(vals, 99),
        min=min(vals),
        max=max(vals),
    )


def percent_change(baseline: float, candidate: float) -> float | None:
    """(candidate - baseline) / baseline x 100. None when the baseline is zero."""
    if baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def bootstrap_mean_ci(
    values: Sequence[float], confidence: float = 0.95, resamples: int = 2000, seed: int = 0
) -> tuple[float, float] | None:
    """Percentile-bootstrap confidence interval for the mean of `values`.

    Used on *paired differences* (candidate - baseline, per test case), which removes
    case-to-case difficulty from the comparison. The RNG is seeded so the same data always
    yields the same interval (reproducible experiment reports). None when fewer than two
    values: a single observation says nothing about variability.
    """
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    alpha = (1 - confidence) / 2
    return (percentile(means, alpha * 100), percentile(means, (1 - alpha) * 100))
