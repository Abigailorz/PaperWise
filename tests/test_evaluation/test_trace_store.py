"""Tests for TraceStore persistence and metrics."""

import tempfile
from pathlib import Path

import pytest

from paperwise.core.types import AgentTrace, TraceEventType, AgentResult
from paperwise.evaluation.trace_store import TraceStore


@pytest.fixture
def store(tmp_path: Path):
    return TraceStore(tmp_path, backend="sqlite")


def _build_trace(trace_id: str = "tr_test", with_result: bool = True) -> AgentTrace:
    trace = AgentTrace(trace_id=trace_id, task="test task", session_id="sess_1")
    trace.add_event(TraceEventType.TRACE_START)
    trace.add_event(TraceEventType.LLM_END, {"step": 1})
    trace.add_event(TraceEventType.TOOL_END, {"tool_name": "read_file"})
    trace.add_event(TraceEventType.TOOL_END, {"tool_name": "grep"})
    trace.add_event(TraceEventType.RETRY)
    trace.add_event(TraceEventType.REPLAN)
    trace.add_event(TraceEventType.ERROR, {"message": "oops"})
    trace.add_event(TraceEventType.TRACE_END)
    if with_result:
        trace.agent_result = AgentResult(final_output="result", success=True)
    return trace


def test_save_and_get(store: TraceStore):
    trace = _build_trace("tr_1")
    store.save(trace)
    loaded = store.get("tr_1")
    assert loaded is not None
    assert loaded.trace_id == "tr_1"
    assert loaded.task == "test task"
    assert loaded.session_id == "sess_1"


def test_get_nonexistent(store: TraceStore):
    assert store.get("missing") is None


def test_list_and_filter(store: TraceStore):
    store.save(_build_trace("tr_a", with_result=False))
    store.save(_build_trace("tr_b", with_result=False))
    all_traces = store.list()
    assert len(all_traces) == 2
    by_session = store.list(session_id="sess_1")
    assert len(by_session) == 2
    by_prefix = store.list(task_prefix="other")
    assert len(by_prefix) == 0


def test_get_metrics_counts(store: TraceStore):
    trace = _build_trace("tr_metrics")
    store.save(trace)
    metrics = store.get_metrics("tr_metrics")
    assert metrics["llm_calls"] == 1
    assert metrics["tool_calls"] == 2
    assert metrics["error_count"] == 1
    assert metrics["replan_count"] == 1
    assert metrics["retry_count"] == 1
    assert metrics["success"] is True
    assert metrics["trace_id"] == "tr_metrics"


def test_list_sessions(store: TraceStore):
    t1 = AgentTrace(trace_id="t1", task="a", session_id="s1")
    t2 = AgentTrace(trace_id="t2", task="b", session_id="s2")
    store.save(t1)
    store.save(t2)
    sessions = store.list_sessions()
    assert sessions == ["s1", "s2"]


def test_count_and_delete(store: TraceStore):
    store.save(_build_trace("tr_del", with_result=False))
    assert store.count() == 1
    assert store.delete("tr_del") is True
    assert store.count() == 0
    assert store.delete("missing") is False


def test_json_backend():
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp), backend="json")
        trace = _build_trace("tr_json")
        store.save(trace)
        assert store.get("tr_json") is not None
        metrics = store.get_metrics("tr_json")
        assert metrics["llm_calls"] == 1
