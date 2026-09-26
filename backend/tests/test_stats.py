import pytest

from app.metrics.stats import percent_change, percentile, rate, summarize


def test_percentile_matches_linear_interpolation():
    values = list(range(1, 11))  # 1..10
    assert percentile(values, 50) == pytest.approx(5.5)
    assert percentile(values, 95) == pytest.approx(9.55)
    assert percentile(values, 99) == pytest.approx(9.91)
    assert percentile(values, 0) == 1 and percentile(values, 100) == 10


def test_percentile_is_order_independent_and_handles_single_value():
    assert percentile([10, 1, 5], 50) == 5
    assert percentile([7], 99) == 7


def test_percentile_rejects_bad_input():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_p95_exposes_tail_that_mean_hides():
    latencies = [100] * 95 + [5000] * 5
    s = summarize(latencies)
    assert s.median == 100
    assert s.mean == pytest.approx(345)
    assert s.p99 == 5000


def test_summarize_empty():
    s = summarize([])
    assert s.count == 0 and s.mean is None and s.p95 is None


def test_percent_change_and_rate():
    assert percent_change(0.076, 0.029) == pytest.approx(-61.842, abs=1e-3)
    assert percent_change(8.7, 4.3) == pytest.approx(-50.575, abs=1e-3)
    assert percent_change(0, 1) is None
    assert rate(3, 4) == 0.75
    assert rate(0, 0) is None
