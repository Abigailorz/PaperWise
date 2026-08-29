"""Tests for PassKEvaluator integration with TraceEvaluator."""

import tempfile
from pathlib import Path

import pytest

from paperwise.core.types import AgentResult
from paperwise.evaluation import PassKEvaluator, TraceEvaluator
from paperwise.evaluation.trace_store import TraceStore
from paperwise.core.trace_collector import InMemoryTraceCollector


class TracedRunner:
    """A fake runner that produces AgentResults with trace_ids backed by a TraceStore."""

    def __init__(self, store: TraceStore):
        self.store = store
        self._counter = 0

    async def run(self) -> AgentResult:
        self._counter += 1
        collector = InMemoryTraceCollector(trace_store=self.store)
        trace = collector.start_trace(task=f"run_{self._counter}")
        from paperwise.core.types import TraceEventType
        collector.add_event(TraceEventType.TOOL_END, {"tool_name": "read_file"})
        result = AgentResult(final_output=f"output {self._counter}", success=True, steps=2)
        collector.end_trace(result)
        await collector.aflush()
        result.trace_id = trace.trace_id
        return result


async def _rubric_fn(output: str) -> dict:
    return {"scores": {"accuracy": 3}, "hallucinations": 0}


async def _empty_rubric_fn(output: str) -> dict:
    return {"scores": {}, "hallucinations": 0}


@pytest.mark.asyncio
async def test_passk_includes_trace_scores_when_trace_evaluator_provided():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp), backend="json")
        runner = TracedRunner(store)
        trace_evaluator = TraceEvaluator()
        evaluator = PassKEvaluator(k=2, trace_evaluator=trace_evaluator, trace_store=store)
        result = await evaluator.evaluate(
            task_name="test_task",
            run_fn=runner.run,
            rubric_fn=_rubric_fn,
        )
        assert result.avg_trace_score is not None
        assert result.avg_trace_score > 0
        for run in result.runs:
            assert run.trace_score is not None


@pytest.mark.asyncio
async def test_passk_works_without_trace_evaluator():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp), backend="json")
        runner = TracedRunner(store)
        evaluator = PassKEvaluator(k=2)
        result = await evaluator.evaluate(
            task_name="test_task",
            run_fn=runner.run,
            rubric_fn=_rubric_fn,
        )
        assert result.avg_trace_score is None


@pytest.mark.asyncio
async def test_passk_handles_missing_trace_id():
    async def run_no_trace() -> AgentResult:
        return AgentResult(final_output="no trace", success=True)

    store = TraceStore(Path(tempfile.mkdtemp()), backend="json")
    evaluator = PassKEvaluator(k=1, trace_evaluator=TraceEvaluator(), trace_store=store)
    result = await evaluator.evaluate(
        task_name="test_task",
        run_fn=run_no_trace,
        rubric_fn=_empty_rubric_fn,
    )
    assert result.avg_trace_score is None
