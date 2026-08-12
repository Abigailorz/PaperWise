"""Agent 核心引擎 — 类型定义、LLM 客户端"""

# 先导入无依赖的基础类型
from paperwise.core.types import (
    Role, Message, ToolCall, ToolResult, AgentState, AgentConfig, AgentResult, ParsedPaper
)
from paperwise.core.llm_client import LLMClient, LLMResponse

# Agent 类有工具系统依赖，延迟导入
def get_agent(*args, **kwargs):
    """延迟导入 Agent 以避免循环依赖。"""
    from paperwise.core.agent import Agent
    return Agent(*args, **kwargs)


__all__ = [
    "Role", "Message", "ToolCall", "ToolResult",
    "AgentState", "AgentConfig", "AgentResult", "ParsedPaper",
    "LLMClient", "LLMResponse", "get_agent",
]
