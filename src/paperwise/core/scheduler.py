"""主动调度器 — 系统级定时器与事件注入。

对应书中 4.7 节：事件驱动的异步 Agent。
Agent 通过 set_timer / monitor_shell 注册任务，调度器到期后
通过回调把事件注入活跃 Session（广播 + 追加上下文），
使 Agent 从"被动等待用户"变为"主动服务"。

用法：
    scheduler = Scheduler.instance()
    scheduler.start()                       # 应用启动时
    scheduler.add_timer("t1", 30, "检查进度", session_id="abc", callback=fn)
    await scheduler.stop()                  # 应用关闭时
"""

import asyncio
import logging
import time
import uuid
from typing import Callable, Optional


class Scheduler:
    """进程内主动调度器（单例）。"""

    _instance: Optional["Scheduler"] = None
    POLL_INTERVAL = 0.2  # 秒

    def __init__(self):
        self._timers: dict[str, dict] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def instance(cls) -> "Scheduler":
        if cls._instance is None:
            cls._instance = Scheduler()
        return cls._instance

    def start(self) -> None:
        """启动后台轮询任务（幂等）。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止后台任务。"""
        self._running = False
        if self._task:
            task = self._task
            self._task = None
            if not task.done():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    def add_timer(self, delay_seconds: float, message: str, session_id: str,
                  callback: Optional[Callable[[dict], None]] = None,
                  timer_id: Optional[str] = None) -> str:
        """注册一个定时器。

        Args:
            delay_seconds: 延迟秒数
            message: 触发时注入的消息
            session_id: 目标会话
            callback: 触发回调（接收事件 dict），可为 async

        Returns:
            timer_id
        """
        tid = timer_id or uuid.uuid4().hex[:8]
        self._timers[tid] = {
            "due": time.monotonic() + max(0.0, float(delay_seconds)),
            "message": message,
            "session_id": session_id,
            "callback": callback,
            "kind": "timer",
        }
        return tid

    def add_monitor(self, message: str, session_id: str,
                    callback: Optional[Callable[[dict], None]] = None,
                    task_id: Optional[str] = None) -> str:
        """注册一个后台任务完成事件（由 monitor_shell 完成时触发）。"""
        tid = task_id or uuid.uuid4().hex[:8]
        self._timers[tid] = {
            "due": time.monotonic(),  # 立即就绪，等待手动触发
            "message": message,
            "session_id": session_id,
            "callback": callback,
            "kind": "monitor",
            "ready": False,
        }
        return tid

    def fire_monitor(self, task_id: str, output: str = "") -> None:
        """手动触发 monitor 事件（由 monitor_shell 完成任务时调用）。"""
        entry = self._timers.get(task_id)
        if not entry or entry.get("kind") != "monitor":
            return
        entry["due"] = time.monotonic()
        entry["ready"] = True
        entry["output"] = output

    def cancel(self, timer_id: str) -> bool:
        return self._timers.pop(timer_id, None) is not None

    def pending(self) -> int:
        return len(self._timers)

    async def _loop(self) -> None:
        while self._running:
            now = time.monotonic()
            fired = [
                tid for tid, t in self._timers.items()
                if t.get("ready", True) and t["due"] <= now
            ]
            for tid in fired:
                entry = self._timers.pop(tid, None)
                if not entry:
                    continue
                event = {
                    "type": entry.get("kind", "timer"),
                    "id": tid,
                    "session_id": entry["session_id"],
                    "message": entry["message"],
                    "output": entry.get("output", ""),
                }
                try:
                    result = entry["callback"](event) if entry.get("callback") else None
                    if result is not None and hasattr(result, "__await__"):
                        await result
                except Exception:
                    logging.getLogger("paperwise").exception(
                        "Scheduler callback failed for %s", tid)
            await asyncio.sleep(self.POLL_INTERVAL)
