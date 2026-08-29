"""Failure pattern extraction — 从 AgentTrace 历史聚合可复用的失败模式。

LearningSignal 描述"这一次执行发生了什么"，
FailurePattern 描述"跨多次执行反复出现什么"。
只有出现次数 >= min_occurrences 的模式才会被保留，避免对偶发噪声过拟合。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from paperwise.core.types import AgentTrace, TraceEventType


@dataclass
class FailurePattern:
    """跨多条 trace 反复出现的失败模式。"""

    category: str                    # node_failure | exception | retry_loop | replan
    subject: str                     # node_id / 异常类型 / 工具名
    pattern_id: str = ""
    occurrences: int = 0
    trace_ids: list[str] = field(default_factory=list)
    example_messages: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self) -> None:
        if not self.pattern_id:
            self.pattern_id = f"fp_{self.category}_{self.subject}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FailurePattern":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class FailurePatternExtractor:
    """从 TraceStore / trace 列表中聚合失败模式。"""

    def __init__(self, min_occurrences: int = 2, max_examples: int = 3):
        self.min_occurrences = min_occurrences
        self.max_examples = max_examples

    def extract(self, traces: list[AgentTrace]) -> list[FailurePattern]:
        """聚合失败模式，按出现次数降序返回。"""
        buckets: dict[str, FailurePattern] = {}

        def _record(category: str, subject: str, trace: AgentTrace, message: str, timestamp: str) -> None:
            key = f"{category}|{subject}"
            pat = buckets.get(key)
            if pat is None:
                pat = FailurePattern(category=category, subject=subject)
                pat.first_seen = timestamp
                buckets[key] = pat
            pat.occurrences += 1
            pat.last_seen = timestamp
            if trace.trace_id not in pat.trace_ids:
                pat.trace_ids.append(trace.trace_id)
            if message and len(pat.example_messages) < self.max_examples:
                pat.example_messages.append(message[:200])

        for trace in traces:
            for ev in trace.events:
                if ev.type == TraceEventType.NODE_FAILED:
                    _record("node_failure", ev.node_id or "unknown", trace,
                            str(ev.data.get("status", "")), ev.timestamp)
                elif ev.type == TraceEventType.ERROR:
                    _record("exception", str(ev.data.get("exception", "unknown")), trace,
                            str(ev.data.get("message", "")), ev.timestamp)
                elif ev.type == TraceEventType.RETRY:
                    _record("retry_loop", ev.node_id or "unknown", trace,
                            str(ev.data.get("status", "")), ev.timestamp)
                elif ev.type == TraceEventType.REPLAN:
                    _record("replan", ev.node_id or "unknown", trace,
                            str(ev.data.get("status", "")), ev.timestamp)

        patterns = [p for p in buckets.values() if p.occurrences >= self.min_occurrences]
        patterns.sort(key=lambda p: p.occurrences, reverse=True)
        return patterns

    def extract_from_store(self, store: Any, limit: int = 100) -> list[FailurePattern]:
        """从 TraceStore 读取最近 limit 条 trace 并聚合。"""
        traces = store.list(limit=limit)
        return self.extract(traces)
