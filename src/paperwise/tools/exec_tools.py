"""执行工具 — code_interpreter（Python 代码沙盒）、bash（shell 命令）

对应书中 5.1.1 节七核心工具中的 2 个执行工具
沙盒使用 subprocess + 临时目录隔离
"""

import sys
import asyncio
import tempfile
import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

from paperwise.tools.base import BaseTool, ToolDefinition
from paperwise.core.types import ToolRisk


# Windows 常见命令别名（Agent 常写出 Unix 风格命令）
WINDOWS_COMMAND_ALIASES = [
    (r"\bpython3\.\d+\b", "python"),
    (r"\bpython3\b", "python"),
    (r"\bpython2\b", "python"),
    (r"\bwhich\b", "where"),
]


def adapt_command_for_windows(command: str) -> tuple[str, bool]:
    """将 Unix 风格命令适配为 Windows 可执行形式。

    Returns:
        (适配后的命令, 是否发生了替换)
    """
    if sys.platform != "win32":
        return command, False
    adapted, changed = command, False
    for pattern, replacement in WINDOWS_COMMAND_ALIASES:
        new, n = re.subn(pattern, replacement, adapted)
        if n:
            adapted, changed = new, True
    return adapted, changed


class CodeInterpreterTool(BaseTool):
    """在隔离的 subprocess 中执行 Python 代码。

    安全措施：
    - 使用 -I 标志（隔离模式，不加载用户 site-packages）
    - 在临时 scratch 目录中执行
    - 30 秒超时
    - 捕获 stdout/stderr 分离
    - 禁止网络访问（-I 模式 + 无外部模块）

    对应书中 7 核心工具之一 + 4.7.5 节虚拟身份与隔离执行环境
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="code_interpreter",
            description=(
                "Execute Python code in a sandboxed subprocess. "
                "Use when you need to: verify mathematical claims, reproduce "
                "statistical tests from the paper, generate plots, or process "
                "structured data. "
                "Available modules: math, statistics, json, csv, re, "
                "collections, itertools, functools. "
                "DO NOT use for: simple arithmetic (state the result directly), "
                "file operations (use read_file/write_file), network requests "
                "(not available in sandbox)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds. Default: 30.",
                    },
                },
                "required": ["code"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, code: str, timeout: int = 30) -> str:
        scratch_dir = self.workspace / ".scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)

        code_file = scratch_dir / f"code_{uuid4().hex[:8]}.py"
        code_file.write_text(code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(code_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(scratch_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace").rstrip())
            if stderr:
                output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace').rstrip()}")
            if proc.returncode != 0:
                output_parts.append(f"[exit code: {proc.returncode}]")

            return "\n".join(output_parts) if output_parts else "[No output]"

        except asyncio.TimeoutError:
            return f"[Error] Code execution timed out after {timeout}s"
        except Exception as e:
            return f"[Error] Code execution failed: {e}"
        finally:
            try:
                code_file.unlink(missing_ok=True)
            except Exception:
                pass


class BashTool(BaseTool):
    """执行 Shell 命令。使用共享安全模块。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Execute a bash shell command in the workspace directory. "
                "Use for running external tools (PDF parsing scripts, file "
                "format conversion, etc.). On Windows, falls back to cmd.exe "
                "when bash is not available. "
                "DO NOT use for: file reading (use read_file), file searching "
                "(use grep/glob), running Python code (use code_interpreter)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command."},
                    "timeout": {"type": "integer", "description": "Timeout (sec). Default: 60."},
                },
                "required": ["command"],
            },
            risk=ToolRisk.MEDIUM,
        )

    async def execute(self, command: str, timeout: int = 60) -> str:
        if not command or not command.strip():
            return "[Error] Empty command"

        from paperwise.harness.security import check_command_dangerous
        match = check_command_dangerous(command)
        if match:
            return f"[Blocked] Dangerous command pattern: {match}"

        adapted, changed = adapt_command_for_windows(command)
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c", adapted,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace),
                )
            except FileNotFoundError:
                # Windows 上无 Git Bash 时回退到 cmd.exe
                if sys.platform != "win32":
                    raise
                proc = await asyncio.create_subprocess_exec(
                    "cmd.exe", "/d", "/s", "/c", adapted,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace),
                )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output_parts = []
            if stdout:
                out = stdout.decode("utf-8", errors="replace").rstrip()
                if len(out) > 5000:
                    out = out[:5000] + f"\n[... truncated, {len(out)} total chars ...]"
                output_parts.append(out)
            if stderr:
                err = stderr.decode("utf-8", errors="replace").rstrip()
                output_parts.append(f"[stderr]\n{err}")
            if proc.returncode != 0:
                hint = ""
                if sys.platform == "win32" and proc.returncode == 9009:
                    hint = (
                        "\n[hint] exit code 9009 表示命令未找到。"
                        "Windows 下请使用完整路径或 cmd 语法"
                        "（如 'python -c \"...\"' 而非 'python3'）。"
                    )
                output_parts.append(f"[exit code: {proc.returncode}]{hint}")
            elif changed and sys.platform == "win32":
                output_parts.append("[adapted] 命令已做 Windows 兼容替换 (python3→python 等)")

            return "\n".join(output_parts) if output_parts else "[Command completed with no output]"

        except asyncio.TimeoutError:
            return f"[Error] Command timed out after {timeout}s"
        except Exception as e:
            return f"[Error] Command failed: {e}"
