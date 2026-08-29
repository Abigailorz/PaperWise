"""PaperWise Experience / Strategy Learning (P3).

从执行轨迹与审查结果中提取可复用的经验：

- ``signals.py``: LearningSignal — Reviewer / Trace 产生的结构化学习信号
- ``failure_patterns.py``: FailurePatternExtractor — 从 AgentTrace 聚合失败模式
- ``strategy_library.py``: StrategyLibrary — 策略库，驱动后续 Plan 组合
"""

from paperwise.learning.signals import LearningSignal, LearningSignalGenerator
from paperwise.learning.failure_patterns import FailurePattern, FailurePatternExtractor
from paperwise.learning.strategy_library import Strategy, StrategyLibrary

__all__ = [
    "LearningSignal",
    "LearningSignalGenerator",
    "FailurePattern",
    "FailurePatternExtractor",
    "Strategy",
    "StrategyLibrary",
]
