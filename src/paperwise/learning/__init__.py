"""PaperWise Experience / Strategy Learning (P3) + Learning Validation (P3.5).

从执行轨迹与审查结果中提取可复用的经验，并验证经验是否真正带来改善：

- ``signals.py``: LearningSignal — Reviewer / Trace 产生的结构化学习信号
- ``failure_patterns.py``: FailurePatternExtractor — 从 AgentTrace 聚合失败模式
- ``strategy_library.py``: StrategyLibrary — 策略库，驱动后续 Plan 组合
- ``strategy_evaluator.py``: StrategyEvaluator — A/B 验证策略收益（P3.5）
"""

from paperwise.learning.signals import LearningSignal, LearningSignalGenerator
from paperwise.learning.failure_patterns import FailurePattern, FailurePatternExtractor
from paperwise.learning.strategy_library import Strategy, StrategyLibrary
from paperwise.learning.strategy_evaluator import (
    StrategyEvalOutcome,
    StrategyEvalReport,
    StrategyEvaluator,
)

__all__ = [
    "LearningSignal",
    "LearningSignalGenerator",
    "FailurePattern",
    "FailurePatternExtractor",
    "Strategy",
    "StrategyLibrary",
    "StrategyEvalOutcome",
    "StrategyEvalReport",
    "StrategyEvaluator",
]
