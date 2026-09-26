"""Unit tests for paired comparison, significance and the acceptance verdict."""

import random
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.experiments.compare import Obs, compare
from app.experiments.criteria import AcceptanceCriteria
from app.metrics.stats import bootstrap_mean_ci

rng = random.Random(42)


def obs(quality=0.9, cost="0.076", latency=8700.0, ok=True, tools=5, passed=None, fails=0):
    return Obs(
        succeeded=ok,
        quality=quality,
        passed=(quality >= 0.75 if quality is not None else None) if passed is None else passed,
        cost=None if cost is None else Decimal(cost),
        latency_ms=latency,
        llm_calls=3,
        tool_calls=tools,
        tool_failures=fails,
        tokens=1000,
    )


def optimized_pairs(n=20, candidate_ok=lambda i: True):
    """Baseline vs a candidate ~62% cheaper, ~50% faster, ~1 point lower quality."""
    pairs = []
    for i in range(n):
        q = 0.85 + rng.uniform(0, 0.1)
        pairs.append(
            (
                obs(
                    quality=q,
                    cost=f"{0.076 + rng.uniform(-0.005, 0.005):.6f}",
                    latency=8700 + rng.uniform(-500, 500),
                ),
                obs(
                    quality=q - 0.01 + rng.uniform(-0.005, 0.005),
                    cost=f"{0.029 + rng.uniform(-0.002, 0.002):.6f}",
                    latency=4300 + rng.uniform(-300, 300),
                    ok=candidate_ok(i),
                    tools=3,
                ),
            )
        )
    return pairs


PLAN_CRITERIA = AcceptanceCriteria(
    min_quality=0.8,
    max_quality_drop_points=2,
    min_cost_reduction_pct=20,
    min_latency_reduction_pct=15,
    max_error_rate=0.03,
)


def test_clear_optimization_passes():
    result = compare(optimized_pairs(), PLAN_CRITERIA)
    assert result["verdict"] == "PASS", result["reasons"]
    m = result["metrics"]
    assert m["cost_per_run"]["change_pct"] == pytest.approx(-61.8, abs=2)
    assert m["latency_ms"]["change_pct"]["mean"] == pytest.approx(-50.6, abs=2)
    assert m["quality"]["change_points"] == pytest.approx(-1.0, abs=0.5)
    assert m["tool_calls_per_run"]["change_pct"] == pytest.approx(-40)
    assert all(c["status"] == "PASS" for c in result["checks"])


def test_error_rate_breach_fails_even_when_cheaper_and_faster():
    # plan §17 example: cost and latency improve, but errors exceed the threshold.
    pairs = optimized_pairs(n=25, candidate_ok=lambda i: i != 3)  # 4% error rate
    result = compare(pairs, PLAN_CRITERIA)
    assert result["verdict"] == "FAIL"
    assert result["reasons"] == ["candidate error rate 0.040"]
    by_name = {c["name"]: c["status"] for c in result["checks"]}
    assert by_name["min_cost_reduction"] == "PASS"
    assert by_name["max_error_rate"] == "FAIL"


def test_quality_drop_beyond_limit_fails():
    pairs = [(obs(quality=0.92), obs(quality=0.85, cost="0.02")) for _ in range(20)]
    result = compare(pairs, AcceptanceCriteria(max_quality_drop_points=2))
    assert result["verdict"] == "FAIL"
    assert "quality dropped 7.00 points" in result["reasons"][0]


def test_small_sample_is_inconclusive_not_pass():
    result = compare(optimized_pairs(n=5), PLAN_CRITERIA)
    assert result["verdict"] == "INCONCLUSIVE"
    assert "only 5 paired runs" in result["reasons"][-1]


def test_noisy_cost_saving_is_inconclusive_unless_significance_disabled():
    # Mean saving ~25%, but per-case differences swing wildly in both directions.
    pairs = []
    for i in range(12):
        base = 0.10
        cand = 0.075 + (0.12 if i % 2 else -0.12) * (0.5 + i / 24)
        pairs.append((obs(cost=f"{base}"), obs(cost=f"{max(cand, 0.001):.6f}")))
    strict = compare(pairs, AcceptanceCriteria(min_cost_reduction_pct=5))
    assert strict["verdict"] == "INCONCLUSIVE"
    assert "confidence interval" in strict["reasons"][0]

    lax = compare(pairs, AcceptanceCriteria(min_cost_reduction_pct=5, require_significance=False))
    assert lax["verdict"] == "PASS"


def test_quality_non_inferiority_needs_ci_within_margin():
    # Average drop 1 point (within a 2-point margin), but so noisy that a >2-point drop
    # cannot be ruled out.
    pairs = [(obs(quality=0.8), obs(quality=0.8 + (0.2 if i % 2 else -0.22))) for i in range(12)]
    result = compare(pairs, AcceptanceCriteria(max_quality_drop_points=2))
    assert result["metrics"]["quality"]["change_points"] == pytest.approx(-1.0)
    assert result["verdict"] == "INCONCLUSIVE"
    assert "cannot rule out a larger drop" in result["reasons"][0]


def test_missing_cost_data_is_inconclusive():
    pairs = [(obs(cost=None), obs(cost="0.01")) for _ in range(20)]
    result = compare(pairs, AcceptanceCriteria(min_cost_reduction_pct=10))
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["metrics"]["cost_per_run"]["n"] == 0


def test_latency_ignores_failed_runs_and_supports_stat_choice():
    pairs = [(obs(latency=1000), obs(latency=500)) for _ in range(12)]
    pairs.append((obs(latency=1000), obs(latency=1, ok=False)))  # fast failure ignored
    result = compare(pairs, AcceptanceCriteria(min_latency_reduction_pct=40, latency_stat="median"))
    assert result["metrics"]["latency_ms"]["n"] == 12
    assert result["metrics"]["latency_ms"]["change_pct"]["median"] == pytest.approx(-50)


def test_comparison_is_deterministic():
    pairs = optimized_pairs()
    assert compare(pairs, PLAN_CRITERIA) == compare(pairs, PLAN_CRITERIA)


def test_criteria_validation():
    with pytest.raises(ValidationError, match="at least one acceptance threshold"):
        AcceptanceCriteria()
    with pytest.raises(ValidationError):
        AcceptanceCriteria(max_error_rate=0.1, typo_field=1)
    with pytest.raises(ValidationError):
        AcceptanceCriteria(min_quality=1.5)


def test_bootstrap_ci():
    diffs = [rng.gauss(-2, 1) for _ in range(200)]
    lo, hi = bootstrap_mean_ci(diffs)
    assert lo < sum(diffs) / len(diffs) < hi
    assert hi < 0
    assert bootstrap_mean_ci(diffs) == (lo, hi)  # seeded: reproducible
    assert bootstrap_mean_ci([1.0]) is None
    assert bootstrap_mean_ci([0.5, 0.5, 0.5]) == (0.5, 0.5)
