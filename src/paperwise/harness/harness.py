"""Harness 工程层 — Agent 边界内的运行与治理层"""

import json
from pathlib import Path
from typing import Optional

from paperwise.core.types import Message, Role, ToolCall, ToolResult, AgentState
from paperwise.harness.context import ContextManager
from paperwise.harness.constraints import ConstraintEngine
from paperwise.harness.verification import OutputVerifier
from paperwise.harness.correction import Corrector
from paperwise.harness.status_bar import StatusBar


class Harness:
    """Agent 边界内的运行与治理层。

    在 Agent 循环的关键节点注入钩子（hooks）：
    - pre_llm:  每次 LLM 调用前（上下文构造、状态栏注入、循环检测）
    - post_llm: 每次 LLM 响应后（token 使用追踪）
    - pre_tool: 每次工具执行前（约束检查、参数验证）
    - post_tool: 每次工具执行后（用量追踪）
    """

    def __init__(self, workspace: Path, max_steps: int = 25):
        self.workspace = Path(workspace)
        self.context_manager = ContextManager(workspace)
        self.constraint_engine = ConstraintEngine(workspace)
        self.verifier = OutputVerifier(workspace)
        self.corrector = Corrector()
        self.status_bar = StatusBar()

        self.max_steps = max_steps
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5  # 熔断器阈值

    # 每轮注入后即过期的瞬时状态消息前缀（下一轮前清理，避免上下文膨胀）
    TRANSIENT_PREFIXES = (
        "<agent_status>",
        "<loop_warning>",
        "<budget_note>",
        "<budget_alert>",
    )

    def _strip_transient_messages(self, state: AgentState) -> None:
        """移除上一轮注入的瞬时状态消息（状态栏/循环警告/预算提醒）。

        这些消息只对"当前这一步"有意义，保留历史副本只会占用
        上下文窗口。下一轮会重新注入最新状态，因此删除是安全的。
        """
        state.messages = [
            m for m in state.messages
            if not (
                m.role == Role.USER
                and m.content
                and m.content.startswith(self.TRANSIENT_PREFIXES)
            )
        ]

    # === LLM 钩子 ===

    def pre_llm(self, state: AgentState) -> None:
        """每次 LLM 调用前执行。

        1. 检查 token 预算 → 触发完整 5 层压缩
        2. 清理上一轮的瞬时状态消息
        3. 注入 Agent 状态栏到上下文末尾
        4. 检测循环 → 注入警告
        5. Layer 3 微压缩 → 精简消息格式
        """
        # 清理上一轮的瞬时状态消息
        self._strip_transient_messages(state)

        # Token 预算检查
        if state.tokens_used > state.token_limit * 0.85:
            self.context_manager.full_compress(state)  # Layer 2+4+5
        elif state.tokens_used > state.token_limit * 0.5:
            self.context_manager.compress(state)  # Layer 2+4

        # Layer 3: API 微压缩（每次发送前精简格式）
        state.messages = self.context_manager.micro_compress(state.messages)

        # 注入状态栏（作为 user 角色消息，放在末尾）
        status_text = self.status_bar.generate(state)
        state.messages.append(Message(role=Role.USER, content=status_text))

        # 循环检测
        loop_warning = self.status_bar.detect_loops(state)
        if loop_warning:
            state.messages.append(Message(role=Role.USER, content=loop_warning))

    def post_llm(self, state: AgentState, response) -> None:
        """每次 LLM 响应后执行。更新 token 使用追踪。"""
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            if usage.get("estimated"):
                # 流式响应无精确计数 → 用字符数估算
                content_len = len(response.content or "")
                tool_args_len = sum(
                    len(json.dumps(tc.arguments)) if hasattr(tc, 'arguments') else 0
                    for tc in (response.tool_calls or [])
                )
                state.tokens_used += (content_len + tool_args_len) // 3
            else:
                state.tokens_used += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

    # === 工具钩子 ===

    def pre_tool(self, tool_call: ToolCall, state: AgentState) -> bool:
        """工具执行前的安全检查。

        Returns:
            True 如果允许执行，False 如果被阻止。
        """
        return self.constraint_engine.check(tool_call, state)

    def post_tool(self, tool_call: ToolCall, result: ToolResult, state: AgentState) -> None:
        """工具执行后更新状态。"""
        # 追踪错误
        if result.is_error:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 0

        # 更新工具调用计数（在 state 中）
        name = tool_call.name
        state.tool_call_count[name] = state.tool_call_count.get(name, 0) + 1

    # === 纠正钩子 ===

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """判断是否应该重试。对应书中纠正机制的静默重试策略。"""
        return self.corrector.should_retry(error, attempt)

    def is_circuit_open(self) -> bool:
        """检查熔断器是否触发（连续错误过多）。对应书中 1.2 节熔断器。"""
        return self.consecutive_errors >= self.max_consecutive_errors

    def reset_circuit(self) -> None:
        """重置熔断器。"""
        self.consecutive_errors = 0

    # === 便捷方法 ===

    def check_exit_condition(self, state: AgentState) -> Optional[str]:
        """检查是否满足退出条件。

        Returns:
            None 如果应该继续，否则返回退出原因字符串。
        """
        if state.current_step >= state.max_steps:
            return f"Reached maximum steps ({state.max_steps})"
        if self.is_circuit_open():
            return f"Circuit breaker triggered ({self.consecutive_errors} consecutive errors)"
        if state.tokens_used > state.token_limit:
            return f"Token budget exceeded ({state.tokens_used}/{state.token_limit})"
        return None
