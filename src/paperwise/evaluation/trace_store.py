"""Agent 执行轨迹的持久化存储。

复用现有的 StorageBackend 抽象（SQLite / JSON），
支持按 trace_id、session_id、时间范围查询。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from paperwise.core.types import AgentTrace, TraceEventType
from paperwise.memory.storage import create_storage, StorageBackend


class TraceStore:
    """AgentTrace 持久化存储。

    每个 trace 作为一个独立记录保存，键为 trace_id，
    值为 {"trace": <AgentTrace dict>, "updated_at": <ISO timestamp>}。
    """

    COLLECTION = "agent_traces"

    def __init__(self, storage_dir: Path, backend: str = "sqlite"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.store: StorageBackend = create_storage(backend, self.storage_dir)

    def save(self, trace: AgentTrace) -> None:
        """保存或更新一条轨迹。"""
        payload = {
            "trace": trace.to_dict(),
            "updated_at": datetime.now().isoformat(),
        }
        self.store.put(self.COLLECTION, trace.trace_id, payload)

    def get(self, trace_id: str) -> Optional[AgentTrace]:
        """按 trace_id 读取轨迹。"""
        data = self.store.get(self.COLLECTION, trace_id)
        if not data or "trace" not in data:
            return None
        try:
            return AgentTrace.from_dict(data["trace"])
        except Exception:
            return None

    def list(
        self,
        session_id: Optional[str] = None,
        task_prefix: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentTrace]:
        """列出轨迹，支持按 session_id、task 前缀、时间范围过滤。

        当前在内存中过滤；未来可在 SQLiteBackend 中加索引优化。
        """
        keys = self.store.list_keys(self.COLLECTION)
        traces: list[AgentTrace] = []

        for key in keys:
            trace = self.get(key)
            if trace is None:
                continue
            if session_id is not None and trace.session_id != session_id:
                continue
            if task_prefix is not None and not trace.task.startswith(task_prefix):
                continue
            if start_time is not None and trace.start_time < start_time:
                continue
            if end_time is not None and trace.start_time > end_time:
                continue
            traces.append(trace)

        traces.sort(key=lambda t: t.start_time, reverse=True)
        return traces[offset:offset + limit]

    def list_sessions(self) -> list[str]:
        """返回所有出现过的 session_id 列表（去重）。"""
        keys = self.store.list_keys(self.COLLECTION)
        sessions: set[str] = set()
        for key in keys:
            trace = self.get(key)
            if trace and trace.session_id:
                sessions.add(trace.session_id)
        return sorted(sessions)

    def delete(self, trace_id: str) -> bool:
        """删除指定轨迹。"""
        return self.store.delete(self.COLLECTION, trace_id)

    def count(self) -> int:
        """返回已保存的轨迹总数。"""
        return self.store.count(self.COLLECTION)

    def get_metrics(self, trace_id: str) -> dict[str, Any]:
        """快速计算指定轨迹的基础指标，不依赖完整 TraceEvaluator。"""
        trace = self.get(trace_id)
        if trace is None:
            return {"error": "trace not found"}

        events = trace.events
        tool_events = [ev for ev in events if ev.type.value.endswith("_end") and ev.type.value.startswith("tool_")]
        llm_events = [ev for ev in events if ev.type == TraceEventType.LLM_END]
        error_events = [ev for ev in events if ev.type == TraceEventType.ERROR]
        replan_events = [ev for ev in events if ev.type == TraceEventType.REPLAN]
        retry_events = [ev for ev in events if ev.type == TraceEventType.RETRY]

        tool_stats: dict[str, int] = {}
        for ev in tool_events:
            name = ev.data.get("tool_name", "unknown")
            tool_stats[name] = tool_stats.get(name, 0) + 1

        total_latency_ms = 0.0
        end_time = trace.end_time or datetime.now().isoformat()
        try:
            start_dt = datetime.fromisoformat(trace.start_time)
            end_dt = datetime.fromisoformat(end_time)
            total_latency_ms = (end_dt - start_dt).total_seconds() * 1000
        except Exception:
            pass

        return {
            "trace_id": trace_id,
            "total_events": len(events),
            "llm_calls": len(llm_events),
            "tool_calls": len(tool_events),
            "tool_stats": tool_stats,
            "error_count": len(error_events),
            "replan_count": len(replan_events),
            "retry_count": len(retry_events),
            "success": trace.agent_result.success if trace.agent_result else None,
            "duration_ms": round(total_latency_ms, 2),
        }

    def close(self) -> None:
        """关闭底层存储连接。"""
        self.store.close()
