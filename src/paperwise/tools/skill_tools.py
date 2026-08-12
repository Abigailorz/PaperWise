"""Skill 工具 — skill_list / skill_load

Agent 可以通过这两个工具主动发现和加载 Skills，
而非仅依赖 system prompt 中的被动文字目录。

对应书中 2.5 节：Agent Skills 渐进式披露
"""

from pathlib import Path
from typing import Optional

from paperwise.tools.base import BaseTool, ToolDefinition
from paperwise.core.types import ToolRisk


class SkillListTool(BaseTool):
    """列出所有可用的 Agent Skills（结构化列表）。

    Agent 在开始任务时调用此工具，了解可用的专业能力模块，
    然后根据任务需要调用 skill_load 加载对应 Skill。
    """

    def __init__(self, workspace: Path, skill_loader=None):
        super().__init__(workspace)
        self._loader = skill_loader

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_list",
            description=(
                "列出所有可用的 Agent Skills。每个 Skill 是一个专业能力模块，"
                "包含特定领域的工作流程和最佳实践。\n"
                "当你需要了解：\n"
                "- 有哪些专业能力可供使用\n"
                "- 某个 Skill 的简要描述\n"
                "- 应该为当前任务加载哪个 Skill\n"
                "时使用此工具。\n"
                "查看到合适的 Skill 后，使用 skill_load 加载它。\n"
                "DO NOT use for: 执行具体任务（使用 read_file/write_file 等）、"
                "搜索文件内容（使用 grep）。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self) -> str:
        if not self._loader:
            return "[Skill System] Skill loader not initialized."

        lines = ["# Available Skills\n"]
        for skill in self._loader._catalog:
            name = skill["name"]
            desc = skill["description"][:120]
            lines.append(f"## {name}")
            lines.append(f"  {desc}")
            lines.append(f"  加载命令: skill_load(name=\"{name}\")")
            lines.append("")

        if not self._loader._catalog:
            lines.append("(No skills available)")

        lines.append("\n---")
        lines.append("使用 skill_load(name=\"...\") 加载具体 Skill。")
        lines.append("加载后请严格遵循 Skill 中的工作流程和最佳实践。")

        return "\n".join(lines)


class SkillLoadTool(BaseTool):
    """按需加载指定 Skill 的完整内容。

    三层渐进式披露的第二层：
    1. system prompt 中的 catalog（元数据目录，~200 tokens）
    2. 本工具返回完整 SKILL.md（核心流程，按需加载）
    3. Skill 中引用的子文档（细则文档，深入时按需）

    调用后 Agent 应在当前对话中遵循 Skill 的指令。
    """

    def __init__(self, workspace: Path, skill_loader=None):
        super().__init__(workspace)
        self._loader = skill_loader
        self._loaded_skills: set[str] = set()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_load",
            description=(
                "加载并激活指定的 Agent Skill。Skill 是包含专业工作流程和"
                "最佳实践的指令文档，加载后你应在当前任务中严格遵循其指导。\n\n"
                "何时使用：\n"
                "- 开始分析学术论文时 → 加载 academic-reading\n"
                "- 生成正式报告时 → 加载 report-generation\n"
                "- 验证报告质量时 → 加载 verification\n"
                "- 用户说\"严格按流程分析\" → 加载对应 Skill\n\n"
                "加载后：仔细阅读 Skill 内容，在后续工作中遵循其工作流程。\n"
                "DO NOT use for: 临时性简单任务（无需加载 Skill）、"
                "已在当前对话中加载过的 Skill（无需重复加载）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill 名称。使用 skill_list 查看可用 Skill 列表。"
                    },
                },
                "required": ["name"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, name: str) -> str:
        if not self._loader:
            return f"[Error] Skill loader not available."

        content = self._loader.load_skill(name)
        if not content:
            available = self._loader.list_skills()
            return (
                f"[Error] Skill '{name}' not found.\n"
                f"Available skills: {', '.join(available)}\n"
                f"Use skill_list to see detailed descriptions."
            )

        self._loaded_skills.add(name)

        # 返回完整 Skill 内容，带加载确认头
        # Agent 会阅读并遵循其中的工作流程
        return (
            f"# Skill Loaded: {name}\n"
            f"# ═══════════════════════════════════════\n"
            f"# The following skill is now ACTIVE.\n"
            f"# Follow these instructions for all subsequent work.\n"
            f"# ═══════════════════════════════════════\n\n"
            f"{content}\n\n"
            f"---\n"
            f"Skill '{name}' loaded. You are now expected to follow the above workflow.\n"
            f"Loaded skills this session: {', '.join(sorted(self._loaded_skills))}"
        )
