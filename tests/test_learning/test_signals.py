"""Tests for learning signals (Reviewer -> LearningSignalGenerator)."""

from paperwise.core.types import AgentTrace, TraceEventType
from paperwise.learning.signals import (
    LearningSignal,
    LearningSignalGenerator,
    SignalType,
)


def _findings(**kwargs) -> dict:
    base = {"verdict": "PASS", "critical": 0, "major": 0, "minor": 0}
    base.update(kwargs)
    return base


def test_findings_critical_produces_hallucination_signal():
    gen = LearningSignalGenerator()
    signals = gen.from_findings(_findings(verdict="REJECT", critical=2), task_type="analysis")
    assert any(
        s.signal_type == SignalType.HALLUCINATION and s.severity == "critical"
        for s in signals
    )


def test_findings_major_minor_produce_quality_gap():
    gen = LearningSignalGenerator()
    signals = gen.from_findings(_findings(major=1, minor=2))
    quality = [s for s in signals if s.signal_type == SignalType.QUALITY_GAP]
    severities = {s.severity for s in quality}
    assert "major" in severities
    assert "minor" in severities


def test_findings_missing_aspects_produce_omission():
    gen = LearningSignalGenerator()
    signals = gen.from_findings(_findings(missing_aspects=["limitations", "related work"]))
    assert any(s.signal_type == SignalType.OMISSION for s in signals)


def test_findings_clean_pass_produces_success_signal():
    gen = LearningSignalGenerator()
    signals = gen.from_findings(_findings(verdict="PASS"))
    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.SUCCESS
    assert signals[0].severity == "info"


def test_from_trace_extracts_failure_and_replan_signals():
    trace = AgentTrace(trace_id="t1", task="analyze paper")
    trace.add_event(TraceEventType.NODE_FAILED, data={"status": "failed"}, node_id="verify_data")
    trace.add_event(TraceEventType.REPLAN, data={"status": "replan"}, node_id="verify_data")
    trace.add_event(TraceEventType.ERROR, data={"exception": "RuntimeError", "message": "boom"})

    gen = LearningSignalGenerator()
    signals = gen.from_trace(trace)

    node_failures = [s for s in signals if s.signal_type == SignalType.NODE_FAILURE]
    assert any(s.subject == "verify_data" for s in node_failures)
    assert any(s.subject == "RuntimeError" and s.severity == "critical" for s in node_failures)
    assert any(s.signal_type == SignalType.PLANNING_FAILURE for s in signals)


def test_from_trace_instability_requires_three_retries():
    trace = AgentTrace(trace_id="t2", task="analyze paper")
    for _ in range(2):
        trace.add_event(TraceEventType.RETRY, data={"status": "retry"}, node_id="reader")
    gen = LearningSignalGenerator()
    assert not any(s.signal_type == SignalType.INSTABILITY for s in gen.from_trace(trace))

    trace.add_event(TraceEventType.RETRY, data={"status": "retry"}, node_id="reader")
    assert any(s.signal_type == SignalType.INSTABILITY for s in gen.from_trace(trace))


def test_learning_signal_serialization_roundtrip():
    signal = LearningSignal(
        signal_type=SignalType.QUALITY_GAP,
        source="reviewer",
        severity="major",
        task_type="analysis",
        detail="d",
        subject="REVISE",
    )
    restored = LearningSignal.from_dict(signal.to_dict())
    assert restored.signal_id == signal.signal_id
    assert restored.signal_type == signal.signal_type
    assert restored.severity == signal.severity
