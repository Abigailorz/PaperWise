"""Tests for P3.5 strategy A/B evaluation and validation fields."""

import pytest

from paperwise.learning.strategy_evaluator import (
    StrategyEvalOutcome,
    StrategyEvaluator,
)
from paperwise.learning.strategy_library import Strategy, StrategyLibrary


def _library(tmp_path) -> StrategyLibrary:
    return StrategyLibrary(tmp_path / "strategies")


def test_strategy_validation_fields_default_and_roundtrip(tmp_path):
    strat = Strategy(task_type="analysis", name="s")
    assert strat.success_count == 0
    assert strat.failure_count == 0
    assert strat.expected_gain == 0.0
    assert strat.actual_gain == 0.0

    restored = Strategy.from_dict(strat.to_dict())
    assert restored.success_count == 0
    assert restored.expected_gain == 0.0


def test_confidence_prior_without_observations():
    strat = Strategy(task_type="analysis", name="s")
    assert abs(strat.confidence - 0.5) < 1e-9  # Laplace 先验


def test_confidence_grows_with_successes():
    strat = Strategy(task_type="analysis", name="s", success_count=4, failure_count=0)
    assert abs(strat.confidence - 5 / 6) < 1e-9
    strat2 = Strategy(task_type="analysis", name="s", success_count=1, failure_count=3)
    assert abs(strat2.confidence - 2 / 6) < 1e-9


def test_record_outcome_tracks_counts(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="s"))
    lib.record_outcome(strat.strategy_id, success=True)
    lib.record_outcome(strat.strategy_id, success=False)

    updated = lib.get(strat.strategy_id)
    assert updated.success_count == 1
    assert updated.failure_count == 1
    assert updated.use_count == 2


def test_select_demotes_unproven_strategies(tmp_path):
    """validated 策略应排在同 success_rate 的未验证策略之前。"""
    lib = _library(tmp_path)
    proven = lib.add_or_update(Strategy(
        task_type="analysis", name="proven",
        success_rate=0.9, use_count=4, success_count=4,
    ))
    lib.add_or_update(Strategy(
        task_type="analysis", name="unproven",
        success_rate=0.9, use_count=4,  # 无验证计数
    ))

    selected = lib.select("analysis")
    assert selected[0].strategy_id == proven.strategy_id


def test_evaluator_computes_gain_and_writes_back(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="verify-numerics"))
    evaluator = StrategyEvaluator(lib)

    def run_fn(task, strategy):
        # 模拟：应用策略后得分从 0.5 提升到 0.8
        return StrategyEvalOutcome(score=0.8 if strategy else 0.5, success=True)

    report = evaluator.evaluate(strat, ["task a", "task b"], run_fn)

    assert report.task_count == 2
    assert abs(report.baseline_mean - 0.5) < 1e-9
    assert abs(report.treatment_mean - 0.8) < 1e-9
    assert abs(report.actual_gain - 0.3) < 1e-9
    assert report.improved

    updated = lib.get(strat.strategy_id)
    assert abs(updated.actual_gain - 0.3) < 1e-9


def test_evaluator_negative_gain_marks_not_improved(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="bad-strategy"))
    evaluator = StrategyEvaluator(lib)

    def run_fn(task, strategy):
        return StrategyEvalOutcome(score=0.4 if strategy else 0.6, success=True)

    report = evaluator.evaluate(strat, ["task a"], run_fn)
    assert not report.improved
    assert report.actual_gain < 0


def test_evaluator_respects_min_gain(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="marginal"))
    evaluator = StrategyEvaluator(lib, min_gain=0.1)

    def run_fn(task, strategy):
        return StrategyEvalOutcome(score=0.55 if strategy else 0.5, success=True)

    report = evaluator.evaluate(strat, ["task a"], run_fn)
    assert report.actual_gain > 0
    assert not report.improved  # 0.05 < min_gain 0.1


def test_evaluator_rejects_empty_tasks(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="s"))
    evaluator = StrategyEvaluator(lib)
    with pytest.raises(ValueError):
        evaluator.evaluate(strat, [], lambda t, s: StrategyEvalOutcome(score=1.0))


def test_record_evaluation_updates_expected_gain(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="s"))
    lib.record_evaluation(strat.strategy_id, actual_gain=0.25, expected_gain=0.2)

    updated = lib.get(strat.strategy_id)
    assert abs(updated.actual_gain - 0.25) < 1e-9
    assert abs(updated.expected_gain - 0.2) < 1e-9
