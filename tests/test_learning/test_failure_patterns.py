"""Tests for failure pattern extraction from agent traces."""

from paperwise.core.types import AgentTrace, TraceEventType
from paperwise.evaluation.trace_store import TraceStore
from paperwise.learning.failure_patterns import FailurePattern, FailurePatternExtractor


def _trace_with_failures(trace_id: str, node_id: str = "verify_data") -> AgentTrace:
    trace = AgentTrace(trace_id=trace_id, task=f"task {trace_id}")
    trace.add_event(TraceEventType.NODE_FAILED, data={"status": "failed"}, node_id=node_id)
    trace.add_event(TraceEventType.ERROR, data={"exception": "RuntimeError", "message": "boom"})
    return trace


def test_extract_aggregates_recurring_node_failure():
    extractor = FailurePatternExtractor(min_occurrences=2)
    traces = [_trace_with_failures("t1"), _trace_with_failures("t2"), _trace_with_failures("t3")]
    patterns = extractor.extract(traces)

    node_failure = next(p for p in patterns if p.category == "node_failure")
    assert node_failure.subject == "verify_data"
    assert node_failure.occurrences == 3
    assert node_failure.trace_ids == ["t1", "t2", "t3"]
    assert node_failure.first_seen <= node_failure.last_seen


def test_extract_filters_rare_patterns():
    extractor = FailurePatternExtractor(min_occurrences=2)
    traces = [_trace_with_failures("t1", node_id="flaky_once")]
    # 同一 trace 内只出现一次 -> 不满足阈值
    assert extractor.extract(traces) == []


def test_extract_groups_exceptions_by_type():
    extractor = FailurePatternExtractor(min_occurrences=2)
    traces = [_trace_with_failures("t1"), _trace_with_failures("t2")]
    patterns = extractor.extract(traces)

    exception = next(p for p in patterns if p.category == "exception")
    assert exception.subject == "RuntimeError"
    assert exception.example_messages == ["boom", "boom"]  # 每条 trace 一个示例


def test_extract_orders_by_occurrences():
    extractor = FailurePatternExtractor(min_occurrences=1)
    traces = [
        _trace_with_failures("t1", node_id="a"),
        _trace_with_failures("t2", node_id="a"),
        _trace_with_failures("t3", node_id="b"),
    ]
    patterns = extractor.extract(traces)
    node_patterns = [p for p in patterns if p.category == "node_failure"]
    assert node_patterns[0].occurrences >= node_patterns[-1].occurrences


def test_extract_from_store(tmp_path):
    store = TraceStore(tmp_path / "traces")
    for i in range(3):
        store.save(_trace_with_failures(f"t{i}"))

    extractor = FailurePatternExtractor(min_occurrences=2)
    patterns = extractor.extract_from_store(store)
    assert any(p.category == "node_failure" and p.occurrences == 3 for p in patterns)
    store.close()


def test_failure_pattern_serialization_roundtrip():
    pattern = FailurePattern(
        category="node_failure",
        subject="verify_data",
        occurrences=4,
        trace_ids=["t1"],
        example_messages=["failed"],
    )
    restored = FailurePattern.from_dict(pattern.to_dict())
    assert restored.pattern_id == pattern.pattern_id
    assert restored.occurrences == 4
    assert restored.trace_ids == ["t1"]
