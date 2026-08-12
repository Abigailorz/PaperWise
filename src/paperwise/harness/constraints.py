"""约束引擎 — 工具风险评级与安全检查（使用共享安全模块）

对应书中 1.2.6 节护栏和 4.2 节工具设计通用原则
"""

from pathlib import Path
from typing import Optional

from paperwise.core.types import ToolCall, AgentState
from paperwise.harness.security import (
    TOOL_RISK_LEVELS, TOOL_CALL_LIMITS,
    check_command_dangerous, check_path_dangerous,
    check_injection, check_api_key_leak, check_system_prompt_leak,
)


class ConstraintViolation(Exception):
    """约束违规异常"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ConstraintEngine:
    """用代码而非 LLM 执行的安全约束。使用共享安全模块。

    安全分级策略：
    - 写入操作：严格限制在 workspace 内（由 BaseTool._resolve_path 的 for_write 保证）
    - 读取操作：默认限制在 workspace 内，但可通过 allow_read_path() 添加外部白名单路径
    - 危险路径：所有操作统一拦截（由 check_path_dangerous 保证）
    """

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        # 读取白名单：允许 Agent 读取这些路径（及其子目录）下的文件
        self._allowed_read_paths: list[Path] = []

    def allow_read_path(self, path: Path) -> None:
        """添加一个外部路径到读取白名单。

        例如：用户上传的 PDF 所在目录应在此时添加。
        path 可以是文件或目录；目录则递归允许其下所有文件。
        """
        p = Path(path).resolve()
        if p not in self._allowed_read_paths:
            self._allowed_read_paths.append(p)

    def is_read_allowed(self, resolved_path: Path) -> bool:
        """检查给定路径是否允许读取。

        Returns:
            True 如果路径在 workspace 内或在读取白名单中。
        """
        rp = Path(resolved_path).resolve()
        ws = self.workspace
        # 在 workspace 内 → 允许
        try:
            rp.relative_to(ws)
            return True
        except ValueError:
            pass
        # 在白名单路径内 → 允许
        for allowed in self._allowed_read_paths:
            try:
                rp.relative_to(allowed)
                return True
            except ValueError:
                pass
        return False

    def check(self, tool_call: ToolCall, state: AgentState) -> bool:
        """执行前检查。"""
        tool_name = tool_call.name

        risk = TOOL_RISK_LEVELS.get(tool_name)
        if risk is None:
            raise ConstraintViolation(f"Unknown tool: {tool_name}")

        limit = TOOL_CALL_LIMITS.get(tool_name)
        if limit and state.tool_call_count.get(tool_name, 0) >= limit:
            raise ConstraintViolation(
                f"Tool '{tool_name}' limit reached ({limit}).")

        if tool_name in {"read_file", "write_file", "edit_file", "glob", "grep"}:
            path = tool_call.arguments.get("path", "")
            if path:
                match = check_path_dangerous(path)
                if match:
                    raise ConstraintViolation(f"Dangerous path: {match}")

        if tool_name == "bash":
            command = tool_call.arguments.get("command", "")
            if command:
                match = check_command_dangerous(command)
                if match:
                    raise ConstraintViolation(f"Dangerous command: {match}")

        return True

    def input_guard(self, user_input: str) -> bool:
        if len(user_input) > 100_000:
            return False
        if check_injection(user_input):
            return False
        return True

    def output_guard(self, output: str) -> bool:
        if len(output) > 500_000:
            return False
        if check_api_key_leak(output):
            return False
        if check_system_prompt_leak(output):
            return False
        return True
