"""协作工具 + 事件触发工具 + 用户沟通工具 — 补全书第 4 章五类工具"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional
from uuid import uuid4

from paperwise.tools.base import BaseTool, ToolDefinition
from paperwise.core.types import ToolRisk


# ══════════ 协作工具 ══════════

class SpawnSubAgentTool(BaseTool):
    """创建子 Agent 处理独立子任务。"""

    def __init__(self, workspace, llm_client=None):
        super().__init__(workspace)
        self._llm = llm_client  # 注入 LLM 客户端

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="spawn_subagent",
            description=(
                "创建一个子 Agent 来独立处理指定的子任务。"
                "当你需要并行处理多个独立任务，或将大任务分解为子任务时使用。"
                "子 Agent 拥有独立上下文，结果以结构化摘要返回。"
                "DO NOT use for: 需要主 Agent 上下文的任务、简单的单步操作。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "子任务的完整描述"},
                    "name": {"type": "string", "description": "子 Agent 名称，如 'table-extractor'"},
                    "max_steps": {"type": "integer", "description": "最大步数，默认 10", "default": 10},
                },
                "required": ["task", "name"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, task: str, name: str, max_steps: int = 10) -> str:
        """创建子 Agent 并异步执行。实际创建独立的 Agent 实例。

        对应书中 5.1.5 节 + 10.4.4 节：子 Agent 委托。
        """
        import asyncio

        # 构建子 Agent 配置
        try:
            from paperwise.core.agent import Agent, AgentConfig
            from paperwise.tools.registry import ToolRegistry
            from paperwise.harness.harness import Harness

            sub_workspace = self.workspace / f"subagent_{name}"
            sub_workspace.mkdir(parents=True, exist_ok=True)

            sub_tools = ToolRegistry.create_default(sub_workspace)
            # 注册消息邮箱 + receive_message 工具
            from paperwise.core.bus import AgentBus
            AgentBus.instance().register(name)
            sub_tools.register(ReceiveMessageTool(sub_workspace, agent_name=name))
            for tool_name in sub_tools.list_names():
                sub_tools.get(tool_name)._agent_name = name
            sub_harness = Harness(sub_workspace, max_steps=max_steps)

            config = AgentConfig(
                name=name,
                system_prompt=f"你是子 Agent '{name}'，负责完成以下具体任务。完成后返回简洁的结果摘要。",
                model="deepseek-chat",
                max_steps=max_steps,
            )

            # 使用注入的 LLM 客户端，否则回退
            if self._llm:
                llm = self._llm
            else:
                from paperwise.core.llm_client import LLMClient
                from paperwise.config.settings import get_settings
                settings = get_settings()
                llm = LLMClient(provider="openai_compatible", model="deepseek-chat")

            sub_agent = Agent(
                config=config, tools=sub_tools, llm_client=llm,
                harness=sub_harness, workspace_dir=sub_workspace,
            )

            result = await sub_agent.run(task)

            return (
                f"[子 Agent '{name}' 执行完成]\n"
                f"步数: {result.steps}\n"
                f"工具: {dict(result.tool_stats)}\n"
                f"结果:\n{result.final_output[:1000]}"
            )
        except Exception as e:
            return (
                f"[子 Agent '{name}' 已创建但执行出错]\n"
                f"错误: {type(e).__name__}: {str(e)[:200]}\n"
                f"任务: {task[:200]}\n"
                f"请主 Agent 直接处理该任务。"
            )


class SendMessageTool(BaseTool):
    """向其他 Agent 发送消息 — 通过 AgentBus 或回调。"""

    def __init__(self, workspace, callback=None):
        super().__init__(workspace)
        self._callback = callback

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="send_message_to_agent",
            description=(
                "向另一个 Agent 发送消息或指令。"
                "用于多 Agent 协作场景中的显式通信。"
                "DO NOT use for: 向用户发送消息（使用 ask_user）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "目标 Agent 名称"},
                    "message": {"type": "string", "description": "消息内容"},
                },
                "required": ["agent_name", "message"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, agent_name: str, message: str) -> str:
        # 真实通信：优先投递到 AgentBus 邮箱
        from paperwise.core.bus import AgentBus
        bus = AgentBus.instance()
        if bus.is_registered(agent_name):
            sender = getattr(self, "_agent_name", "main")
            delivered = bus.send(agent_name, {
                "from": sender,
                "message": message,
                "ts": datetime.now().isoformat(),
            })
            if delivered:
                return (
                    f"[消息已投递到 Agent '{agent_name}' 的邮箱]\n"
                    f"来自: {sender}\n内容: {message[:300]}"
                )

        # 真实通信：通过回调发送事件
        if self._callback:
            try:
                self._callback("agent_msg", json.dumps({"to": agent_name, "msg": message[:500]}))
            except Exception:
                pass
        return (
            f"[目标 Agent '{agent_name}' 未注册邮箱，消息仅记录]\n"
            f"内容: {message[:300]}"
        )


class ReceiveMessageTool(BaseTool):
    """读取发给当前 Agent 的待处理消息（AgentBus 邮箱）。"""

    def __init__(self, workspace, agent_name: str = "main"):
        super().__init__(workspace)
        self._agent_name = agent_name
        from paperwise.core.bus import AgentBus
        AgentBus.instance().register(agent_name)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="receive_message",
            description=(
                "读取其他 Agent 发送给你的待处理消息。\n"
                "当你在协作任务中等待 Manager 或其他 Agent 的指令、"
                "反馈或新任务时使用。\n"
                "DO NOT use for: 向用户提问（使用 ask_user）、"
                "向其他 Agent 发送消息（使用 send_message_to_agent）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "number",
                        "description": "等待秒数（0 表示立即返回）。默认 0。",
                        "default": 0,
                    },
                },
                "required": [],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, timeout: float = 0.0) -> str:
        from paperwise.core.bus import AgentBus
        bus = AgentBus.instance()
        wait = max(0.0, min(float(timeout or 0), 30.0))
        msg = await bus.receive(self._agent_name, timeout=wait if wait > 0 else 0.05)
        if msg is None:
            return "[无新消息]"
        return (
            f"[新消息]\n"
            f"来自: {msg.get('from', '?')}\n"
            f"时间: {msg.get('ts', '')}\n"
            f"内容: {msg.get('message', '')}"
        )


# ══════════ 事件触发工具 ══════════

class SetTimerTool(BaseTool):
    """设置定时器 — 到时间时通过回调通知。"""

    _timers: dict[str, asyncio.Task] = {}

    def __init__(self, workspace, callback=None):
        super().__init__(workspace)
        self._callback = callback

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="set_timer",
            description=(
                "设置定时器，在指定时间后触发通知。"
                "用于需要在未来某个时间点执行操作的场景。"
                "例如：'30分钟后提醒我检查分析进度'。"
                "DO NOT use for: 即时任务、同步等待。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "延迟秒数"},
                    "message": {"type": "string", "description": "触发时显示的消息"},
                },
                "required": ["seconds", "message"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, seconds: int, message: str) -> str:
        timer_id = uuid4().hex[:8]
        async def _fire():
            await asyncio.sleep(seconds)
            if self._callback:
                try:
                    self._callback("timer", json.dumps({"id": timer_id, "message": message}))
                except Exception:
                    pass
        self._timers[timer_id] = asyncio.create_task(_fire())
        return f"定时器已设置 (ID: {timer_id})：{seconds}s 后触发 — {message}"


class MonitorShellTool(BaseTool):
    """监控后台命令的执行状态。"""

    _tasks: dict[str, asyncio.subprocess.Process] = {}

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="monitor_shell",
            description=(
                "启动并监控后台运行的 shell 命令。命令在后台执行，"
                "你可以继续其他工作，稍后查询状态和结果。"
                "用于长时间任务（如 PDF 解析、数据下载）。"
                "DO NOT use for: 需要即时结果的命令（直接用 bash 工具）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要后台执行的 shell 命令"},
                    "task_id": {"type": "string", "description": "任务标识（用于后续查询），默认自动生成"},
                },
                "required": ["command"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, command: str, task_id: str = None) -> str:
        if task_id is None:
            import uuid
            task_id = uuid.uuid4().hex[:8]

        # 安全检查
        dangerous = ["rm", "sudo", "chmod", "mkfs", "dd", "shutdown"]
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word in dangerous:
            return f"[Blocked] 命令 '{first_word}' 不允许在后台执行"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            self._tasks[task_id] = proc

            # 启动后台监控
            async def _monitor():
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=3600,
                    )
                    output = (stdout or b"").decode("utf-8", errors="replace")[:2000]
                    if stderr:
                        output += "\n[stderr]\n" + (stderr or b"").decode("utf-8", errors="replace")[:500]
                    self._tasks[task_id] = output  # 替换为结果字符串
                except asyncio.TimeoutError:
                    self._tasks[task_id] = "[超时] 任务执行超过 1 小时"
                except Exception as e:
                    self._tasks[task_id] = f"[错误] {e}"

            asyncio.create_task(_monitor())

            return (
                f"[后台任务已启动]\n"
                f"任务 ID: {task_id}\n"
                f"命令: {command[:200]}\n"
                f"状态: 运行中（稍后使用 monitor_shell 查询，或等待自动完成）"
            )
        except Exception as e:
            return f"[错误] 后台任务启动失败: {e}"

    async def check_status(self, task_id: str) -> str:
        """查询后台任务状态。"""
        if task_id not in self._tasks:
            return f"[未找到] 任务 '{task_id}' 不存在或已完成"

        result = self._tasks[task_id]
        if isinstance(result, str):
            return f"[已完成] 任务 '{task_id}':\n{result}"
        elif isinstance(result, asyncio.subprocess.Process):
            if result.returncode is not None:
                return f"[已完成] 任务 '{task_id}' 退出码: {result.returncode}"
            return f"[运行中] 任务 '{task_id}' 仍在执行"
        return f"[未知状态] 任务 '{task_id}'"


# ══════════ 用户沟通工具 ══════════

class AskUserTool(BaseTool):
    """在关键决策点主动询问用户。"""

    def __init__(self, workspace, callback=None):
        super().__init__(workspace)
        self._callback = callback  # 异步回调，等待用户响应

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ask_user",
            description=(
                "在需要用户决策或澄清意图时主动提问。"
                "用于：任务目标不明确需要澄清、高风险操作前确认、"
                "发现多种可行方案需要用户选择。"
                "DO NOT use for: 知识性问题（应自行查找）、过度确认（影响体验）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "明确的问题描述"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选的答案选项（最多 5 个）",
                    },
                },
                "required": ["question"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, question: str, options: list[str] = None) -> str:
        opts = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options or []))
        # 通过回调通知前端（WebSocket 或 CLI 显示）
        if self._callback:
            try:
                self._callback("ask_user", json.dumps({"question": question, "options": options or []}))
            except Exception:
                pass
        return (
            f"[已向用户提问]\n\n"
            f"{question}\n\n"
            f"{opts}\n\n"
            f"[请用户回复以继续...]"
        )


class NotifyUserTool(BaseTool):
    """向用户发送通知或进度更新。"""

    def __init__(self, workspace, callback=None):
        super().__init__(workspace)
        self._callback = callback  # 外部注入的回调，用于实际通知

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="notify_user",
            description=(
                "向用户发送主动通知——报告进度、关键发现或请求反馈。"
                "用于长任务中定期更新用户，或发现重要信息时主动告知。"
                "DO NOT use for: 最终答案（直接输出即可）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "通知内容"},
                    "level": {
                        "type": "string",
                        "enum": ["info", "success", "warning", "error"],
                        "description": "通知级别", "default": "info",
                    },
                },
                "required": ["message"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, message: str, level: str = "info") -> str:
        icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
        # 真实通知：通过回调发送事件
        if self._callback:
            try:
                self._callback("notify", f"[{level}] {message}")
            except Exception:
                pass
        return f"[{icons.get(level, 'ℹ️')} 通知已发送] {message}"
