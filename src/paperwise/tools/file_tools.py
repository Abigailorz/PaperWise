"""文件操作工具 — read_file, write_file, edit_file

对应书中 5.1.1 节七核心工具中的 3 个文件工具
"""

from pathlib import Path

from paperwise.tools.base import BaseTool, ToolDefinition, AccessRequested
from paperwise.core.types import ToolRisk


def _format_access_denied(path: str, mode: str) -> str:
    """格式化"需要申请授权"的提示信息。"""
    return (
        f"[Permission Required] 文件 '{path}' 不在当前工作目录内，需要授权才能{mode}。\n\n"
        f"请使用 request_file_access 工具申请访问权限：\n"
        f"  request_file_access(path=\"{path}\", mode=\"{mode}\", "
        f"reason=\"说明为什么需要访问此文件\")\n\n"
        f"注意：\n"
        f"- mode=\"read\"  → 直接读取原文件（只读）\n"
        f"- mode=\"write\" → 文件会被拷贝到工作目录下的 sandbox 中，原文件不会被修改\n"
        f"- 系统文件（/etc/、C:\\\\Windows\\\\、.ssh/ 等）出于安全原因不允许访问"
    )


class ReadFileTool(BaseTool):
    """读取文件内容（带行号、支持指定行范围）。

    描述原则（书中 4.2.4 节）：
    - "何时用"：需要查看文件内容时
    - "DO NOT use for"：搜索文件内容用 grep，查找文件名用 glob
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description=(
                "Read the contents of a file, returning content with line numbers. "
                "Use when you need to examine parsed paper text, previous analysis "
                "outputs, or any file in the workspace. Supports reading specific "
                "line ranges (offset + limit) for large files. "
                "DO NOT use for: searching file contents by pattern (use grep), "
                "finding files by name (use glob), or modifying files (use edit_file/write_file)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to workspace or absolute."
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start reading from this line (1-based). Default: 1."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum lines to read. Default: 2000."
                    },
                },
                "required": ["path"],
            },
            risk=ToolRisk.LOW,
        )

    async def execute(self, path: str, offset: int = 1, limit: int = 2000) -> str:
        try:
            file_path = self._resolve_path(path)
        except AccessRequested:
            return _format_access_denied(path, "read")
        except ValueError as e:
            return f"[Error] {e}"
        if not file_path.exists():
            return f"[Error] File not found: {path}"
        if not file_path.is_file():
            return f"[Error] Not a file: {path}"

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return f"[Error] Cannot read file (binary or unknown encoding): {path}"

        total_lines = len(lines)
        start = max(0, offset - 1)
        end = min(start + limit, total_lines)

        result = []
        for i in range(start, end):
            result.append(f"{i + 1:6d}|{lines[i]}")

        output = "\n".join(result)
        if end < total_lines:
            output += f"\n[... {total_lines - end} more lines, total {total_lines} lines ...]"

        return output


class WriteFileTool(BaseTool):
    """写入文件（创建或覆盖）。

    注意：这是 MEDIUM 风险操作，Harness 层会进行路径安全检查。
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write_file",
            description=(
                "Write content to a file, creating it if it doesn't exist or "
                "overwriting if it does. Use to save analysis results, report "
                "sections, extracted data, or generated code. "
                "DO NOT use for: partial modifications to existing files "
                "(use edit_file instead), or reading files (use read_file)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace or absolute."
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write."
                    },
                },
                "required": ["path", "content"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, path: str, content: str) -> str:
        try:
            file_path = self._resolve_path(path, for_write=True)
        except AccessRequested:
            return _format_access_denied(path, "write")
        except ValueError as e:
            return f"[Error] {e}"

        # 多 Agent 写入冲突保护
        from paperwise.tools.locks import FileLockManager
        owner = getattr(self, "_agent_name", "main")
        lock_id = FileLockManager(self.workspace).acquire(file_path, owner=owner)
        if lock_id is None:
            holder = FileLockManager(self.workspace).owner(file_path)
            return (
                f"[Blocked] 文件 '{path}' 正被 Agent '{holder or '?'}' 写入。\n"
                f"请稍后重试，或使用不同的文件名避免冲突。"
            )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_path.write_text(content, encoding="utf-8")
            size = len(content)
            return f"Successfully wrote {size} characters ({len(content.splitlines())} lines) to {path}"
        finally:
            FileLockManager(self.workspace).release(file_path, lock_id)


class EditFileTool(BaseTool):
    """编辑文件（精确字符串替换）。

    对应书中 5.1 节描述的精确匹配替换机制。
    old_string 必须在文件中唯一匹配一次。
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit_file",
            description=(
                "Edit a file by replacing one exact string with another. "
                "The 'search' string must match exactly once in the file "
                "(including whitespace). Use for small, targeted modifications. "
                "DO NOT use for: large edits (>50 lines, use write_file), "
                "creating new files (use write_file)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to edit."
                    },
                    "search": {
                        "type": "string",
                        "description": "Exact string to find (must match exactly once)."
                    },
                    "replace": {
                        "type": "string",
                        "description": "String to replace with."
                    },
                },
                "required": ["path", "search", "replace"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, path: str, search: str, replace: str) -> str:
        try:
            file_path = self._resolve_path(path, for_write=True)
        except AccessRequested:
            return _format_access_denied(path, "write")
        except ValueError as e:
            return f"[Error] {e}"
        if not file_path.exists():
            return f"[Error] File not found: {path}"

        # 多 Agent 写入冲突保护
        from paperwise.tools.locks import FileLockManager
        owner = getattr(self, "_agent_name", "main")
        lock_mgr = FileLockManager(self.workspace)
        lock_id = lock_mgr.acquire(file_path, owner=owner)
        if lock_id is None:
            holder = lock_mgr.owner(file_path)
            return (
                f"[Blocked] 文件 '{path}' 正被 Agent '{holder or '?'}' 写入。\n"
                f"请稍后重试，或使用不同的文件名避免冲突。"
            )

        try:
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"[Error] Cannot read file (binary or unknown encoding): {path}"

            count = content.count(search)
            if count == 0:
                return f"[Error] Search string not found in {path}"
            if count > 1:
                return (
                    f"[Error] Search string found {count} times in {path}. "
                    f"Please provide a more specific string that matches exactly once. "
                    f"Use read_file to see current content."
                )

            new_content = content.replace(search, replace, 1)
            file_path.write_text(new_content, encoding="utf-8")
            return f"Successfully edited {path} (1 replacement)"
        finally:
            lock_mgr.release(file_path, lock_id)
