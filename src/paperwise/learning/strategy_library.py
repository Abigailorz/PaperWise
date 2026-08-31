"""Strategy library — 可持久化的策略库，驱动 Procedural Memory -> Plan 的闭环。

Strategy 是一条"针对某类任务，应该（或不应该）怎么规划"的经验：
- ``plan_hints``: 规划时应确保存在的节点 id（如 verify_data）
- ``avoid``: 规划时应避免的节点 / 行为
- ``success_rate`` / ``use_count``: 随执行结果滚动更新

信号 -> 策略的映射是确定性规则（Mechanism over prompt）：
只有 critical/major 级别的信号才会生成或强化策略。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from paperwise.learning.signals import LearningSignal, SignalType
from paperwise.memory.storage import create_storage

logger = logging.getLogger("paperwise")


class StrategyLifecycle(str, Enum):
    """P6 Phase E: strategy maturity states. Promotion requires A/B evidence."""

    CANDIDATE = "candidate"          # newly created, no validation
    EXPERIMENTAL = "experimental"    # in A/B testing
    VALIDATED = "validated"          # A/B positive gain confirmed
    TRUSTED = "trusted"              # consistently positive over multiple rounds
    DEPRECATED = "deprecated"        # regression confirmed, no longer used
# signal_type -> (策略名, plan_hints, avoid, 描述)
_SIGNAL_TO_STRATEGY: dict[str, tuple[str, list[str], list[str], str]] = {
    SignalType.HALLUCINATION: (
        "enforce-citations",
        ["expand_evidence"],
        [],
        "审查发现编造内容：确保 expand_evidence 节点强制引用溯源",
    ),
    SignalType.QUALITY_GAP: (
        "ensure-review",
        ["review_report"],
        [],
        "审查发现重大质量问题：确保 review_report 节点存在",
    ),
    SignalType.OMISSION: (
        "expand-coverage",
        ["expand_evidence"],
        [],
        "审查发现内容遗漏：扩展证据覆盖",
    ),
    SignalType.VERIFICATION_GAP: (
        "verify-numerics",
        ["verify_data"],
        [],
        "数值验证不足：确保 verify_data 节点存在",
    ),
    SignalType.NODE_FAILURE: (
        "guard-unstable-node",
        [],
        [],
        "节点反复失败：考虑 replan 或降低对该节点的依赖",
    ),
    SignalType.PLANNING_FAILURE: (
        "stabilize-plan",
        [],
        [],
        "发生过 replan：优先复用历史成功 Plan 骨架",
    ),
}


@dataclass
class Strategy:
    """一条可复用的规划策略。

    P3.5 起增加验证字段：
    - ``success_count`` / ``failure_count``: record_outcome 累积的执行结果计数
    - ``confidence``: 由计数推导的置信度（Laplace 平滑），用于选择时降权未验证策略
    - ``expected_gain`` / ``actual_gain``: A/B 评测的预期与实际收益
    """

    task_type: str
    name: str
    strategy_id: str = field(default_factory=lambda: f"strat_{uuid.uuid4().hex[:8]}")
    description: str = ""
    plan_hints: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    source: str = "learned"            # learned | manual | signal
    success_rate: float = 0.5
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    expected_gain: float = 0.0
    actual_gain: float = 0.0
    lifecycle: str = StrategyLifecycle.CANDIDATE.value
    last_used: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def confidence(self) -> float:
        """Laplace 平滑置信度：观测越多，越接近真实成功率。

        无观测时为先验 0.5——未验证的策略在选择中自动降权。
        """
        total = self.success_count + self.failure_count
        return (self.success_count + 1) / (total + 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Strategy":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def is_trusted(self) -> bool:
        return self.lifecycle == StrategyLifecycle.TRUSTED.value

    @property
    def is_usable(self) -> bool:
        """Deprecated strategies are excluded from selection."""
        return self.lifecycle != StrategyLifecycle.DEPRECATED.value


class StrategyLibrary:
    """策略库：存储、选择、并按执行结果滚动更新策略。"""

    def __init__(self, storage_dir: Path, user_id: str = "default", backend: str = "sqlite"):
        self.user_id = user_id
        self.store = create_storage(backend, Path(storage_dir))
        self.strategies: dict[str, Strategy] = {}
        self._load()

    # ------------------------------------------------------------------ CRUD

    def add_or_update(self, strategy: Strategy) -> Strategy:
        """按 (task_type, name) 去重：已存在则合并 hints 并更新描述。"""
        for existing in self.strategies.values():
            if existing.task_type == strategy.task_type and existing.name == strategy.name:
                for hint in strategy.plan_hints:
                    if hint not in existing.plan_hints:
                        existing.plan_hints.append(hint)
                for item in strategy.avoid:
                    if item not in existing.avoid:
                        existing.avoid.append(item)
                if strategy.description:
                    existing.description = strategy.description
                existing.last_used = datetime.now().isoformat()
                self._save()
                return existing
        self.strategies[strategy.strategy_id] = strategy
        self._save()
        return strategy

    def get(self, strategy_id: str) -> Optional[Strategy]:
        return self.strategies.get(strategy_id)

    def all(self) -> list[Strategy]:
        return list(self.strategies.values())

    def count(self) -> int:
        return len(self.strategies)

    # ------------------------------------------------------------- selection

    def select(
        self,
        task_type: str,
        min_success_rate: float = 0.5,
        limit: int = 3,
    ) -> list[Strategy]:
        """选择某类任务下最值得应用的策略。

        排序键：(confidence, success_rate, use_count) 降序——
        经过验证的策略优先，未验证策略（confidence 为先验 0.5）自动降权。
        """
        candidates = [
            s for s in self.strategies.values()
            if s.task_type == task_type
            and s.success_rate >= min_success_rate
            and s.is_usable
        ]
        candidates.sort(
            key=lambda s: (s.confidence, s.success_rate, s.use_count),
            reverse=True,
        )
        return candidates[:limit]

    # --------------------------------------------------------------- outcome

    def record_outcome(self, strategy_id: str, success: bool) -> Optional[Strategy]:
        """滚动更新策略成功率与验证计数。"""
        strat = self.strategies.get(strategy_id)
        if strat is None:
            return None
        strat.use_count += 1
        if success:
            strat.success_count += 1
        else:
            strat.failure_count += 1
        strat.success_rate = (
            strat.success_rate * (strat.use_count - 1) + (1.0 if success else 0.0)
        ) / strat.use_count
        strat.last_used = datetime.now().isoformat()
        self._save()
        return strat

    def record_evaluation(
        self,
        strategy_id: str,
        actual_gain: float,
        expected_gain: Optional[float] = None,
    ) -> Optional[Strategy]:
        """记录 A/B 评测得到的实际收益（P3.5 Learning Validation）。"""
        strat = self.strategies.get(strategy_id)
        if strat is None:
            return None
        strat.actual_gain = actual_gain
        if expected_gain is not None:
            strat.expected_gain = expected_gain
        strat.last_used = datetime.now().isoformat()
        self._save()
        return strat

    def promote_strategy(self, strategy_id: str, eval_report: Any = None) -> Optional[Strategy]:
        """Promote strategy lifecycle based on A/B evaluation evidence.

        candidate -> experimental -> validated -> trusted
        A strategy cannot skip states; each promotion requires positive gain.
        """
        strat = self.strategies.get(strategy_id)
        if strat is None:
            return None
        current = StrategyLifecycle(strat.lifecycle)
        gain = getattr(eval_report, "actual_gain", None)
        if gain is None:
            gain = strat.actual_gain
        if gain is None or gain <= 0:
            return strat
        if current == StrategyLifecycle.CANDIDATE:
            strat.lifecycle = StrategyLifecycle.EXPERIMENTAL.value
        elif current == StrategyLifecycle.EXPERIMENTAL:
            strat.lifecycle = StrategyLifecycle.VALIDATED.value
        elif current == StrategyLifecycle.VALIDATED and strat.use_count >= 5 and strat.success_rate >= 0.7:
            strat.lifecycle = StrategyLifecycle.TRUSTED.value
        strat.last_used = datetime.now().isoformat()
        self._save()
        return strat

    def demote_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """Demote strategy on regression. validated/trusted -> deprecated."""
        strat = self.strategies.get(strategy_id)
        if strat is None:
            return None
        current = StrategyLifecycle(strat.lifecycle)
        if current in (StrategyLifecycle.VALIDATED, StrategyLifecycle.TRUSTED):
            strat.lifecycle = StrategyLifecycle.DEPRECATED.value
        elif current == StrategyLifecycle.EXPERIMENTAL:
            strat.lifecycle = StrategyLifecycle.CANDIDATE.value
        strat.last_used = datetime.now().isoformat()
        self._save()
        return strat

    # --------------------------------------------------------------- signals

    def learn_from_signals(
        self,
        task_type: str,
        signals: list[LearningSignal],
    ) -> list[Strategy]:
        """把 LearningSignal 转换为策略（只处理 critical/major 级别）。"""
        created: list[Strategy] = []
        for signal in signals:
            if signal.severity not in ("critical", "major"):
                continue
            mapping = _SIGNAL_TO_STRATEGY.get(signal.signal_type)
            if mapping is None:
                continue
            name, hints, avoid, desc = mapping
            strat = Strategy(
                task_type=task_type,
                name=name,
                plan_hints=list(hints),
                avoid=list(avoid),
                description=desc,
                source="signal",
            )
            created.append(self.add_or_update(strat))
        return created

    # ----------------------------------------------------------- persistence

    def _save(self) -> None:
        data = {"strategies": [s.to_dict() for s in self.strategies.values()]}
        try:
            self.store.put("strategies", "all", data)
        except Exception as e:
            logger.warning(f"StrategyLibrary save failed: {e}")

    def _load(self) -> None:
        data = self.store.get("strategies", "all")
        if data and "strategies" in data:
            loaded = {}
            for raw in data["strategies"]:
                try:
                    strat = Strategy.from_dict(raw)
                    loaded[strat.strategy_id] = strat
                except Exception:
                    pass
            self.strategies = loaded
        else:
            self.strategies = {}
