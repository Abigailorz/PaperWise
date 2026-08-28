"""核心类型定义 — Message, ToolCall, AgentState 等"""

from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum
from datetime import datetime
from pathlib import Path


class Role(str, Enum):
    """消息角色 — 对应 OpenAI Chat Completions API"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolRisk(str, Enum):
    """工具风险等级 — 对应书中 1.2.6 节护栏"""
    LOW = "low"          # read_file, grep, glob
    MEDIUM = "medium"    # write_file, edit_file, code_interpreter, bash
    HIGH = "high"        # delete_file, send_email


@dataclass
class ToolCall:
    """LLM 发出的工具调用请求"""
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_call_id: str
    name: str
    output: str
    is_error: bool = False
    truncated: bool = False
    full_output_path: Optional[Path] = None


@dataclass
class Message:
    """对话中的一条消息"""
    role: Role
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None  # for Role.TOOL messages
    reasoning: Optional[str] = None     # thinking/CoT content

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "tool_calls": [tc.to_dict() if hasattr(tc, "to_dict") else {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in (self.tool_calls or [])],
            "tool_call_id": self.tool_call_id,
            "reasoning": self.reasoning,
        }


@dataclass
class AgentState:
    """Agent 运行时状态"""
    messages: list[Message] = field(default_factory=list)
    tool_call_count: dict[str, int] = field(default_factory=dict)
    current_step: int = 0
    max_steps: int = 25
    tokens_used: int = 0
    token_limit: int = 180_000
    cost_used: float = 0.0
    cost_limit: float = 5.0
    start_time: datetime = field(default_factory=datetime.now)
    workspace_dir: Optional[Path] = None
    task_description: str = ""
    todo_items: list[dict] = field(default_factory=list)  # [{text: str, status: pending|done|in_progress}]


@dataclass
class AgentConfig:
    """Agent 实例配置"""
    name: str = "paperwise-agent"
    system_prompt: str = ""
    model: str = "deepseek-chat"
    max_steps: int = 25
    token_budget: int = 180_000
    temperature: float = 0.3
    allowed_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    enable_plan: bool = True
    enable_budget_note: bool = True
    enable_judge_review: bool = True
    enable_hierarchical_memory: bool = True
    enable_orchestration: bool = True


@dataclass
class AgentResult:
    """Agent 执行任务的结果"""
    final_output: str = ""
    messages: list[Message] = field(default_factory=list)
    steps: int = 0
    tool_stats: dict[str, int] = field(default_factory=dict)
    success: bool = True
    error_message: str = ""
    tokens_used: int = 0
    trace_id: str = ""  # 关联的 AgentTrace ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_output": self.final_output,
            "messages": [m.to_dict() if hasattr(m, "to_dict") else {"role": m.role.value, "content": m.content} for m in self.messages],
            "steps": self.steps,
            "tool_stats": dict(self.tool_stats),
            "success": self.success,
            "error_message": self.error_message,
            "tokens_used": self.tokens_used,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResult":
        messages = []
        for m in data.get("messages", []):
            if isinstance(m, dict):
                role = Role(m.get("role", "assistant"))
                content = m.get("content")
                tool_calls = None
                tool_call_id = m.get("tool_call_id")
                if m.get("tool_calls"):
                    tool_calls = [ToolCall(**tc) for tc in m["tool_calls"]]
                messages.append(Message(role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id))
        return cls(
            final_output=data.get("final_output", ""),
            messages=messages,
            steps=data.get("steps", 0),
            tool_stats=data.get("tool_stats", {}),
            success=data.get("success", True),
            error_message=data.get("error_message", ""),
            tokens_used=data.get("tokens_used", 0),
            trace_id=data.get("trace_id", ""),
        )


@dataclass
class ParsedPaper:
    """解析后的论文数据结构"""
    paper_id: str = ""
    output_dir: Path = field(default_factory=Path)
    metadata: dict = field(default_factory=dict)
    text: str = ""
    structure: dict = field(default_factory=dict)
    figures: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    formulas: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)


class TraceEventType(str, Enum):
    """Agent 执行轨迹中的事件类型"""
    TRACE_START = "trace_start"
    TRACE_END = "trace_end"
    ROUTER_DECISION = "router_decision"
    PLAN_GENERATED = "plan_generated"
    CONTEXT_ASSEMBLED = "context_assembled"
    STEP_START = "step_start"
    STEP_END = "step_end"
    LLM_START = "llm_start"
    LLM_END = "llm_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    RETRY = "retry"
    REPLAN = "replan"
    NODE_START = "node_start"
    NODE_END = "node_end"
    NODE_FAILED = "node_failed"
    NODE_REPLANNED = "node_replanned"
    NODE_DONE = "node_done"
    REVIEW_ROUND = "review_round"
    EXIT_CONDITION = "exit_condition"
    MEMORY_EXTRACT = "memory_extract"
    COMPRESSION = "compression"
    ERROR = "error"
    RESULT = "result"


@dataclass
class TraceEvent:
    """执行轨迹中的单个事件"""
    event_id: str
    trace_id: str
    type: TraceEventType
    timestamp: str
    step: Optional[int] = None
    node_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "step": self.step,
            "node_id": self.node_id,
            "parent_event_id": self.parent_event_id,
            "data": self.data,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        return cls(
            event_id=data["event_id"],
            trace_id=data["trace_id"],
            type=TraceEventType(data.get("type", "trace_start")),
            timestamp=data["timestamp"],
            step=data.get("step"),
            node_id=data.get("node_id"),
            parent_event_id=data.get("parent_event_id"),
            data=data.get("data", {}),
            latency_ms=data.get("latency_ms"),
        )


@dataclass
class AgentTrace:
    """Agent 单次执行的完整轨迹"""
    trace_id: str
    task: str
    session_id: Optional[str] = None
    user_id: str = "default"
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    end_time: Optional[str] = None
    events: list[TraceEvent] = field(default_factory=list)
    agent_result: Optional[AgentResult] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_event(
        self,
        event_type: TraceEventType,
        data: Optional[dict[str, Any]] = None,
        step: Optional[int] = None,
        node_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
    ) -> TraceEvent:
        """向轨迹中添加一个事件"""
        import uuid
        event = TraceEvent(
            event_id=f"ev_{uuid.uuid4().hex[:8]}",
            trace_id=self.trace_id,
            type=event_type,
            timestamp=datetime.now().isoformat(),
            step=step,
            node_id=node_id,
            parent_event_id=parent_event_id,
            data=data or {},
            latency_ms=latency_ms,
        )
        self.events.append(event)
        return event

    def find_events(
        self,
        event_type: Optional[TraceEventType] = None,
        node_id: Optional[str] = None,
        step: Optional[int] = None,
    ) -> list[TraceEvent]:
        """按条件检索事件"""
        results = []
        for ev in self.events:
            if event_type and ev.type != event_type:
                continue
            if node_id is not None and ev.node_id != node_id:
                continue
            if step is not None and ev.step != step:
                continue
            results.append(ev)
        return results

    def last_event(self, event_type: TraceEventType) -> Optional[TraceEvent]:
        """返回指定类型的最后一个事件"""
        matches = self.find_events(event_type=event_type)
        return matches[-1] if matches else None

    def merge_child_trace(
        self,
        child_trace: "AgentTrace",
        parent_event: Optional[TraceEvent] = None,
    ) -> None:
        """将子 Agent 的轨迹合并到当前轨迹中"""
        parent_id = parent_event.event_id if parent_event else None
        for ev in child_trace.events:
            # 子轨迹的第一个事件如果类型是 trace_start，则转换为 node_start
            if ev.type == TraceEventType.TRACE_START and parent_id:
                continue
            merged = TraceEvent(
                event_id=ev.event_id,
                trace_id=self.trace_id,
                type=ev.type,
                timestamp=ev.timestamp,
                step=ev.step,
                node_id=ev.node_id or child_trace.trace_id,
                parent_event_id=parent_id,
                data={**ev.data, "child_trace_id": child_trace.trace_id},
                latency_ms=ev.latency_ms,
            )
            self.events.append(merged)

    def finish(self, agent_result: Optional[AgentResult] = None) -> None:
        """结束当前轨迹"""
        self.end_time = datetime.now().isoformat()
        if agent_result:
            self.agent_result = agent_result

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task": self.task,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "events": [ev.to_dict() for ev in self.events],
            "agent_result": self.agent_result.to_dict() if self.agent_result else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTrace":
        result_data = data.get("agent_result")
        agent_result = AgentResult.from_dict(result_data) if result_data else None
        return cls(
            trace_id=data["trace_id"],
            task=data["task"],
            session_id=data.get("session_id"),
            user_id=data.get("user_id", "default"),
            start_time=data.get("start_time", datetime.now().isoformat()),
            end_time=data.get("end_time"),
            events=[TraceEvent.from_dict(ev) for ev in data.get("events", [])],
            agent_result=agent_result,
            metadata=data.get("metadata", {}),
        )
