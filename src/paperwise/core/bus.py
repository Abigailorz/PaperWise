"""Agent 间消息总线 — 进程内 asyncio 队列注册表。

对应书中 10.4.2 节：Agent 间的通信与控制。
Manager 可通过 send() 向任意已注册的子 Agent 投递消息，
子 Agent 通过 receive_message 工具读取自己的邮箱。

用法：
    bus = AgentBus.instance()
    bus.register("analyst")
    bus.send("analyst", {"from": "manager", "message": "..."})
    msg = await bus.receive("analyst", timeout=5.0)
"""

import asyncio
from typing import Optional


class AgentBus:
    """轻量进程内消息总线（单例）。"""

    _instance: Optional["AgentBus"] = None

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}

    @classmethod
    def instance(cls) -> "AgentBus":
        """获取全局单例。"""
        if cls._instance is None:
            cls._instance = AgentBus()
        return cls._instance

    def register(self, name: str) -> None:
        """为 Agent 创建邮箱。重复注册为幂等操作。"""
        if name not in self._queues:
            self._queues[name] = asyncio.Queue()

    def unregister(self, name: str) -> None:
        """移除 Agent 邮箱。"""
        self._queues.pop(name, None)

    def is_registered(self, name: str) -> bool:
        return name in self._queues

    def send(self, to: str, message: dict) -> bool:
        """向目标 Agent 投递消息。

        Returns:
            True 表示已投递；False 表示目标未注册（消息未送达）。
        """
        queue = self._queues.get(to)
        if queue is None:
            return False
        queue.put_nowait(message)
        return True

    async def receive(self, name: str, timeout: float = 5.0) -> Optional[dict]:
        """读取一条消息。超时返回 None。"""
        queue = self._queues.get(name)
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def pending(self, name: str) -> int:
        """查询待处理消息数。"""
        queue = self._queues.get(name)
        return queue.qsize() if queue else 0
