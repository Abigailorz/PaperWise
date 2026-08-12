"""工具系统 — MCP 兼容的 7 核心 Coding Agent 工具"""

from paperwise.tools.base import BaseTool, ToolDefinition

# ToolRegistry 延迟导入，避免循环
def get_registry(workspace=None):
    from paperwise.tools.registry import ToolRegistry
    return ToolRegistry(workspace) if workspace else ToolRegistry

__all__ = ["BaseTool", "ToolDefinition", "get_registry"]
