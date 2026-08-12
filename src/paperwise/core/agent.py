"""Agent 核心引擎 — 完整 ReAct 循环 + 高级特性

对应书中:
- 1.1.5 节: ReAct 循环（思考→行动→观察）
- 1.2 节: Harness 工程（约束+验证+纠正）
- 1.2.5 节: 工作流与自主混合编排
- 2.2 节: Agent API 调用结构
- 2.6 节: Agent 状态栏
- 5.1 节: Coding Agent 整体流程
- 10.2 节: Budget-Aware 执行策略
- Loop 工程: Proposer-Reviewer 防过早终止

增强特性 (vs 基础 ReAct):
1. Budget-Aware 执行: 根据剩余 token/步数动态调整策略
2. Plan-then-Execute: 先写计划，再逐步执行
3. 自验证循环: 每个子任务完成后验证产物
4. 过早终止检测: 连续 text 响应触发 Proposer-Reviewer
5. 流式思考展示: 实时推送 thinking 到 callback
6. 动态工具发现: 支持按需搜索工具
7. 轨迹持久化: 自动保存完整轨迹到磁盘
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Callable, AsyncIterator

from paperwise.core.types import (
    Message, Role, ToolCall, ToolResult,
    AgentState, AgentConfig, AgentResult,
)
from paperwise.core.llm_client import LLMClient, LLMResponse, StreamEvent
from paperwise.harness.harness import Harness
from paperwise.harness.constraints import ConstraintViolation
from paperwise.tools.registry import ToolRegistry


class Agent:
    """增强版 Agent — ReAct 循环 + Budget-Aware + Plan-Execute-Verify

    每次迭代:
    1. pre_llm:  构造上下文 + 注入状态栏 + 检测循环
    2. call_llm: 调用 LLM（支持流式） + 追踪 token
    3. post_llm: 更新状态 + 过早终止检测
    4. 解析响应: tool_calls → 执行工具 / text → 检查是否完成
    5. post_tool: 追踪用量 + 检测错误
    6. check_exit: 7 种退出条件检查
    """

    def __init__(
        self,
        config: AgentConfig,
        tools: ToolRegistry,
        llm_client: LLMClient,
        harness: Harness,
        workspace_dir: Path,
    ):
        self.config = config
        self.tools = tools
        self.llm = llm_client
        self.harness = harness
        self.workspace = Path(workspace_dir)
        self.workspace.mkdir(parents=True, exist_ok=True)

        # 应用 AgentConfig 的工具过滤
        if config.allowed_tools:
            for name in list(tools.list_names()):
                if name not in config.allowed_tools:
                    tools.unregister(name)

        # 应用 AgentConfig 的 Skills
        if config.skills:
            self._active_skills = config.skills
        else:
            self._active_skills = []

        self.state = AgentState(
            max_steps=config.max_steps,
            token_limit=config.token_budget,
            workspace_dir=self.workspace,
        )
        self.callbacks: list[Callable] = []

        # 高级特性 — 从配置读取
        from paperwise.config.settings import get_settings
        settings = get_settings()
        self._plan: list[str] = []
        self._consecutive_text_responses = 0
        self._early_term_threshold = settings.early_term_threshold
        self._time_budget = settings.time_budget_seconds
        self._start_time = time.time()

    # === 公共接口 ===

    def on_event(self, callback: Callable) -> None:
        """注册事件回调（UI 实时更新）。"""
        self.callbacks.append(callback)

    async def run(self, task: str) -> AgentResult:
        """执行 ReAct 循环直到任务完成。"""
        self.state.task_description = task
        self._init_messages(task)

        try:
            while self.state.current_step < self.config.max_steps:
                # === 7 种退出条件检查 ===
                if reason := self._check_exit():
                    return self._make_result(
                        f"[Agent stopped: {reason}]", success=False,
                        error=reason
                    )

                # === Pre-LLM: 上下文构造 + 状态栏 ===
                self.harness.pre_llm(self.state)
                self._emit("step", f"Step {self.state.current_step + 1}/{self.config.max_steps}")

                # === Budget-Aware 策略调整 ===
                budget_note = self._budget_note()
                if budget_note:
                    self.state.messages.append(Message(role=Role.USER, content=budget_note))

                # === Call LLM（流式） ===
                response = await self._call_llm_with_retry()

                # === Post-LLM: 更新状态 ===
                self.harness.post_llm(self.state, response)
                self._emit("tokens", f"~{self.state.tokens_used:,}/{self.state.token_limit:,} tokens")

                # === 解析响应 ===
                if response.tool_calls:
                    # ---- 工具调用分支 ----
                    self._consecutive_text_responses = 0
                    self.state.messages.append(Message(
                        role=Role.ASSISTANT,
                        content=response.content or None,
                        tool_calls=response.tool_calls,
                        reasoning=response.reasoning,
                    ))

                    for tc in response.tool_calls:
                        result = await self._execute_tool(tc)
                        self.state.messages.append(Message(
                            role=Role.TOOL, content=result.output,
                            tool_call_id=tc.id,
                        ))

                elif response.content:
                    # ---- 文本响应分支 ----
                    self._consecutive_text_responses += 1
                    self.state.messages.append(Message(
                        role=Role.ASSISTANT, content=response.content,
                        reasoning=response.reasoning,
                    ))

                    # 过早终止检测
                    if self._consecutive_text_responses >= self._early_term_threshold:
                        self._emit("verify", "Checking if task is truly complete...")
                        if not await self._verify_completion():
                            self._consecutive_text_responses = 0
                            self.state.messages.append(Message(
                                role=Role.USER,
                                content=(
                                    "<verification_result>Task is NOT complete. "
                                    "Please continue working. Check:\n"
                                    "1. Are all promised files actually created?\n"
                                    "2. Is every section fully written?\n"
                                    "3. Are all claims cited with evidence?\n"
                                    "Continue from where you left off.</verification_result>"
                                )
                            ))
                            continue
                        # 真的完成了
                        return self._make_result(response.content, success=True)

                    # 单次 text 响应，但可能已完成
                    if self._consecutive_text_responses == 1:
                        # 检查是否有明确完成标志
                        if self._looks_complete(response.content):
                            return self._make_result(response.content, success=True)
                else:
                    self._emit("warn", "Empty response from LLM")

                self.state.current_step += 1

            # 达到最大步数
            return self._make_result(
                f"[Max steps ({self.config.max_steps}) reached. "
                f"Tools: {dict(self.state.tool_call_count)}]",
                success=False, error="max_steps"
            )

        except Exception as e:
            import traceback
            self._emit("error", f"{type(e).__name__}: {e}")
            return self._make_result(
                f"[Agent error: {e}]", success=False, error=str(e)
            )

    # === 退出条件（7 种） ===

    def _check_exit(self) -> Optional[str]:
        """检查 7 种退出条件。"""
        s = self.state

        # 1. 达到最大步数
        if s.current_step >= s.max_steps:
            return f"max_steps ({s.max_steps})"

        # 2. Token 预算耗尽
        if s.tokens_used > s.token_limit:
            return f"token_budget ({s.tokens_used}/{s.token_limit})"

        # 3. 熔断器触发
        if self.harness.is_circuit_open():
            return f"circuit_breaker ({self.harness.consecutive_errors} errors)"

        # 4. 时间预算
        elapsed = time.time() - self._start_time
        if elapsed > self._time_budget:
            return f"time_budget ({elapsed:.0f}s)"

        # 5. 连续工具错误
        if self.harness.consecutive_errors >= 5:
            return f"consecutive_errors ({self.harness.consecutive_errors})"

        return None

    # === Budget-Aware 策略 ===

    def _budget_note(self) -> Optional[str]:
        """根据剩余预算自动注入策略提示。

        对应书中 10.2 节: Budget-Aware Tool-Use
        前期广泛探索 → 后期聚焦最有希望的方向
        """
        s = self.state
        steps_ratio = s.current_step / max(s.max_steps, 1)
        tokens_ratio = s.tokens_used / max(s.token_limit, 1)
        usage = max(steps_ratio, tokens_ratio)

        if usage > 0.8:
            return (
                "<budget_alert>Budget nearly exhausted "
                f"({s.current_step}/{s.max_steps} steps, {s.tokens_used}/{s.token_limit} tokens). "
                "Stop exploring. Synthesize what you have. "
                "Save all findings and write the final report NOW. "
                "Do NOT start new searches or deep analyses.</budget_alert>"
            )
        elif usage > 0.5:
            return (
                "<budget_note>Half of budget used. "
                "Focus on the most important remaining sections. "
                "Skip non-essential details.</budget_note>"
            )
        return None

    # === 过早终止检测（Loop 工程核心） ===

    async def _verify_completion(self) -> bool:
        """验证任务是否真正完成。

        Proposer-Reviewer 模式:
        - Proposer (主 Agent): 声称任务完成
        - Reviewer (验证逻辑): 检查产物是否真实存在

        对应书中 Loop 工程: "由验证判定何时可以停"
        """
        # 检查关键产物是否存在
        checks = []

        # 报告文件存在性
        report_path = self.workspace / "report" / "report.md"
        checks.append(("Report file", report_path.exists()))

        # 报告文件大小（至少 500 字符才有意义）
        if report_path.exists():
            size = len(report_path.read_text(encoding="utf-8"))
            checks.append((f"Report size ({size} chars)", size > 500))

        # 分析文件存在性
        analysis_dir = self.workspace / "analysis"
        checks.append(("Analysis directory", analysis_dir.exists()))

        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        self._emit("verify", f"Completion check: {passed}/{total} passed")

        return passed >= total * 0.6  # 60% 通过即算完成

    def _looks_complete(self, text: str) -> bool:
        """检测文本是否看起来是最终回答（中英文）。"""
        complete_markers = [
            # English
            "report has been generated", "report is complete",
            "final answer", "task complete", "all sections",
            "analysis complete",
            # 中文
            "报告已生成", "任务完成", "分析完成",
            "报告已完成", "所有章节", "以上就是",
            "总结如下", "以上是完整的", "已经完成",
            # 通用完成信号
            "以上是我的分析", "如有需要可以继续",
        ]
        text_lower = text.lower()
        return any(m.lower() in text_lower for m in complete_markers)

    # === LLM 调用 ===

    async def _call_llm_with_retry(self, attempt: int = 1) -> LLMResponse:
        """调用 LLM，带自动重试和缓冲流式输出。

        流式策略（防洪水）：
        - 内部收集完整文本（用于构建最终响应）
        - 对外只推送缓冲块（每 0.5s 或遇到自然断点）
        - 工具调用只推送 start/end，不推送中间 args
        """
        try:
            messages = self._to_api_format()
            tools = self.tools.get_definitions()

            # 内部完整收集
            text_parts: list[str] = []
            emit_buffer = ""       # 推送缓冲区
            last_flush = time.time()
            tool_calls_data: dict[str, dict] = {}  # id → {name, args_str}

            async for event in self.llm.chat_stream(
                messages=messages, tools=tools,
                temperature=self.config.temperature,
            ):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                    emit_buffer += event.text

                    # 推送条件：间隔 > 0.5s 或遇到自然断点
                    natural_break = any(
                        event.text.rstrip().endswith(p)
                        for p in ("\n", "。", ".", "!", "?", "：", ":", "）")
                    )
                    if time.time() - last_flush >= 0.5 or natural_break:
                        chunk = emit_buffer.strip()
                        if chunk and len(chunk) > 3:  # 至少 3 个字符才推送
                            self._emit("thinking", chunk)
                        emit_buffer = ""
                        last_flush = time.time()

                elif event.type == "tool_call_start":
                    if emit_buffer.strip():
                        self._emit("thinking", emit_buffer.strip())
                        emit_buffer = ""
                    tool_calls_data[event.tool_id] = {
                        "name": event.tool_name, "args_str": ""
                    }
                    self._emit("tool_start", event.tool_name)

                elif event.type == "tool_call_delta":
                    if event.tool_id in tool_calls_data:
                        tool_calls_data[event.tool_id]["args_str"] += (event.tool_arguments or "")

                elif event.type == "tool_call_end":
                    self._emit("tool_end", f"{event.tool_name} 完成")

                elif event.type == "done":
                    if emit_buffer.strip():
                        self._emit("thinking", emit_buffer.strip())
                    break

            # 组装完整响应
            full_content = "".join(text_parts).strip()

            tool_calls = []
            for tc_id, tc_data in tool_calls_data.items():
                try:
                    args = json.loads(tc_data["args_str"]) if tc_data["args_str"].strip() else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc_id, name=tc_data["name"], arguments=args))

            # Token 估算：API 返回的精确数据在 post_llm 中已计入
            # 此处的 count_tokens 作为后备（当 API usage 不可用时）

            return LLMResponse(
                content=full_content, tool_calls=tool_calls,
                reasoning="",
                stop_reason="tool_calls" if tool_calls else "stop",
                usage={"estimated": True},
            )

        except Exception as e:
            if self.harness.should_retry(e, attempt):
                delay = min(2 ** attempt, 30)
                self._emit("retry", f"API 错误，{delay}s 后重试 (第{attempt}次)...")
                await asyncio.sleep(delay)
                return await self._call_llm_with_retry(attempt + 1)
            raise

    # === 工具执行 ===

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """执行工具调用（含完整 Harness 检查 + 参数验证）。"""
        try:
            # Pre-tool 安全检查
            self.harness.pre_tool(tool_call, self.state)

            tool = self.tools.get(tool_call.name)
            # 参数验证（书中 4.2 节）
            tool.validate_args(**tool_call.arguments)
            output = await tool.execute(**tool_call.arguments)

            # 大输出截断（存磁盘，模型看摘要）
            output, truncated, full_path = (
                self.harness.context_manager.truncate_tool_output(output)
            )

            result = ToolResult(
                tool_call_id=tool_call.id, name=tool_call.name,
                output=output, is_error=output.startswith("[Error]"),
                truncated=truncated, full_output_path=full_path,
            )

            self.harness.post_tool(tool_call, result, self.state)
            self.harness.corrector.record_success()
            return result

        except ConstraintViolation as e:
            self.harness.corrector.record_error()
            return ToolResult(
                tool_call_id=tool_call.id, name=tool_call.name,
                output=f"[Blocked] {e.message}", is_error=True,
            )
        except Exception as e:
            self.harness.corrector.record_error()
            return ToolResult(
                tool_call_id=tool_call.id, name=tool_call.name,
                output=f"[Error] {type(e).__name__}: {e}", is_error=True,
            )

    # === 消息构造 ===

    def _init_messages(self, task: str) -> None:
        """构造初始消息 — KV Cache 友好布局，复用 Harness 的 ContextManager。"""
        cm = self.harness.context_manager  # 复用，保持 LLM 连接

        # Plan-then-Execute: 要求 Agent 先写出计划
        enhanced_task = (
            f"<task>\n{task}\n</task>\n\n"
            f"<instructions>\n"
            f"1. FIRST, write a brief plan of action to analysis/plan.md\n"
            f"2. THEN execute the plan step by step\n"
            f"3. AFTER each step, verify the output exists before moving on\n"
            f"4. When ALL steps are complete, produce the final report\n"
            f"5. DO NOT claim completion until all output files exist\n"
            f"</instructions>\n"
        )

        self.state.messages = cm.build_initial_context(
            system_prompt=self.config.system_prompt,
            task=enhanced_task,
            workspace=self.workspace,
        )

    def _to_api_format(self) -> list[dict]:
        """内部 Message → OpenAI API 格式。"""
        api_msgs = []
        for msg in self.state.messages:
            if msg.role == Role.SYSTEM:
                api_msgs.append({"role": "system", "content": msg.content or ""})
            elif msg.role == Role.USER:
                api_msgs.append({"role": "user", "content": msg.content or ""})
            elif msg.role == Role.ASSISTANT:
                entry = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                        for tc in msg.tool_calls
                    ]
                elif msg.content is None:
                    entry["content"] = None
                api_msgs.append(entry)
            elif msg.role == Role.TOOL:
                api_msgs.append({
                    "role": "tool", "tool_call_id": msg.tool_call_id,
                    "content": msg.content or "",
                })
        return api_msgs

    # === 内部辅助 ===

    def _make_result(self, output: str, success: bool, error: str = "") -> AgentResult:
        """构造 AgentResult + 自动保存轨迹。"""
        result = AgentResult(
            final_output=output, messages=self.state.messages,
            steps=self.state.current_step,
            tool_stats=dict(self.state.tool_call_count),
            success=success, error_message=error,
        )
        self._save_trajectory()
        return result

    def _save_trajectory(self) -> None:
        """持久化完整轨迹到磁盘（用于评估和持续进化）。"""
        traj_dir = self.workspace / ".trajectories"
        traj_dir.mkdir(exist_ok=True)
        import time as _time
        traj_file = traj_dir / f"traj_{int(_time.time())}_{self.state.current_step}steps.json"

        data = {
            "agent": self.config.name,
            "task": self.state.task_description,
            "steps": self.state.current_step,
            "tool_stats": dict(self.state.tool_call_count),
            "messages": [
                {"role": m.role.value, "content": str(m.content)[:500],
                 "tool_calls": [tc.name for tc in m.tool_calls] if m.tool_calls else None}
                for m in self.state.messages[-50:]  # 最近 50 条
            ],
        }
        traj_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit(self, event_type: str, detail: str) -> None:
        """发送事件给所有注册的回调。"""
        for cb in self.callbacks:
            try:
                cb(event_type, detail)
            except Exception:
                pass
