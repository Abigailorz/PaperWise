"""Agent 状态栏 — 运行时状态的结构化注入

对应书中 2.6 节：把分散的隐式状态提炼为显式知识

三条核心原则：
1. 用代码维护，不用 LLM 去统计
2. 不删除原始上下文（有损投影的风险）
3. 把准确性当一线生产指标
"""

from datetime import datetime
from typing import Optional

from paperwise.core.types import AgentState


class StatusBar:
    """生成 Agent 状态栏（XML 格式的状态摘要）。

    注入位置：上下文末尾（user 角色消息），获得最高注意力权重。

    包含信息：
    - 任务进度 (TODO 列表)
    - 工具使用统计
    - 资源消耗 (tokens, 时间)
    - 环境信息 (时间, 工作目录)
    - 告警信息
    """

    LOOP_DETECTION_WINDOW = 5  # 检测最近 N 次工具调用

    def generate(self, state: AgentState) -> str:
        """生成当前状态栏 XML。TODO 从 Agent plan 自动填充。"""
        parts = ["<agent_status>"]

        # 1. 任务进度 — 自动从 Agent plan 和工具调用中推断
        self._auto_populate_todos(state)
        if state.todo_items:
            parts.append("  <progress>")
            for item in state.todo_items[-10:]:
                status_icon = {"done": "✓", "in_progress": "→", "pending": "○"}
                icon = status_icon.get(item.get("status", "pending"), "?")
                parts.append(f"    {icon} {item['text']}")
            parts.append("  </progress>")

        # 2. 工具使用统计
        if state.tool_call_count:
            parts.append("  <tool_usage>")
            for name, count in sorted(state.tool_call_count.items()):
                parts.append(f"    {name}: {count} calls")
            parts.append("  </tool_usage>")

        # 3. 资源消耗
        from datetime import datetime
        elapsed = (datetime.now() - state.start_time).total_seconds()
        parts.append("  <resources>")
        parts.append(f"    Step: {state.current_step}/{state.max_steps}")
        parts.append(f"    Tokens: ~{state.tokens_used:,}/{state.token_limit:,}")
        parts.append(f"    Time: {elapsed:.0f}s")
        if state.workspace_dir:
            parts.append(f"    Working dir: {state.workspace_dir}")
        parts.append("  </resources>")

        parts.append("</agent_status>")
        return "\n".join(parts)

    def _auto_populate_todos(self, state: AgentState):
        """从 Agent 的消息历史中自动推断 TODO 项。

        启发式规则：
        - 写文件操作且路径以 report/ 开头 → 报告相关 TODO
        - 读论文文本 → 论文理解 TODO
        - 调用 code_interpreter → 数据验证 TODO
        """
        if state.todo_items:
            return  # 已有手动填充的 TODO，不覆盖

        inferred = []
        seen = set()

        for msg in state.messages[-30:]:
            # 从 assistant 消息中的 tool_calls 推断
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.name == "read_file" and "text.md" in str(tc.arguments.get("path", "")):
                        key = "read_paper"
                        if key not in seen:
                            inferred.append({"text": "Read and understand paper", "status": "done", "key": key})
                            seen.add(key)
                    elif tc.name == "grep":
                        key = "search_content"
                        if key not in seen:
                            inferred.append({"text": "Search paper for key information", "status": "done", "key": key})
                            seen.add(key)
                    elif tc.name == "write_file":
                        path = str(tc.arguments.get("path", ""))
                        if "report" in path:
                            key = "generate_report"
                            if key not in seen:
                                inferred.append({"text": "Generate analysis report", "status": "in_progress", "key": key})
                                seen.add(key)
                    elif tc.name == "code_interpreter":
                        key = "verify_data"
                        if key not in seen:
                            inferred.append({"text": "Verify numerical claims with code", "status": "done", "key": key})
                            seen.add(key)

        if inferred:
            state.todo_items = inferred

    def detect_loops(self, state: AgentState) -> Optional[str]:
        """检测 Agent 是否陷入重复操作循环。

        发现连续 N 次相同工具 + 相同参数时注入警告。
        """
        if len(state.messages) < self.LOOP_DETECTION_WINDOW * 2:
            return None

        # 提取最近的工具调用
        recent_tools = []
        for msg in state.messages[-20:]:
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    recent_tools.append((tc.name, str(tc.arguments)))

        if len(recent_tools) < self.LOOP_DETECTION_WINDOW:
            return None

        # 检查最近 N 次是否完全相同
        last_n = recent_tools[-self.LOOP_DETECTION_WINDOW:]
        if len(set(last_n)) == 1 and len(last_n) >= self.LOOP_DETECTION_WINDOW:
            name, args = last_n[0]
            return (
                f"<loop_warning>\n"
                f"  WARNING: You have called '{name}' with the same arguments "
                f"{len(last_n)} times in a row. You may be stuck in a loop.\n"
                f"  Consider:\n"
                f"  1. Are you getting useful new information each time?\n"
                f"  2. Should you try a different approach?\n"
                f"  3. Is it time to report what you've found so far?\n"
                f"</loop_warning>"
            )

        return None
