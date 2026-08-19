"""Statistical significance analysis for agent evaluation results.

Implements standard error, confidence intervals, and basic paired tests
following Chapter 6.7 of "深入理解 AI Agent".
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def standard_error(p: float, n: int) -> float:
    """Standard error of a binomial proportion.

    Args:
        p: observed success rate
        n: number of trials

    Returns:
        Standard error SE(p)
    """
    if n <= 0:
        return 0.0
    return math.sqrt(p * (1 - p) / n)


def confidence_interval(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a binomial proportion.

    Args:
        p: observed success rate
        n: number of trials
        z: z-score for desired confidence (default 1.96 for 95%)

    Returns:
        (lower_bound, upper_bound)
    """
    se = standard_error(p, n)
    return (max(0.0, p - z * se), min(1.0, p + z * se))


def is_significant(
    p1: float, n1: int,
    p2: float, n2: int,
    threshold: float = 1.96,
) -> bool:
    """Check if the difference between two success rates is statistically significant.

    Uses a simple z-test for two proportions (unpaired).

    Args:
        p1, n1: success rate and trials for configuration 1
        p2, n2: success rate and trials for configuration 2
        threshold: z-score threshold (default 1.96 for p < 0.05)

    Returns:
        True if the difference is statistically significant
    """
    if n1 <= 0 or n2 <= 0:
        return False
    pooled_p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se_diff = math.sqrt(pooled_p * (1 - pooled_p) * (1 / n1 + 1 / n2))
    if se_diff == 0:
        return False
    z = abs(p1 - p2) / se_diff
    return z > threshold


def mcnemar_test(wins_a: int, wins_b: int) -> tuple[float, bool]:
    """McNemar's test for paired nominal data.

    Use when the same tasks are run under two configurations.

    Args:
        wins_a: number of tasks where only configuration A passed
        wins_b: number of tasks where only configuration B passed

    Returns:
        (chi2 statistic, is_significant at p < 0.05)
    """
    n = wins_a + wins_b
    if n == 0:
        return 0.0, False
    chi2 = (abs(wins_a - wins_b) - 1) ** 2 / n
    # chi2 > 3.84 corresponds to p < 0.05 with 1 degree of freedom
    return chi2, chi2 > 3.84


def paired_bootstrap(
    outcomes_a: Sequence[bool],
    outcomes_b: Sequence[bool],
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> dict[str, float]:
    """Paired bootstrap test for two configurations on the same tasks.

    Args:
        outcomes_a: binary outcomes for configuration A
        outcomes_b: binary outcomes for configuration B
        n_bootstrap: number of bootstrap resamples
        seed: random seed for reproducibility

    Returns:
        dict with keys: mean_diff, se, ci_lower, ci_upper, p_value_approx
    """
    import random

    if seed is not None:
        random.seed(seed)

    if len(outcomes_a) != len(outcomes_b) or not outcomes_a:
        return {
            "mean_diff": 0.0,
            "se": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "p_value_approx": 1.0,
        }

    n = len(outcomes_a)
    diffs = [int(a) - int(b) for a, b in zip(outcomes_a, outcomes_b)]
    mean_diff = sum(diffs) / n

    bootstrap_diffs = []
    for _ in range(n_bootstrap):
        sample = [random.choice(diffs) for _ in range(n)]
        bootstrap_diffs.append(sum(sample) / n)

    se = math.sqrt(sum((d - mean_diff) ** 2 for d in bootstrap_diffs) / n_bootstrap)
    sorted_diffs = sorted(bootstrap_diffs)
    ci_lower = sorted_diffs[int(0.025 * n_bootstrap)]
    ci_upper = sorted_diffs[int(0.975 * n_bootstrap)]

    # Approximate p-value: proportion of bootstrap diffs crossing zero
    if mean_diff > 0:
        p_value = sum(1 for d in bootstrap_diffs if d <= 0) / n_bootstrap
    else:
        p_value = sum(1 for d in bootstrap_diffs if d >= 0) / n_bootstrap

    return {
        "mean_diff": mean_diff,
        "se": se,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "p_value_approx": min(p_value * 2, 1.0),  # two-sided
    }


def pass_at_k(p: float, k: int) -> float:
    """Compute Pass@k given single-run success rate p."""
    return 1 - (1 - p) ** k


def pass_consecutive_k(p: float, k: int) -> float:
    """Compute Pass^k given single-run success rate p."""
    return p ** k


def summarize_runs(
    runs: Iterable[dict],
    k: int = 1,
) -> dict:
    """Summarize a list of trial results with statistical metrics.

    Args:
        runs: list of dicts with at least "passed" key
        k: number of trials per task for pass@k / pass^k computation

    Returns:
        dict with aggregate statistics
    """
    runs = list(runs)
    n = len(runs)
    if n == 0:
        return {"n": 0, "success_rate": 0.0}

    passed = sum(1 for r in runs if r.get("passed"))
    p = passed / n

    return {
        "n": n,
        "passed": passed,
        "success_rate": round(p, 4),
        "standard_error": round(standard_error(p, n), 4),
        "confidence_interval": [
            round(ci, 4) for ci in confidence_interval(p, n)
        ],
        "pass_at_k": round(pass_at_k(p, k), 4),
        "pass_consecutive_k": round(pass_consecutive_k(p, k), 4),
    }


__all__ = [
    "standard_error",
    "confidence_interval",
    "is_significant",
    "mcnemar_test",
    "paired_bootstrap",
    "pass_at_k",
    "pass_consecutive_k",
    "summarize_runs",
]
