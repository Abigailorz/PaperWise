"""Tests for TraceEvaluator and graders using synthetic traces."""

import tempfile
from pathlib import Path

import pytest

from paperwise.core.types import AgentTrace, TraceEventType, AgentResult
from paperwise.evaluation.trace_evaluator import (
    TraceEvaluator,
    TraceMetricsExtractor,
    RoutingGrader,
    PlanningGrader,
    RetrievalGrader,
    EvidenceGrader,
    ToolUsageGrader,
    ExecutionGrader,
    TraceCompositeGrader,
)
from paperwise.evaluation.trace_store import TraceStore


def _build_trace(trace_id: str = "tr_1", with_citation: bool = True) -> AgentTrace:
    trace = AgentTrace(trace_id=trace_id, task="Analyze the method of the paper")
    trace.add_event(TraceEventType.TRACE_START)
    trace.add_event(
        TraceEventType.ROUTER_DECISION,
        data={"route": {"complexity": "complex", "confidence": 0.9}},
    )
    trace.add_event(
        TraceEventType.PLAN_GENERATED,
        data={"tasks": ["read_paper", "analyze_method", "generate_report"]},
    )
    trace.add_event(TraceEventType.LLM_END, {"step": 1})
    trace.add_event(
        TraceEventType.TOOL_START,
        {"tool_name": "read_file", "args": {"path": "text.md"}},
        step=1,
    )
    trace.add_event(
        TraceEventType.TOOL_END,
        {"tool_name": "read_file", "args": {"path": "text.md"}, "output_preview": "paper text"},
        step=1,
    )
    trace.add_event(
        TraceEventType.TOOL_START,
        {"tool_name": "grep", "args": {"pattern": "accuracy"}},
        step=2,
    )
    trace.add_event(
        TraceEventType.TOOL_END,
        {"tool_name": "grep", "args": {"pattern": "accuracy"}, "output_preview": "95% accuracy"},
        step=2,
    )
    trace.add_event(TraceEventType.NODE_DONE, {"node": "read_paper"}, node_id="read_paper")
    trace.add_event(TraceEventType.NODE_DONE, {"node": "analyze_method"}, node_id="analyze_method")
    trace.add_event(TraceEventType.NODE_DONE, {"node": "generate_report"}, node_id="generate_report")
    output = "The method achieves 95% accuracy."
    if with_citation:
        output += " [source: text.md L10-L15]"
    trace.agent_result = AgentResult(final_output=output, success=True, steps=3)
    trace.add_event(TraceEventType.RESULT, {"final_output_preview": output[:100]})
    trace.add_event(TraceEventType.TRACE_END)
    return trace


@pytest.mark.asyncio
async def test_trace_evaluator_runs_all_graders():
    trace = _build_trace()
    evaluator = TraceEvaluator()
    result = await evaluator.evaluate(trace)
    assert result["trace_id"] == "tr_1"
    assert "metrics" in result
    assert "score" in result
    assert "passed" in result
    assert result["metrics"]["llm_calls"] == 1
    assert result["metrics"]["tool_calls"] == 2


@pytest.mark.asyncio
async def test_trace_evaluator_evaluate_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp), backend="json")
        trace = _build_trace("tr_by_id")
        store.save(trace)
        evaluator = TraceEvaluator()
        result = await evaluator.evaluate_by_id("tr_by_id", store)
        assert result["trace_id"] == "tr_by_id"
        assert result["score"] > 0


@pytest.mark.asyncio
async def test_trace_evaluator_evaluate_result():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp), backend="json")
        trace = _build_trace("tr_result")
        store.save(trace)
        agent_result = AgentResult(final_output="ok", success=True, trace_id="tr_result")
        evaluator = TraceEvaluator()
        result = await evaluator.evaluate_result(agent_result, store)
        assert result["trace_id"] == "tr_result"


@pytest.mark.asyncio
async def test_routing_grader_scores_high_for_complex_route():
    trace = _build_trace()
    grader = RoutingGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score > 0.5


@pytest.mark.asyncio
async def test_planning_grader_scores_high_when_all_nodes_done():
    trace = _build_trace()
    grader = PlanningGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score > 0.5


@pytest.mark.asyncio
async def test_retrieval_grader_detects_read_file():
    trace = _build_trace()
    grader = RetrievalGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score > 0.5


@pytest.mark.asyncio
async def test_evidence_grader_validates_citation():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        paper_dir = workspace / "paper"
        paper_dir.mkdir()
        text_md = paper_dir / "text.md"
        lines = ["\n"] * 20
        lines[9:15] = ["method section line\n"] * 6
        text_md.write_text("".join(lines), encoding="utf-8")

        trace = _build_trace(with_citation=True)
        trace.metadata["workspace"] = str(workspace)
        grader = EvidenceGrader()
        grade = await grader.grade(trace.agent_result.final_output, {"trace": trace})
        assert grade.score > 0.5


@pytest.mark.asyncio
async def test_tool_usage_grader_scores_high_for_legal_tools():
    trace = _build_trace()
    grader = ToolUsageGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score > 0.5


@pytest.mark.asyncio
async def test_execution_grader_scores_high_for_success():
    trace = _build_trace()
    grader = ExecutionGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score > 0.5


@pytest.mark.asyncio
async def test_execution_grader_penalizes_errors():
    trace = _build_trace()
    trace.events.append(
        type("E", (), {"type": TraceEventType.ERROR, "data": {}, "node_id": None, "step": 5})()
    )
    grader = ExecutionGrader()
    grade = await grader.grade("", {"trace": trace})
    assert grade.score < 1.0


def test_metrics_extractor_counts():
    trace = _build_trace()
    metrics = TraceMetricsExtractor().extract(trace)
    assert metrics["llm_calls"] == 1
    assert metrics["tool_calls"] == 2
    assert metrics["tool_stats"].get("read_file") == 1
    assert metrics["tool_stats"].get("grep") == 1
    assert metrics["retrieved_text"] is True
    assert metrics["grep_searches"] == 1
    assert metrics["nodes_executed"] == 3
    assert metrics["citation_count"] == 1


def test_composite_grader_weights():
    grader = TraceCompositeGrader()
    assert grader.graders["routing"][1] == TraceCompositeGrader.DEFAULT_WEIGHTS["routing"]
