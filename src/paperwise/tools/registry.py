"""工具注册中心 — MCP 兼容的工具发现与管理

对应书中 4.3 节：工具生态与 MCP
"""

from pathlib import Path
from typing import Optional

from paperwise.tools.base import BaseTool, ToolDefinition


class ToolRegistry:
    """工具注册中心 — 管理所有可用工具。

    支持：
    - 注册/注销工具
    - 按名称查找工具
    - 导出 API 格式的工具定义列表（OpenAI function calling 格式）
    - 分批暴露工具（渐进式披露）
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._tools: dict[str, BaseTool] = {}
        self._active_tools: set[str] = set()  # 当前上下文已暴露的工具

    # === 注册 ===

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例。"""
        name = tool.definition.name
        self._tools[name] = tool
        self._active_tools.add(name)

    def register_all(self, tools: list[BaseTool]) -> None:
        """批量注册工具。"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """注销工具。"""
        self._tools.pop(name, None)
        self._active_tools.discard(name)

    # === 查找 ===

    def get(self, name: str) -> BaseTool:
        """按名称获取工具。KeyError 如果不存在。"""
        if name not in self._tools:
            available = list(self._tools.keys())
            raise KeyError(f"Tool '{name}' not found. Available: {available}")
        return self._tools[name]

    def list_names(self) -> list[str]:
        """列出所有工具名称。"""
        return sorted(self._tools.keys())

    def list_active_names(self) -> list[str]:
        """列出当前激活的工具名称。"""
        return sorted(self._active_tools)

    # === 导出工具定义 ===

    def get_definitions(self, names: Optional[list[str]] = None) -> list[dict]:
        """获取 OpenAI function calling 格式的工具定义列表。

        Args:
            names: 指定工具名称列表，None 表示所有已注册工具
        """
        target = names or list(self._active_tools)
        definitions = []
        for name in target:
            if name in self._tools:
                tool = self._tools[name]
                defn = tool.definition
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": defn.name,
                        "description": defn.description,
                        "parameters": defn.parameters,
                    },
                })
        return definitions

    def get_risk_level(self, name: str) -> str:
        """获取工具的风险等级。"""
        if name in self._tools:
            return self._tools[name].definition.risk.value
        return "unknown"

    # === 渐进式披露 ===

    def get_catalog(self) -> str:
        """生成工具目录摘要（仅名称 + 一句话描述，约 200 tokens）。

        对应书中 2.5 节 Skills 渐进式披露和 4.8 节动态工具发现。
        """
        lines = ["<available_tools>"]
        for name in sorted(self._active_tools):
            if name in self._tools:
                desc = self._tools[name].definition.description.split(".")[0]
                lines.append(f"  - {name}: {desc.strip()}")
        lines.append("</available_tools>")
        return "\n".join(lines)

    # === Skill 注入 ===

    def set_skill_loader(self, loader) -> None:
        """将 SkillLoader 注入到 SkillListTool 和 SkillLoadTool 中。"""
        for name in ("skill_list", "skill_load"):
            tool = self._tools.get(name)
            if tool and hasattr(tool, "_loader"):
                tool._loader = loader

    # === 工厂方法 ===

    def allow_read_path(self, path: Path) -> None:
        """将外部路径添加到所有工具的读取白名单。"""
        for tool in self._tools.values():
            tool.allow_read_path(path)

    @classmethod
    def create_default(cls, workspace: Path) -> "ToolRegistry":
        """创建包含全部五类工具的注册中心。

        对应书中 4.1 节五类工具 + 5.1.1 节七核心 Coding Agent 工具
        """
        from paperwise.tools.file_tools import ReadFileTool, WriteFileTool, EditFileTool
        from paperwise.tools.search_tools import GlobTool, GrepTool
        from paperwise.tools.exec_tools import CodeInterpreterTool, BashTool
        from paperwise.tools.access_tool import RequestFileAccessTool
        from paperwise.tools.skill_tools import SkillListTool, SkillLoadTool
        from paperwise.tools.collab_tools import (
            SpawnSubAgentTool, SendMessageTool, SetTimerTool,
            MonitorShellTool, AskUserTool, NotifyUserTool,
        )

        registry = cls(workspace)
        # 感知工具
        registry.register_all([
            ReadFileTool(workspace), GlobTool(workspace), GrepTool(workspace),
        ])
        # 执行工具
        registry.register_all([
            WriteFileTool(workspace), EditFileTool(workspace),
            CodeInterpreterTool(workspace), BashTool(workspace),
        ])
        # Skill 工具（渐进式披露）
        # skill_loader 由 AgentSession/Agent 后续注入
        registry.register_all([
            SkillListTool(workspace), SkillLoadTool(workspace),
        ])
        # 文件访问申请工具（需要 ToolRegistry 引用以广播白名单）
        registry.register(RequestFileAccessTool(workspace, tool_registry=registry))
        # 协作工具
        registry.register_all([
            SpawnSubAgentTool(workspace), SendMessageTool(workspace),
        ])
        # 事件触发工具
        registry.register_all([
            SetTimerTool(workspace), MonitorShellTool(workspace),
        ])
        # 用户沟通工具
        registry.register_all([
            AskUserTool(workspace), NotifyUserTool(workspace),
        ])
        return registry
