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


@dataclass
class AgentResult:
    """Agent 执行任务的结果"""
    final_output: str = ""
    messages: list[Message] = field(default_factory=list)
    steps: int = 0
    tool_stats: dict[str, int] = field(default_factory=dict)
    success: bool = True
    error_message: str = ""


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
