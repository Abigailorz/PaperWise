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
from pathlib import Path
from typing import Any, Optional

from paperwise.learning.signals import LearningSignal, SignalType
from paperwise.memory.storage import create_storage

logger = logging.getLogger("paperwise")


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
    """一条可复用的规划策略。"""

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
    last_used: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Strategy":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


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

        按 success_rate 降序，同分时按 use_count 降序（置信度优先）。
        """
        candidates = [
            s for s in self.strategies.values()
            if s.task_type == task_type and s.success_rate >= min_success_rate
        ]
        candidates.sort(key=lambda s: (s.success_rate, s.use_count), reverse=True)
        return candidates[:limit]

    # --------------------------------------------------------------- outcome

    def record_outcome(self, strategy_id: str, success: bool) -> Optional[Strategy]:
        """滚动更新策略成功率（与 ProceduralMemory 相同的均值更新）。"""
        strat = self.strategies.get(strategy_id)
        if strat is None:
            return None
        strat.use_count += 1
        strat.success_rate = (
            strat.success_rate * (strat.use_count - 1) + (1.0 if success else 0.0)
        ) / strat.use_count
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
