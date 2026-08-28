"""TraceCollector — Agent 执行轨迹收集器。

提供显式 API 收集 Agent 执行过程中的结构化事件，
支持嵌套 trace、span 上下文和可选的异步持久化。
"""

import uuid
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Callable

from paperwise.core.types import AgentTrace, TraceEvent, TraceEventType, AgentResult


class TraceCollector(ABC):
    """轨迹收集器抽象。"""

    @abstractmethod
    def start_trace(
        self,
        task: str,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: str = "default",
        parent_trace_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentTrace: ...

    @abstractmethod
    def end_trace(self, agent_result: Optional[AgentResult] = None) -> Optional[AgentTrace]: ...

    @abstractmethod
    def add_event(
        self,
        event_type: TraceEventType,
        data: Optional[dict[str, Any]] = None,
        step: Optional[int] = None,
        node_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
    ) -> Optional[TraceEvent]: ...

    @abstractmethod
    def current_trace(self) -> Optional[AgentTrace]: ...

    @abstractmethod
    def is_active(self) -> bool: ...


class NullTraceCollector(TraceCollector):
    """空实现：不收集任何轨迹，零开销。"""

    def start_trace(self, task: str, trace_id: Optional[str] = None,
                    session_id: Optional[str] = None, user_id: str = "default",
                    parent_trace_id: Optional[str] = None,
                    parent_event_id: Optional[str] = None,
                    metadata: Optional[dict[str, Any]] = None) -> AgentTrace:
        # 返回一个一次性 trace，但不保存
        return AgentTrace(trace_id=trace_id or "null", task=task)

    def end_trace(self, agent_result: Optional[AgentResult] = None) -> Optional[AgentTrace]:
        return None

    def add_event(self, event_type: TraceEventType,
                  data: Optional[dict[str, Any]] = None,
                  step: Optional[int] = None, node_id: Optional[str] = None,
                  parent_event_id: Optional[str] = None,
                  latency_ms: Optional[float] = None) -> Optional[TraceEvent]:
        return None

    def current_trace(self) -> Optional[AgentTrace]:
        return None

    def is_active(self) -> bool:
        return False


class InMemoryTraceCollector(TraceCollector):
    """内存中的轨迹收集器，支持异步持久化到 TraceStore。"""

    def __init__(
        self,
        trace_store: Optional[Any] = None,
        save_callback: Optional[Callable[[AgentTrace], None]] = None,
        max_content_preview: int = 500,
        max_tool_output_preview: int = 800,
    ):
        self.trace_store = trace_store
        self.save_callback = save_callback
        self.max_content_preview = max_content_preview
        self.max_tool_output_preview = max_tool_output_preview
        self._trace: Optional[AgentTrace] = None

    def start_trace(
        self,
        task: str,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: str = "default",
        parent_trace_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentTrace:
        trace_id = trace_id or f"tr_{uuid.uuid4().hex[:12]}"
        self._trace = AgentTrace(
            trace_id=trace_id,
            task=task,
            session_id=session_id,
            user_id=user_id,
            metadata={
                "parent_trace_id": parent_trace_id,
                "parent_event_id": parent_event_id,
                "version": 1,
                **(metadata or {}),
            },
        )
        self._trace.add_event(
            TraceEventType.TRACE_START,
            data={"parent_trace_id": parent_trace_id, "parent_event_id": parent_event_id},
        )
        return self._trace

    def end_trace(self, agent_result: Optional[AgentResult] = None) -> Optional[AgentTrace]:
        if self._trace is None:
            return None
        self._trace.finish(agent_result)
        self._trace.add_event(
            TraceEventType.TRACE_END,
            data={"duration_ms": self._duration_ms()},
        )
        trace = self._trace
        self._persist(trace)
        self._trace = None
        return trace

    def add_event(
        self,
        event_type: TraceEventType,
        data: Optional[dict[str, Any]] = None,
        step: Optional[int] = None,
        node_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
    ) -> Optional[TraceEvent]:
        if self._trace is None:
            return None
        # 对大型 payload 做预览截断，避免 trace 体积爆炸
        data = self._truncate_data(data or {})
        return self._trace.add_event(
            event_type=event_type,
            data=data,
            step=step,
            node_id=node_id,
            parent_event_id=parent_event_id,
            latency_ms=latency_ms,
        )

    def merge_child_trace(self, child_trace: AgentTrace, parent_event: Optional[TraceEvent] = None) -> None:
        """将子 Agent 的轨迹合并到当前轨迹中。"""
        if self._trace is None or child_trace is None:
            return
        self._trace.merge_child_trace(child_trace, parent_event)

    def current_trace(self) -> Optional[AgentTrace]:
        return self._trace

    def is_active(self) -> bool:
        return self._trace is not None

    def _duration_ms(self) -> Optional[float]:
        if self._trace is None or not self._trace.end_time or not self._trace.start_time:
            return None
        try:
            start = datetime.fromisoformat(self._trace.start_time)
            end = datetime.fromisoformat(self._trace.end_time)
            return (end - start).total_seconds() * 1000
        except Exception:
            return None

    def _truncate_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """对可能很大的字段做预览截断。"""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                if k in ("content", "prompt", "system_prompt"):
                    v = self._preview(v, self.max_content_preview)
                elif k in ("output", "tool_output", "result"):
                    v = self._preview(v, self.max_tool_output_preview)
            result[k] = v
        return result

    @staticmethod
    def _preview(text: str, limit: int) -> str:
        if not text or len(text) <= limit:
            return text
        head = int(limit * 0.7)
        tail = limit - head - 30
        return text[:head] + f"\n... ({len(text) - head - tail} chars omitted) ...\n" + text[-tail:]

    def _persist(self, trace: AgentTrace) -> None:
        """异步持久化 trace，失败不影响主流程。"""
        if self.save_callback:
            try:
                self.save_callback(trace)
            except Exception:
                pass
        if self.trace_store is None:
            return
        try:
            if asyncio.iscoroutinefunction(self.trace_store.save):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.trace_store.save(trace))
                except RuntimeError:
                    # 没有事件循环时同步保存
                    import asyncio as _asyncio
                    _asyncio.run(self.trace_store.save(trace))
            else:
                # 同步保存也尽量在线程中执行
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, self.trace_store.save, trace)
                except RuntimeError:
                    self.trace_store.save(trace)
        except Exception:
            # 持久化失败不应影响 Agent 执行
            pass


def create_trace_collector(
    trace_store: Optional[Any] = None,
    save_callback: Optional[Callable[[AgentTrace], None]] = None,
    enabled: bool = True,
) -> TraceCollector:
    """工厂函数：根据配置创建合适的 TraceCollector。"""
    if not enabled:
        return NullTraceCollector()
    return InMemoryTraceCollector(trace_store=trace_store, save_callback=save_callback)
