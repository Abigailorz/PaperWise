"""Strategy evaluation — P3.5 Learning Validation 的核心。

回答一个问题：P3 学到的策略**是否真的让 Agent 变好**？

方法：对同一批任务分别跑 baseline（不应用策略）与 treatment（应用策略），
对比得分得到 ``actual_gain``，并回写 StrategyLibrary。

``run_fn`` 由调用方注入（可以是真实 DAG 执行 + TraceEvaluator 打分，
也可以是测试中的 mock），本模块不依赖 LLM——评测逻辑保持确定性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from paperwise.learning.strategy_library import Strategy, StrategyLibrary


@dataclass
class StrategyEvalOutcome:
    """单次任务执行（应用或不应用策略）的评测结果。"""

    score: float                       # 0.0 ~ 1.0，来自 TraceEvaluator / grader / 自定义
    success: bool = True
    tokens_used: int = 0
    trace_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# run_fn(task, strategy) -> StrategyEvalOutcome；strategy 为 None 表示 baseline
StrategyRunFn = Callable[[str, Optional[Strategy]], StrategyEvalOutcome]


@dataclass
class StrategyEvalReport:
    """一条策略的 A/B 评测报告。"""

    strategy_id: str
    strategy_name: str
    task_type: str
    task_count: int
    baseline_scores: list[float] = field(default_factory=list)
    treatment_scores: list[float] = field(default_factory=list)
    baseline_mean: float = 0.0
    treatment_mean: float = 0.0
    actual_gain: float = 0.0
    expected_gain: float = 0.0
    improved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "task_type": self.task_type,
            "task_count": self.task_count,
            "baseline_scores": list(self.baseline_scores),
            "treatment_scores": list(self.treatment_scores),
            "baseline_mean": self.baseline_mean,
            "treatment_mean": self.treatment_mean,
            "actual_gain": self.actual_gain,
            "expected_gain": self.expected_gain,
            "improved": self.improved,
        }


class StrategyEvaluator:
    """对 StrategyLibrary 中的策略做 A/B 验证。"""

    def __init__(self, library: StrategyLibrary, min_gain: float = 0.0):
        self.library = library
        self.min_gain = min_gain

    def evaluate(
        self,
        strategy: Strategy,
        tasks: list[str],
        run_fn: StrategyRunFn,
        expected_gain: Optional[float] = None,
    ) -> StrategyEvalReport:
        """A/B 评测一条策略并回写 actual_gain。

        每个任务跑两次：strategy=None（baseline）与 strategy=treatment。
        actual_gain = mean(treatment) - mean(baseline)。
        """
        if not tasks:
            raise ValueError("tasks must not be empty")

        baseline_scores = [run_fn(task, None).score for task in tasks]
        treatment_scores = [run_fn(task, strategy).score for task in tasks]

        baseline_mean = sum(baseline_scores) / len(baseline_scores)
        treatment_mean = sum(treatment_scores) / len(treatment_scores)
        actual_gain = treatment_mean - baseline_mean
        if expected_gain is None:
            expected_gain = strategy.expected_gain

        report = StrategyEvalReport(
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            task_type=strategy.task_type,
            task_count=len(tasks),
            baseline_scores=baseline_scores,
            treatment_scores=treatment_scores,
            baseline_mean=baseline_mean,
            treatment_mean=treatment_mean,
            actual_gain=actual_gain,
            expected_gain=expected_gain,
            improved=actual_gain > self.min_gain,
        )
        self.library.record_evaluation(
            strategy.strategy_id,
            actual_gain=actual_gain,
            expected_gain=expected_gain,
        )
        return report
