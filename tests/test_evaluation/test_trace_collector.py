"""Tests for TraceCollector implementations."""

import asyncio
from pathlib import Path

import pytest

from paperwise.core.types import AgentResult, TraceEventType
from paperwise.core.trace_collector import (
    NullTraceCollector,
    InMemoryTraceCollector,
    create_trace_collector,
)


class FakeTraceStore:
    def __init__(self):
        self.traces = []

    def save(self, trace) -> None:
        self.traces.append(trace)

    async def asave(self, trace) -> None:
        self.traces.append(trace)


class AsyncFakeTraceStore:
    def __init__(self):
        self.traces = []

    async def save(self, trace) -> None:
        await asyncio.sleep(0)
        self.traces.append(trace)


def test_null_collector_is_no_op():
    collector = NullTraceCollector()
    assert not collector.is_active()
    trace = collector.start_trace("task")
    assert trace.trace_id == "null"
    assert collector.add_event(TraceEventType.STEP_START) is None
    assert collector.end_trace() is None
    assert collector.current_trace() is None


def test_create_trace_collector_respects_enabled_flag():
    assert isinstance(create_trace_collector(enabled=True), InMemoryTraceCollector)
    assert isinstance(create_trace_collector(enabled=False), NullTraceCollector)


def test_in_memory_start_end_trace():
    collector = InMemoryTraceCollector()
    trace = collector.start_trace("test task")
    assert collector.is_active()
    assert collector.current_trace() is trace
    collector.add_event(TraceEventType.STEP_START, {"step": 1})
    result = AgentResult(final_output="done", success=True)
    ended = collector.end_trace(result)
    assert ended is trace
    assert not collector.is_active()
    assert ended.agent_result is result
    assert ended.events[0].type == TraceEventType.TRACE_START
    assert ended.events[-1].type == TraceEventType.TRACE_END


def test_nested_traces_stack():
    collector = InMemoryTraceCollector()
    outer = collector.start_trace("outer")
    collector.add_event(TraceEventType.STEP_START, {"step": 1})
    inner = collector.start_trace("inner")
    assert collector.current_trace() is inner
    collector.add_event(TraceEventType.STEP_START, {"step": 2})
    collector.end_trace(AgentResult(final_output="inner done", success=True))
    assert collector.current_trace() is outer
    collector.end_trace(AgentResult(final_output="outer done", success=True))
    assert not collector.is_active()


def test_fire_and_forget_sync_store():
    store = FakeTraceStore()
    collector = InMemoryTraceCollector(trace_store=store)
    collector.start_trace("task")
    collector.end_trace(AgentResult(final_output="ok", success=True))
    # sync store is called synchronously in _persist
    assert len(store.traces) == 1


def test_sync_context_async_store_runs_immediately():
    """When called from a sync context with no running loop, async save runs via asyncio.run."""
    store = AsyncFakeTraceStore()
    collector = InMemoryTraceCollector(trace_store=store)
    collector.start_trace("task")
    collector.end_trace(AgentResult(final_output="ok", success=True))
    # In a sync test there is no running loop, so _persist falls back to asyncio.run
    assert len(store.traces) == 1


@pytest.mark.asyncio
async def test_aflush_waits_for_async_store_in_async_context():
    """When called from an async context, aflush should await pending save tasks."""
    store = AsyncFakeTraceStore()
    collector = InMemoryTraceCollector(trace_store=store)
    collector.start_trace("task")
    collector.add_event(TraceEventType.TOOL_END, {"tool_name": "read_file"})
    collector.end_trace(AgentResult(final_output="ok", success=True))
    # Inside an async context, _persist schedules a task
    assert len(store.traces) == 0
    await collector.aflush()
    assert len(store.traces) == 1
    assert any(e.type == TraceEventType.TOOL_END for e in store.traces[0].events)


def test_truncate_large_payload():
    collector = InMemoryTraceCollector(max_content_preview=200)
    trace = collector.start_trace("task")
    big = "x" * 1000
    collector.add_event(TraceEventType.LLM_START, {"prompt": big})
    event = trace.events[-1]
    assert "omitted" in event.data["prompt"]
    assert len(event.data["prompt"]) < 250
    collector.end_trace(AgentResult(final_output="ok", success=True))


def test_merge_child_trace():
    collector = InMemoryTraceCollector()
    parent = collector.start_trace("parent")
    parent_event = collector.add_event(TraceEventType.NODE_START, {"node": "child"})
    child_collector = InMemoryTraceCollector()
    child = child_collector.start_trace("child")
    child.add_event(TraceEventType.STEP_START, {"step": 1})
    child_collector.end_trace(AgentResult(final_output="child result", success=True))

    collector.merge_child_trace(child, parent_event)
    collector.end_trace(AgentResult(final_output="parent result", success=True))

    child_events = [e for e in parent.events if e.node_id == child.trace_id]
    assert len(child_events) >= 1
