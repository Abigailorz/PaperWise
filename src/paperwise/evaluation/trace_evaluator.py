"""TraceEvaluator — 从 AgentTrace 中提取多维过程指标。

提供六个维度的 Grader：
- RoutingGrader: 路由决策质量
- PlanningGrader: 计划覆盖与完成度
- RetrievalGrader: 信息检索充分性
- EvidenceGrader: 引用证据质量
- ToolUsageGrader: 工具调用效率
- ExecutionGrader: 执行稳健性
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from paperwise.core.types import AgentTrace, TraceEvent, TraceEventType, AgentResult
from paperwise.evaluation.graders import Grader, GradeResult


class TraceMetricsExtractor:
    """从 AgentTrace 中提取标准化过程指标。"""

    def extract(self, trace: AgentTrace) -> dict[str, Any]:
        events = trace.events
        llm_ends = [ev for ev in events if ev.type == TraceEventType.LLM_END]
        tool_ends = [ev for ev in events if ev.type == TraceEventType.TOOL_END]
        errors = [ev for ev in events if ev.type == TraceEventType.ERROR]
        replans = [ev for ev in events if ev.type == TraceEventType.REPLAN]
        retries = [ev for ev in events if ev.type == TraceEventType.RETRY]
        step_starts = [ev for ev in events if ev.type == TraceEventType.STEP_START]
        node_ends = [ev for ev in events if ev.type in (TraceEventType.NODE_END, TraceEventType.NODE_DONE)]
        node_failures = [ev for ev in events if ev.type == TraceEventType.NODE_FAILED]
        review_rounds = [ev for ev in events if ev.type == TraceEventType.REVIEW_ROUND]

        tool_stats: dict[str, int] = {}
        for ev in tool_ends:
            name = ev.data.get("tool_name", "unknown")
            tool_stats[name] = tool_stats.get(name, 0) + 1

        # 检索相关事件
        read_paper_events = [
            ev for ev in tool_ends
            if ev.data.get("tool_name") == "read_file" and "text.md" in str(ev.data.get("args", {}))
        ]
        grep_events = [ev for ev in tool_ends if ev.data.get("tool_name") == "grep"]

        # 引用相关
        final_output = trace.agent_result.final_output if trace.agent_result else ""
        citations = self._extract_citations(final_output)
        valid_citations = self._validate_citations(citations, trace)

        # 耗时
        duration_ms = 0.0
        if trace.start_time and trace.end_time:
            from datetime import datetime
            try:
                start = datetime.fromisoformat(trace.start_time)
                end = datetime.fromisoformat(trace.end_time)
                duration_ms = (end - start).total_seconds() * 1000
            except Exception:
                pass

        return {
            "trace_id": trace.trace_id,
            "total_events": len(events),
            "steps": len(step_starts),
            "nodes_executed": len(node_ends),
            "nodes_failed": len(node_failures),
            "llm_calls": len(llm_ends),
            "tool_calls": len(tool_ends),
            "tool_stats": tool_stats,
            "errors": len(errors),
            "replan_count": len(replans),
            "retry_count": len(retries),
            "review_rounds": len(review_rounds),
            "retrieved_text": len(read_paper_events) > 0,
            "grep_searches": len(grep_events),
            "citation_count": len(citations),
            "valid_citation_count": len(valid_citations),
            "invalid_citations": [c for c, ok in zip(citations, valid_citations) if not ok],
            "duration_ms": round(duration_ms, 2),
            "success": trace.agent_result.success if trace.agent_result else None,
        }

    @staticmethod
    def _extract_citations(text: str) -> list[str]:
        pattern = re.compile(r"\[source:\s*text\.md\s+L(\d+)(?:-L?(\d+))?\]", re.IGNORECASE)
        return [m.group(0) for m in pattern.finditer(text or "")]

    @staticmethod
    def _validate_citations(citations: list[str], trace: AgentTrace) -> list[bool]:
        """检查引用行号是否落在 paper text 实际行数范围内。"""
        results = []
        workspace = trace.metadata.get("workspace")
        paper_path = Path(workspace) / "paper" / "text.md" if workspace else None
        if not paper_path or not paper_path.exists():
            # 无法验证时默认视为有效
            results = [True] * len(citations)
            return results

        try:
            total_lines = len(paper_path.read_text(encoding="utf-8").splitlines())
        except Exception:
            results = [True] * len(citations)
            return results

        pattern = re.compile(r"L(\d+)(?:-L?(\d+))?", re.IGNORECASE)
        for cite in citations:
            match = pattern.search(cite)
            if not match:
                results.append(False)
                continue
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else start
            results.append(1 <= start <= total_lines and 1 <= end <= total_lines and end >= start)
        return results


class RoutingGrader(Grader):
    """评估 orchestrator 路由决策质量。"""

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        route_events = trace.find_events(event_type=TraceEventType.ROUTER_DECISION)
        if not route_events:
            return GradeResult(
                passed=False, score=0.0,
                errors=["No routing decision recorded"],
            )

        route = route_events[-1].data.get("route", {})
        confidence = route.get("confidence", "low")
        complexity = route.get("complexity", "simple")
        task = trace.task.lower()

        details = [f"route_complexity={complexity}, confidence={confidence}"]

        # 简单事实查询不应被判定为 complex
        simple_indicators = ["what is", "how many", "who", "where", "when", "which"]
        looks_simple = any(ind in task for ind in simple_indicators) and len(task.split()) <= 45

        score = 1.0
        errors = []
        if confidence == "low":
            score -= 0.2
            errors.append("Low confidence routing")
        if looks_simple and complexity == "complex":
            score -= 0.3
            errors.append("Simple query escalated to complex workflow")

        return GradeResult(
            passed=score >= 0.6,
            score=max(score, 0.0),
            details=details,
            errors=errors,
        )


class PlanningGrader(Grader):
    """评估计划生成与完成度。"""

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        plan_events = trace.find_events(event_type=TraceEventType.PLAN_GENERATED)
        if not plan_events:
            return GradeResult(
                passed=False, score=0.0,
                errors=["No plan generated"],
            )

        plan_data = plan_events[-1].data.get("plan", {})
        tasks = plan_data.get("tasks", [])
        if not tasks:
            tasks = plan_data.get("task_ids", [])

        # 从 node_done / node_failed 推断完成度
        done = trace.find_events(event_type=TraceEventType.NODE_DONE)
        failed = trace.find_events(event_type=TraceEventType.NODE_FAILED)

        total = max(len(tasks), len(done) + len(failed), 1)
        completed = len(done)
        completion_rate = completed / total

        # 检查关键节点是否存在
        required_nodes = {"read_paper"}
        node_ids = {ev.node_id for ev in done + failed if ev.node_id}
        missing = required_nodes - node_ids

        score = completion_rate
        errors = []
        if missing:
            score -= 0.3
            errors.append(f"Missing required nodes: {missing}")

        return GradeResult(
            passed=score >= 0.5,
            score=max(score, 0.0),
            details=[f"plan_tasks={len(tasks)}, done={len(done)}, failed={len(failed)}"],
            errors=errors,
        )


class RetrievalGrader(Grader):
    """评估论文信息检索充分性。"""

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        tool_ends = trace.find_events(event_type=TraceEventType.TOOL_END)
        read_text = any(
            ev.data.get("tool_name") == "read_file" and "text.md" in str(ev.data.get("args", {}))
            for ev in tool_ends
        )
        grep_count = sum(
            1 for ev in tool_ends if ev.data.get("tool_name") == "grep"
        )

        # 检查最终输出前是否有检索动作
        result_event = trace.last_event(TraceEventType.RESULT)
        result_idx = trace.events.index(result_event) if result_event else len(trace.events)
        read_before_result = any(
            ev.type == TraceEventType.TOOL_END
            and ev.data.get("tool_name") == "read_file"
            and "text.md" in str(ev.data.get("args", {}))
            and trace.events.index(ev) < result_idx
            for ev in tool_ends
        )

        score = 0.0
        if read_text:
            score += 0.4
        if grep_count > 0:
            score += min(0.2, grep_count * 0.05)
        if read_before_result:
            score += 0.4

        errors = []
        if not read_text:
            errors.append("Paper text was never read")
        if not read_before_result:
            errors.append("Paper not read before producing final output")

        return GradeResult(
            passed=score >= 0.5,
            score=min(score, 1.0),
            details=[f"read_text={read_text}, grep_count={grep_count}, read_before_result={read_before_result}"],
            errors=errors,
        )


class EvidenceGrader(Grader):
    """评估最终输出中的引用证据质量。"""

    def __init__(self, required_citations: int = 1):
        self.required_citations = required_citations

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        final_output = trace.agent_result.final_output if trace.agent_result else output
        extractor = TraceMetricsExtractor()
        citations = extractor._extract_citations(final_output)
        valid = extractor._validate_citations(citations, trace)

        valid_count = sum(valid)
        invalid = [c for c, ok in zip(citations, valid) if not ok]

        if not citations:
            return GradeResult(
                passed=False, score=0.0,
                errors=[f"No citations found (required {self.required_citations})"],
            )

        score = valid_count / max(len(citations), 1)
        if len(citations) >= self.required_citations:
            score = max(score, 0.5)

        errors = []
        if len(citations) < self.required_citations:
            errors.append(f"Only {len(citations)} citations found (required {self.required_citations})")
        if invalid:
            errors.append(f"Invalid citation ranges: {invalid[:5]}")

        return GradeResult(
            passed=valid_count >= self.required_citations and not invalid,
            score=score,
            details=[f"citations={len(citations)}, valid={valid_count}"],
            errors=errors,
        )


class ToolUsageGrader(Grader):
    """评估工具调用效率。"""

    LEGAL_TOOLS = {
        "read_file", "write_file", "edit_file", "grep", "glob",
        "code_interpreter", "bash", "apply_patch",
    }

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        tool_ends = trace.find_events(event_type=TraceEventType.TOOL_END)
        if not tool_ends:
            return GradeResult(
                passed=False, score=0.0,
                errors=["No tool calls recorded"],
            )

        total = len(tool_ends)
        legal = sum(
            1 for ev in tool_ends
            if ev.data.get("tool_name") in self.LEGAL_TOOLS
        )
        errors_count = sum(1 for ev in tool_ends if ev.data.get("is_error"))

        # 检测重复工具调用
        signatures = [
            (ev.data.get("tool_name"), json.dumps(ev.data.get("args", {}), sort_keys=True))
            for ev in tool_ends
        ]
        unique = len(set(signatures))
        repetition_rate = 1 - unique / max(total, 1)

        legal_rate = legal / total
        error_rate = errors_count / total
        score = legal_rate * (1 - repetition_rate) * (1 - error_rate)

        details = [
            f"total_tools={total}",
            f"legal_rate={legal_rate:.2%}",
            f"error_rate={error_rate:.2%}",
            f"repetition_rate={repetition_rate:.2%}",
        ]
        errors = []
        if error_rate > 0.2:
            errors.append(f"High tool error rate: {error_rate:.2%}")
        if repetition_rate > 0.3:
            errors.append(f"High tool repetition rate: {repetition_rate:.2%}")

        return GradeResult(
            passed=score >= 0.5,
            score=max(score, 0.0),
            details=details,
            errors=errors,
        )


class ExecutionGrader(Grader):
    """评估执行稳健性。"""

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        trace: Optional[AgentTrace] = context.get("trace")
        if trace is None:
            return GradeResult(errors=["trace missing"])

        errors = trace.find_events(event_type=TraceEventType.ERROR)
        failed_nodes = trace.find_events(event_type=TraceEventType.NODE_FAILED)
        replans = trace.find_events(event_type=TraceEventType.REPLAN)
        retries = trace.find_events(event_type=TraceEventType.RETRY)

        success = trace.agent_result.success if trace.agent_result else False

        score = 1.0
        score -= min(0.4, len(errors) * 0.2)
        score -= min(0.3, len(failed_nodes) * 0.15)
        score -= min(0.2, len(replans) * 0.1)
        score -= min(0.1, len(retries) * 0.05)
        if not success:
            score -= 0.3

        details = [
            f"success={success}",
            f"errors={len(errors)}",
            f"failed_nodes={len(failed_nodes)}",
            f"replans={len(replans)}",
            f"retries={len(retries)}",
        ]
        errors_list = []
        if errors:
            errors_list.append(f"{len(errors)} error events")
        if failed_nodes:
            errors_list.append(f"{len(failed_nodes)} failed nodes")
        if not success:
            errors_list.append("Agent result marked as unsuccessful")

        return GradeResult(
            passed=success and score >= 0.5,
            score=max(score, 0.0),
            details=details,
            errors=errors_list,
        )


class TraceCompositeGrader(Grader):
    """组合多个 trace graders，按权重聚合分数。"""

    DEFAULT_WEIGHTS = {
        "routing": 0.10,
        "planning": 0.20,
        "retrieval": 0.20,
        "evidence": 0.20,
        "tool_usage": 0.15,
        "execution": 0.15,
    }

    def __init__(self, graders: Optional[dict[str, tuple[Grader, float]]] = None):
        if graders is None:
            graders = {
                "routing": (RoutingGrader(), self.DEFAULT_WEIGHTS["routing"]),
                "planning": (PlanningGrader(), self.DEFAULT_WEIGHTS["planning"]),
                "retrieval": (RetrievalGrader(), self.DEFAULT_WEIGHTS["retrieval"]),
                "evidence": (EvidenceGrader(), self.DEFAULT_WEIGHTS["evidence"]),
                "tool_usage": (ToolUsageGrader(), self.DEFAULT_WEIGHTS["tool_usage"]),
                "execution": (ExecutionGrader(), self.DEFAULT_WEIGHTS["execution"]),
            }
        self.graders = graders

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        total_weight = 0.0
        total_score = 0.0
        passed = True
        details: list[str] = []
        errors: list[str] = []
        raw: dict[str, Any] = {}

        for name, (grader, weight) in self.graders.items():
            result = await grader.grade(output, context)
            total_weight += weight
            total_score += result.score * weight
            passed = passed and result.passed
            details.extend([f"[{name}] {d}" for d in result.details])
            errors.extend([f"[{name}] {e}" for e in result.errors])
            raw[name] = {"score": result.score, "passed": result.passed, "raw": result.raw}

        overall = total_score / max(total_weight, 1e-9)
        return GradeResult(
            passed=passed and overall >= 0.6,
            score=overall,
            details=details,
            errors=errors,
            raw=raw,
        )


class TraceEvaluator:
    """高阶入口：提取指标 + 运行 graders。"""

    def __init__(self, composite_grader: Optional[TraceCompositeGrader] = None):
        self.composite = composite_grader or TraceCompositeGrader()
        self.metrics = TraceMetricsExtractor()

    async def evaluate(self, trace: AgentTrace) -> dict[str, Any]:
        metrics = self.metrics.extract(trace)
        grade = await self.composite.grade(
            output=trace.agent_result.final_output if trace.agent_result else "",
            context={"trace": trace, "metrics": metrics},
        )
        return {
            "trace_id": trace.trace_id,
            "metrics": metrics,
            "score": grade.score,
            "passed": grade.passed,
            "details": grade.details,
            "errors": grade.errors,
            "raw": grade.raw,
        }

    async def evaluate_result(self, agent_result: AgentResult, store: Optional[Any] = None) -> dict[str, Any]:
        """根据 AgentResult 中记录的 trace_id 评估对应轨迹。"""
        if not agent_result.trace_id:
            return {
                "trace_id": None,
                "metrics": {},
                "score": 0.0,
                "passed": False,
                "details": "No trace_id in AgentResult",
                "errors": ["missing trace_id"],
                "raw": {},
            }
        return await self.evaluate_by_id(agent_result.trace_id, store)

    async def evaluate_by_id(self, trace_id: str, store: Optional[Any] = None) -> dict[str, Any]:
        """按 trace_id 从 store 读取并评估；未传 store 则构造空 trace 返回失败信息。"""
        if store is None:
            return {
                "trace_id": trace_id,
                "metrics": {},
                "score": 0.0,
                "passed": False,
                "details": "No TraceStore provided",
                "errors": ["missing trace_store"],
                "raw": {},
            }
        trace = store.get(trace_id)
        if trace is None:
            return {
                "trace_id": trace_id,
                "metrics": {},
                "score": 0.0,
                "passed": False,
                "details": f"Trace {trace_id} not found",
                "errors": ["trace_not_found"],
                "raw": {},
            }
        return await self.evaluate(trace)
