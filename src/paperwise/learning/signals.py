"""Learning signals — Reviewer 与 AgentTrace 的结构化学习产物。

LearningSignal 是 Reviewer 升级为 Learning Signal Generator 的核心载体：
Reviewer 的 findings（或一次执行的 trace）不再只用于当轮 revise，
而是被转换为带类型与严重度的信号，进入 StrategyLibrary 影响后续规划。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from paperwise.core.types import AgentTrace, TraceEventType


class SignalType:
    """学习信号类型。"""

    HALLUCINATION = "hallucination"        # 审查发现编造内容
    QUALITY_GAP = "quality_gap"            # 审查发现重大质量问题
    OMISSION = "omission"                  # 审查发现遗漏重要内容
    VERIFICATION_GAP = "verification_gap"  # 数值/代码验证不足
    NODE_FAILURE = "node_failure"          # DAG 节点反复失败
    PLANNING_FAILURE = "planning_failure"  # 触发 replan
    INSTABILITY = "instability"            # 大量重试
    SUCCESS = "success"                    # 干净通过，可作为正向经验


@dataclass
class LearningSignal:
    """一条结构化学习信号。"""

    signal_type: str
    source: str                       # reviewer | trace
    severity: str                     # critical | major | minor | info
    signal_id: str = field(default_factory=lambda: f"sig_{uuid.uuid4().hex[:8]}")
    task_type: str = "analysis"
    detail: str = ""
    subject: str = ""                 # 关联对象：node_id / 工具名 / verdict
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LearningSignal":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class LearningSignalGenerator:
    """把 Reviewer findings / AgentTrace 转换为 LearningSignal 列表。

    只产出确定性、可程序校验的信号，不调用 LLM（Mechanism over prompt）。
    """

    def from_findings(
        self,
        findings: dict[str, Any],
        task_type: str = "analysis",
    ) -> list[LearningSignal]:
        """从 parse_findings() 的结果生成信号。

        规则：
        - verdict == REJECT 或 critical > 0 -> hallucination (critical)
        - major > 0 -> quality_gap (major)
        - minor > 0 -> quality_gap (minor)
        - summary 中有 missing aspects -> omission (major)
        - verdict == PASS 且无任何 issue -> success (info)
        """
        signals: list[LearningSignal] = []
        verdict = str(findings.get("verdict", "UNKNOWN")).upper()
        critical = int(findings.get("critical", 0) or 0)
        major = int(findings.get("major", 0) or 0)
        minor = int(findings.get("minor", 0) or 0)

        if verdict == "REJECT" or critical > 0:
            signals.append(LearningSignal(
                signal_type=SignalType.HALLUCINATION,
                source="reviewer",
                severity="critical",
                task_type=task_type,
                subject=verdict,
                detail=f"Review verdict={verdict}, critical={critical}",
            ))
        if major > 0:
            signals.append(LearningSignal(
                signal_type=SignalType.QUALITY_GAP,
                source="reviewer",
                severity="major",
                task_type=task_type,
                subject=verdict,
                detail=f"Review found {major} major issue(s)",
            ))
        if minor > 0:
            signals.append(LearningSignal(
                signal_type=SignalType.QUALITY_GAP,
                source="reviewer",
                severity="minor",
                task_type=task_type,
                subject=verdict,
                detail=f"Review found {minor} minor issue(s)",
            ))

        missing = findings.get("missing_aspects") or []
        if isinstance(missing, (list, tuple)) and missing:
            signals.append(LearningSignal(
                signal_type=SignalType.OMISSION,
                source="reviewer",
                severity="major",
                task_type=task_type,
                detail=f"Missing aspects: {len(missing)}",
            ))

        if not signals and verdict == "PASS":
            signals.append(LearningSignal(
                signal_type=SignalType.SUCCESS,
                source="reviewer",
                severity="info",
                task_type=task_type,
                subject="PASS",
                detail="Review passed with no flagged issues",
            ))
        return signals

    def from_trace(
        self,
        trace: AgentTrace,
        task_type: str = "analysis",
    ) -> list[LearningSignal]:
        """从单条 AgentTrace 提取执行层面的信号。

        规则：
        - NODE_FAILED 事件 -> node_failure (major)，subject=node_id
        - REPLAN 事件 -> planning_failure (major)
        - RETRY 事件 >= 3 次 -> instability (minor)
        - ERROR 事件 -> node_failure (critical)，subject=exception 类型
        """
        signals: list[LearningSignal] = []

        failed_nodes = {
            ev.node_id for ev in trace.find_events(TraceEventType.NODE_FAILED) if ev.node_id
        }
        for node_id in sorted(failed_nodes):
            signals.append(LearningSignal(
                signal_type=SignalType.NODE_FAILURE,
                source="trace",
                severity="major",
                task_type=task_type,
                subject=node_id,
                detail=f"Node {node_id} failed in trace {trace.trace_id}",
            ))

        replans = trace.find_events(TraceEventType.REPLAN)
        if replans:
            signals.append(LearningSignal(
                signal_type=SignalType.PLANNING_FAILURE,
                source="trace",
                severity="major",
                task_type=task_type,
                subject=replans[-1].node_id or "",
                detail=f"{len(replans)} replan event(s) in trace {trace.trace_id}",
            ))

        retries = trace.find_events(TraceEventType.RETRY)
        if len(retries) >= 3:
            signals.append(LearningSignal(
                signal_type=SignalType.INSTABILITY,
                source="trace",
                severity="minor",
                task_type=task_type,
                detail=f"{len(retries)} retry event(s) in trace {trace.trace_id}",
            ))

        for ev in trace.find_events(TraceEventType.ERROR):
            signals.append(LearningSignal(
                signal_type=SignalType.NODE_FAILURE,
                source="trace",
                severity="critical",
                task_type=task_type,
                subject=str(ev.data.get("exception", "unknown")),
                detail=str(ev.data.get("message", ""))[:200],
            ))
        return signals
