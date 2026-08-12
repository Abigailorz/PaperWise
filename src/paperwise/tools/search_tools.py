"""搜索工具 — glob（文件名搜索）、grep（内容搜索）

对应书中 5.1.1 节七核心工具中的 2 个搜索工具
"""

import re
from pathlib import Path
from typing import Optional

from paperwise.tools.base import BaseTool, ToolDefinition, AccessRequested
from paperwise.core.types import ToolRisk


def _format_access_denied(path: str) -> str:
    """格式化搜索工具"需要申请授权"的提示。"""
    return (
        f"[Permission Required] 路径 '{path}' 不在当前工作目录内，需要授权才能搜索。\n\n"
        f"请使用 request_file_access 工具申请访问权限：\n"
        f"  request_file_access(path=\"{path}\", mode=\"read\", "
        f"reason=\"说明为什么需要搜索此目录\")\n\n"
        f"获得授权后，请重试你的搜索操作。"
    )


class GlobTool(BaseTool):
    """Glob 模式匹配 — 按文件名查找文件"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="glob",
            description=(
                "Find files and directories matching a glob pattern. "
                "Use to explore workspace structure, find specific file "
                "types (e.g., '*.md', 'figures/*.png'), or locate parsed "
                "paper components. "
                "DO NOT use for: searching file contents (use grep), "
                "reading file content (use read_file after finding files)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern. Examples: '*.md', 'figures/*.png', "
                            "'**/*.json'. Supports *, **, ?, [chars]."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (relative to workspace). Default: workspace root."
                    },
                },
                "required": ["pattern"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, pattern: str, path: Optional[str] = None) -> str:
        try:
            base = self._resolve_path(path) if path else self.workspace
        except AccessRequested:
            return _format_access_denied(path)
        except ValueError as e:
            return f"[Error] {e}"

        if not base.exists():
            return f"[Error] Directory not found: {base}"

        matches = sorted(base.rglob(pattern)) if "**" in pattern else sorted(base.glob(pattern))

        if not matches:
            return f"No files matching '{pattern}' found in {path or 'workspace'}"

        result = []
        for m in matches[:500]:
            try:
                rel = m.relative_to(self.workspace)
            except ValueError:
                rel = m
            suffix = "/" if m.is_dir() else f" ({m.stat().st_size} bytes)" if m.is_file() else ""
            result.append(f"  {rel}{suffix}")

        output = f"Found {len(matches)} matches for '{pattern}':\n" + "\n".join(result)
        if len(matches) > 500:
            output += f"\n[... {len(matches) - 500} more results truncated ...]"
        return output


class GrepTool(BaseTool):
    """内容搜索 — 使用正则表达式搜索文件内容"""

    TEXT_EXTENSIONS = {".md", ".txt", ".json", ".tex", ".py", ".csv", ".yaml", ".yml", ".xml", ".html"}

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="grep",
            description=(
                "Search file contents using a regex pattern. Returns matching "
                "lines with file paths and line numbers. Use to find specific "
                "terms, formula references (e.g., 'Eq\\.\\\\s*\\\\(12\\\\)'), or "
                "data points in parsed papers. Supports full regex syntax and "
                "context lines. "
                "DO NOT use for: listing file names (use glob), reading full "
                "file content (use read_file after finding matches)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern. Examples: 'attention mechanism', 'p\\\\s*<\\\\s*0\\\\.0[15]', 'Table\\\\s+\\\\d+'."
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search. Default: workspace root."
                    },
                    "glob": {
                        "type": "string",
                        "description": "Filter files by glob pattern (e.g., '*.md')."
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context before/after each match. Default: 2.",
                        "default": 2,
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Case-sensitive search. Default: false.",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        context_lines: int = 2,
        case_sensitive: bool = False,
    ) -> str:
        try:
            base = self._resolve_path(path) if path else self.workspace
        except AccessRequested:
            return _format_access_denied(path)
        except ValueError as e:
            return f"[Error] {e}"

        if not base.exists():
            return f"[Error] Path not found: {base}"

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"[Error] Invalid regex pattern: {e}"

        results = []
        match_count = 0

        # 确定搜索的文件
        if base.is_file():
            files = [base]
        elif glob:
            files = sorted(base.rglob(glob))
        else:
            files = sorted(base.rglob("*"))

        for file_path in files:
            if not file_path.is_file():
                continue
            if file_path.suffix not in self.TEXT_EXTENSIONS:
                continue

            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, PermissionError):
                continue

            for i, line in enumerate(lines):
                if regex.search(line):
                    match_count += 1
                    try:
                        rel = file_path.relative_to(self.workspace)
                    except ValueError:
                        rel = file_path

                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)

                    context_block = []
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        context_block.append(f"  {rel}:{j + 1}:{prefix} {lines[j]}")
                    results.append("\n".join(context_block))
                    results.append("  ---")

                    if len(results) >= 100:
                        break

            if len(results) >= 100:
                break

        if not results:
            return f"No matches for '{pattern}'"

        output = f"Found {match_count} matches for '{pattern}':\n\n" + "\n".join(results)
        if len(results) >= 100:
            output += f"\n[... results truncated, {match_count} total matches ...]"
        return output
