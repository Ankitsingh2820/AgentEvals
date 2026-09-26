"""Paired comparison of two experiment arms, and the acceptance decision.

Runs are paired by (dataset case, repetition): both arms saw exactly the same input.
Comparing paired differences cancels out how hard each case is, so real differences show
up with far fewer cases than comparing two unpaired averages would need.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.experiments.criteria import AcceptanceCriteria
from app.metrics.stats import bootstrap_mean_ci, percent_change, rate, summarize

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"


@dataclass(frozen=True)
class Obs:
    """One run's measurements."""

    succeeded: bool
    quality: float | None  # None when no evaluator produced a score
    passed: bool | None
    cost: Decimal | None  # None unless the run's cost is complete
    latency_ms: float | None
    llm_calls: int
    tool_calls: int
    tool_failures: int
    tokens: int
    retries: int = 0


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _paired(pairs, get) -> tuple[list[float], list[float]]:
    b, c = [], []
    for base, cand in pairs:
        vb, vc = get(base), get(cand)
        if vb is not None and vc is not None:
            b.append(float(vb))
            c.append(float(vc))
    return b, c


def _ci(b, c, confidence):
    return bootstrap_mean_ci([y - x for x, y in zip(b, c, strict=True)], confidence)


def _scaled(ci, factor):
    return None if ci is None else [ci[0] * factor, ci[1] * factor]


def _pct_ci(ci, baseline_mean):
    if ci is None or not baseline_mean:
        return None
    return [ci[0] / baseline_mean * 100, ci[1] / baseline_mean * 100]


def compare(pairs: list[tuple[Obs, Obs]], criteria: AcceptanceCriteria) -> dict[str, Any]:
    conf = criteria.confidence
    metrics: dict[str, Any] = {}

    # Quality: mean evaluator score, in points (0-100).
    qb, qc = _paired(pairs, lambda o: o.quality)
    q_ci = _ci(qb, qc, conf)
    metrics["quality"] = {
        "n": len(qb),
        "baseline": _mean(qb),
        "candidate": _mean(qc),
        "change_points": None if not qb else (_mean(qc) - _mean(qb)) * 100,
        "change_points_ci": _scaled(q_ci, 100),
    }

    pb, pc = _paired(pairs, lambda o: None if o.passed is None else float(o.passed))
    metrics["pass_rate"] = {
        "n": len(pb),
        "baseline": _mean(pb),
        "candidate": _mean(pc),
        "change_points": None if not pb else (_mean(pc) - _mean(pb)) * 100,
    }

    # Cost: only pairs where both runs are fully priced.
    cb, cc = _paired(pairs, lambda o: o.cost)
    c_ci = _ci(cb, cc, conf)
    metrics["cost_per_run"] = {
        "n": len(cb),
        "baseline": _mean(cb),
        "candidate": _mean(cc),
        "baseline_total": sum(cb),
        "candidate_total": sum(cc),
        "change_pct": percent_change(_mean(cb), _mean(cc)) if cb else None,
        "diff_ci": list(c_ci) if c_ci else None,
        "change_pct_ci": _pct_ci(c_ci, _mean(cb)),
        "baseline_range": [min(cb), max(cb)] if cb else None,
        "candidate_range": [min(cc), max(cc)] if cc else None,
    }

    # Latency: only pairs where both runs succeeded (a fast failure is not "faster").
    lb, lc = _paired(pairs, lambda o: o.latency_ms if o.succeeded else None)
    l_ci = _ci(lb, lc, conf)
    sb, sc = summarize(lb), summarize(lc)
    metrics["latency_ms"] = {
        "n": len(lb),
        "baseline": {k: getattr(sb, k) for k in ("mean", "median", "p95", "p99")},
        "candidate": {k: getattr(sc, k) for k in ("mean", "median", "p95", "p99")},
        "change_pct": {
            k: percent_change(getattr(sb, k), getattr(sc, k)) if lb else None
            for k in ("mean", "median", "p95", "p99")
        },
        "mean_diff_ci": list(l_ci) if l_ci else None,
        "mean_change_pct_ci": _pct_ci(l_ci, sb.mean),
    }

    n = len(pairs)
    b_ok = sum(b.succeeded for b, _ in pairs)
    c_ok = sum(c.succeeded for _, c in pairs)
    metrics["reliability"] = {
        "n": n,
        "baseline_success_rate": rate(b_ok, n),
        "candidate_success_rate": rate(c_ok, n),
        "baseline_error_rate": rate(n - b_ok, n),
        "candidate_error_rate": rate(n - c_ok, n),
        "baseline_tool_failure_rate": rate(
            sum(b.tool_failures for b, _ in pairs), sum(b.tool_calls for b, _ in pairs)
        ),
        # Retried transient provider failures per run.
        "baseline_retry_rate": rate(sum(b.retries for b, _ in pairs), n),
        "candidate_retry_rate": rate(sum(c.retries for _, c in pairs), n),
        "candidate_tool_failure_rate": rate(
            sum(c.tool_failures for _, c in pairs), sum(c.tool_calls for _, c in pairs)
        ),
    }
    for key in ("llm_calls", "tool_calls", "tokens"):
        b, c = _paired(pairs, lambda o, k=key: getattr(o, k))
        metrics[f"{key}_per_run"] = {
            "baseline": _mean(b),
            "candidate": _mean(c),
            "change_pct": percent_change(_mean(b), _mean(c)) if b and _mean(b) else None,
        }

    checks = _checks(metrics, criteria, qb, lb, cb)
    verdict, reasons = _verdict(checks, n, criteria)
    return {
        "pairs": n,
        "metrics": metrics,
        "checks": checks,
        "verdict": verdict,
        "reasons": reasons,
    }


def _check(name, rule, observed, status, reason):
    return {"name": name, "rule": rule, "observed": observed, "status": status, "reason": reason}


def _checks(m, cr: AcceptanceCriteria, qb, lb, cb) -> list[dict[str, Any]]:
    checks = []
    sig = cr.require_significance
    q = m["quality"]

    if cr.min_quality is not None:
        if not qb:
            checks.append(
                _check("min_quality", f">= {cr.min_quality}", None, INCONCLUSIVE, "no scored runs")
            )
        else:
            ok = q["candidate"] >= cr.min_quality
            checks.append(
                _check(
                    "min_quality",
                    f">= {cr.min_quality}",
                    q["candidate"],
                    PASS if ok else FAIL,
                    f"candidate quality {q['candidate']:.3f}",
                )
            )

    if cr.max_quality_drop_points is not None:
        limit = -cr.max_quality_drop_points
        rule = f"quality change >= {limit} points"
        if not qb:
            checks.append(_check("max_quality_drop", rule, None, INCONCLUSIVE, "no scored runs"))
        elif q["change_points"] < limit:
            checks.append(
                _check(
                    "max_quality_drop",
                    rule,
                    q["change_points"],
                    FAIL,
                    f"quality dropped {-q['change_points']:.2f} points",
                )
            )
        elif sig and (q["change_points_ci"] is None or q["change_points_ci"][0] < limit):
            lo = q["change_points_ci"][0] if q["change_points_ci"] else None
            checks.append(
                _check(
                    "max_quality_drop",
                    rule,
                    q["change_points"],
                    INCONCLUSIVE,
                    "cannot rule out a larger drop: CI lower bound "
                    f"{'n/a' if lo is None else f'{lo:.2f}'} < {limit}",
                )
            )
        else:
            checks.append(
                _check(
                    "max_quality_drop",
                    rule,
                    q["change_points"],
                    PASS,
                    f"quality change {q['change_points']:+.2f} points",
                )
            )

    if cr.min_pass_rate is not None:
        pr = m["pass_rate"]["candidate"]
        checks.append(
            _check(
                "min_pass_rate",
                f">= {cr.min_pass_rate}",
                pr,
                INCONCLUSIVE if pr is None else (PASS if pr >= cr.min_pass_rate else FAIL),
                "no scored runs" if pr is None else f"candidate pass rate {pr:.3f}",
            )
        )

    if cr.min_cost_reduction_pct is not None:
        c = m["cost_per_run"]
        checks.append(
            _reduction(
                "min_cost_reduction",
                cr.min_cost_reduction_pct,
                c["change_pct"],
                c["diff_ci"],
                bool(cb),
                sig,
                "cost",
            )
        )

    if cr.min_latency_reduction_pct is not None:
        lat = m["latency_ms"]
        checks.append(
            _reduction(
                f"min_latency_reduction ({cr.latency_stat})",
                cr.min_latency_reduction_pct,
                lat["change_pct"][cr.latency_stat],
                lat["mean_diff_ci"],
                bool(lb),
                sig,
                "latency",
            )
        )

    if cr.max_error_rate is not None:
        er = m["reliability"]["candidate_error_rate"]
        checks.append(
            _check(
                "max_error_rate",
                f"<= {cr.max_error_rate}",
                er,
                INCONCLUSIVE if er is None else (PASS if er <= cr.max_error_rate else FAIL),
                "no runs" if er is None else f"candidate error rate {er:.3f}",
            )
        )
    return checks


def _reduction(name, min_pct, change_pct, diff_ci, has_data, sig, what):
    rule = f"reduction >= {min_pct}%"
    if not has_data or change_pct is None:
        return _check(name, rule, None, INCONCLUSIVE, f"no comparable {what} data")
    reduction = -change_pct
    if reduction < min_pct:
        return _check(name, rule, reduction, FAIL, f"{what} reduced {reduction:.1f}%")
    if sig and (diff_ci is None or diff_ci[1] >= 0):
        return _check(
            name,
            rule,
            reduction,
            INCONCLUSIVE,
            f"{what} reduced {reduction:.1f}% but the confidence interval of the "
            "paired difference includes no change",
        )
    return _check(name, rule, reduction, PASS, f"{what} reduced {reduction:.1f}%")


def _verdict(checks, n_pairs, cr: AcceptanceCriteria) -> tuple[str, list[str]]:
    failed = [c["reason"] for c in checks if c["status"] == FAIL]
    if failed:
        return FAIL, failed
    reasons = [c["reason"] for c in checks if c["status"] == INCONCLUSIVE]
    if n_pairs < cr.min_pairs:
        reasons.append(f"only {n_pairs} paired runs; at least {cr.min_pairs} required")
    if reasons:
        return INCONCLUSIVE, reasons
    return PASS, ["all acceptance criteria met"]
