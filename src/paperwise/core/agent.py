"""Agent core engine -- improved ReAct loop with explicit plans and hierarchical memory."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional, Callable

from paperwise.core.types import (
    Message, Role, ToolCall, ToolResult,
    AgentState, AgentConfig, AgentResult,
)
from paperwise.core.llm_client import LLMClient, LLMResponse, StreamEvent
from paperwise.core.plan import Plan, TaskStatus
from paperwise.core.hierarchical_memory import HierarchicalMemory
from paperwise.harness.harness import Harness
from paperwise.harness.constraints import ConstraintViolation
from paperwise.tools.registry import ToolRegistry


class Agent:
    """Enhanced Agent -- ReAct loop + Budget-Aware + explicit Plan-Execute-Verify."""

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

        if config.allowed_tools:
            for name in list(tools.list_names()):
                if name not in config.allowed_tools:
                    tools.unregister(name)

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

        self._plan = Plan()
        self._memory = HierarchicalMemory(self.workspace, llm_client=self.llm)

        from paperwise.config.settings import get_settings
        settings = get_settings()
        self._consecutive_text_responses = 0
        self._early_term_threshold = settings.early_term_threshold
        self._time_budget = settings.time_budget_seconds
        self._start_time = time.time()

    def on_event(self, callback: Callable) -> None:
        """Register an event callback for UI updates."""
        self.callbacks.append(callback)

    async def run(self, task: str) -> AgentResult:
        """Execute the ReAct loop until the task is completed or a limit is hit."""
        self.state.task_description = task
        self._init_messages(task)

        try:
            while self.state.current_step < self.config.max_steps:
                # === Exit condition checks ===
                if reason := self._check_exit():
                    return self._make_result(
                        f"[Agent stopped: {reason}]", success=False,
                        error=reason
                    )

                # === Pre-LLM: context shaping + status bar ===
                self.harness.pre_llm(self.state)
                self._emit("step", f"Step {self.state.current_step + 1}/{self.config.max_steps}")

                # === Budget-Aware guidance ===
                budget_note = self._budget_note()
                if budget_note:
                    budget_msg = Message(role=Role.USER, content=budget_note)
                    self.state.messages.append(budget_msg)
                    self._memory.add_turn(budget_msg)

                # === Optional hierarchical memory compression before LLM ===
                self._maybe_compress_memory()

                # === Call LLM (streaming) ===
                response = await self._call_llm_with_retry()

                # === Post-LLM: update state ===
                self.harness.post_llm(self.state, response)
                self._emit("tokens", f"~{self.state.tokens_used:,}/{self.state.token_limit:,} tokens")

                # === Parse response ===
                if response.tool_calls:
                    # ---- Tool call branch ----
                    self._consecutive_text_responses = 0
                    assistant_msg = Message(
                        role=Role.ASSISTANT,
                        content=response.content or None,
                        tool_calls=response.tool_calls,
                        reasoning=response.reasoning,
                    )
                    self.state.messages.append(assistant_msg)
                    self._memory.add_turn(assistant_msg)

                    for tc in response.tool_calls:
                        result = await self._execute_tool(tc)
                        tool_msg = Message(
                            role=Role.TOOL, content=result.output,
                            tool_call_id=tc.id,
                        )
                        self.state.messages.append(tool_msg)
                        self._memory.add_turn(tool_msg)

                elif response.content:
                    # ---- Text response branch ----
                    self._consecutive_text_responses += 1
                    text_msg = Message(
                        role=Role.ASSISTANT, content=response.content,
                        reasoning=response.reasoning,
                    )
                    self.state.messages.append(text_msg)
                    self._memory.add_turn(text_msg)

                    # Early termination check
                    if self._consecutive_text_responses >= self._early_term_threshold:
                        self._emit("verify", "Checking if task is truly complete...")
                        if not await self._verify_completion():
                            self._consecutive_text_responses = 0
                            retry_msg = Message(
                                role=Role.USER,
                                content=(
                                    "<verification_result>Task is NOT complete. "
                                    "Please continue working. Check:\n"
                                    "1. Are all promised files actually created?\n"
                                    "2. Is every section fully written?\n"
                                    "3. Are all claims cited with evidence?\n"
                                    "Continue from where you left off.</verification_result>"
                                )
                            )
                            self.state.messages.append(retry_msg)
                            self._memory.add_turn(retry_msg)
                            continue
                        return self._make_result(response.content, success=True)

                    # Single text response that may already be complete
                    if self._consecutive_text_responses == 1:
                        if self._looks_complete(response.content):
                            return self._make_result(response.content, success=True)
                else:
                    self._emit("warn", "Empty response from LLM")

                self.state.current_step += 1

            # Max steps reached
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

    # === Exit conditions ===

    def _check_exit(self) -> Optional[str]:
        """Check hard limits, stagnation, and plan completion."""
        s = self.state

        if s.current_step >= s.max_steps:
            return f"max_steps ({s.max_steps})"

        if s.tokens_used > s.token_limit:
            return f"token_budget ({s.tokens_used}/{s.token_limit})"

        if self.harness.is_circuit_open():
            return f"circuit_breaker ({self.harness.consecutive_errors} errors)"

        elapsed = time.time() - self._start_time
        if elapsed > self._time_budget:
            return f"time_budget ({elapsed:.0f}s)"

        if self.harness.consecutive_errors >= 5:
            return f"consecutive_errors ({self.harness.consecutive_errors})"

        stagnation = self._detect_stagnation(window=4)
        if stagnation:
            return f"stagnation: {stagnation}"

        if self._plan.tasks and self._plan.done and len(self._plan.tasks) > 1 and s.current_step > 0:
            return "plan_completed"

        return None

    def _detect_stagnation(self, window: int = 4) -> Optional[str]:
        """Detect repeated identical tool calls over a window."""
        msgs = self.state.messages
        if len(msgs) < window * 2:
            return None
        recent = msgs[-window * 2:]
        tool_calls = []
        for m in recent:
            if m.role == Role.ASSISTANT and m.tool_calls:
                tool_calls.extend([
                    (tc.name, json.dumps(tc.arguments, sort_keys=True))
                    for tc in m.tool_calls
                ])
        if len(tool_calls) >= window and len(set(tool_calls[-window:])) == 1:
            name, _ = tool_calls[-1]
            return f"repeated {name} calls"
        return None

    # === Budget-Aware guidance ===

    def _budget_note(self) -> Optional[str]:
        """Graduated budget guidance based on remaining steps/tokens and plan."""
        s = self.state
        steps_ratio = s.current_step / max(s.max_steps, 1)
        tokens_ratio = s.tokens_used / max(s.token_limit, 1)
        usage = max(steps_ratio, tokens_ratio)

        plan_hint = ""
        if self._plan.tasks and not self._plan.done:
            done, total = self._plan.progress
            plan_hint = f" Plan progress: {done}/{total} tasks done."

        if usage > 0.9:
            return (
                "<budget_alert>CRITICAL: Budget almost exhausted "
                f"({s.current_step}/{s.max_steps} steps, {s.tokens_used}/{s.token_limit} tokens).{plan_hint} "
                "Stop exploring. Synthesize what you have. Write the final report NOW. "
                "Do NOT start new searches or deep analyses.</budget_alert>"
            )
        elif usage > 0.7:
            return (
                "<budget_note>High budget usage. "
                f"{plan_hint} Focus on the most important remaining sections. "
                "Skip non-essential details.</budget_note>"
            )
        elif usage > 0.5:
            return (
                "<budget_note>Half of budget used. "
                f"{plan_hint} Prioritize tasks on the critical path.</budget_note>"
            )
        elif usage > 0.25:
            return (
                "<budget_note>Budget being consumed steadily. "
                f"{plan_hint} Avoid redundant tool calls.</budget_note>"
            )
        return None

    # === Early termination verification ===

    async def _verify_completion(self) -> bool:
        """Verify outputs exist and optionally run a judge review."""
        checks = []

        report_path = self.workspace / "report" / "report.md"
        checks.append(("Report file", report_path.exists()))

        if report_path.exists():
            size = len(report_path.read_text(encoding="utf-8"))
            checks.append((f"Report size ({size} chars)", size > 500))

        analysis_dir = self.workspace / "analysis"
        checks.append(("Analysis directory", analysis_dir.exists()))

        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        self._emit("verify", f"Completion check: {passed}/{total} passed")

        if passed < total * 0.6:
            return False

        judge_ok = await self._judge_review()
        if not judge_ok:
            self._emit("verify", "Judge review failed; continuing to improve")
            return False

        return True

    async def _judge_review(self) -> bool:
        """Run a cheap judge review if a judge model is configured."""
        from paperwise.config.settings import get_settings
        settings = get_settings()
        judge_model = getattr(settings, "judge_model", None)
        judge_provider = getattr(settings, "judge_provider", None)
        judge_key = getattr(settings, "judge_api_key", None)
        if not judge_model or not judge_provider or not judge_key:
            return True
        try:
            from paperwise.core.llm_client import LLMClient
            judge = LLMClient(provider=judge_provider, model=judge_model, api_key=judge_key)
            report_path = self.workspace / "report" / "report.md"
            report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
            task = self.state.task_description or self.config.name
            prompt = (
                f"Review whether the following report satisfies the task: {task}\n\n"
                f"Report (first 2000 chars):\n{report[:2000]}\n\n"
                "Reply with a single JSON object: {\"passed\": true/false, \"feedback\": \"...\"}"
            )
            resp = await judge.chat(
                [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=300
            )
            content = (resp.content or "").strip()
            match = re.search(r"\{.*?\}", content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                if not data.get("passed", True):
                    self.state.messages.append(Message(
                        role=Role.USER,
                        content=f"<judge_feedback>{data.get('feedback', 'Improve the report.')}</judge_feedback>"
                    ))
                return bool(data.get("passed", True))
        except Exception as e:
            self._emit("warn", f"Judge review skipped: {e}")
        return True

    def _looks_complete(self, text: str) -> bool:
        """Check whether the text looks like a final answer."""
        complete_markers = [
            "report has been generated", "report is complete",
            "final answer", "task complete", "all sections",
            "analysis complete",
        ]
        text_lower = text.lower()
        return any(m.lower() in text_lower for m in complete_markers)
    # === LLM call ===

    async def _call_llm_with_retry(self, attempt: int = 1) -> LLMResponse:
        """Call LLM with automatic retry and streaming output."""
        try:
            messages = self._to_api_format()
            tools = self.tools.get_definitions()

            text_parts: list[str] = []
            emit_buffer = ""
            last_flush = time.time()
            tool_calls_data: dict[str, dict] = {}

            async for event in self.llm.chat_stream(
                messages=messages, tools=tools,
                temperature=self.config.temperature,
            ):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                    emit_buffer += event.text

                    natural_break = any(
                        event.text.rstrip().endswith(p)
                        for p in ("\n", ".", "!", "?", ":", ")")
                    )
                    if time.time() - last_flush >= 0.5 or natural_break:
                        chunk = emit_buffer.strip()
                        if chunk and len(chunk) > 3:
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
                    self._emit("tool_end", f"{event.tool_name} done")

                elif event.type == "done":
                    if emit_buffer.strip():
                        self._emit("thinking", emit_buffer.strip())
                    break

            full_content = "".join(text_parts).strip()

            tool_calls = []
            for tc_id, tc_data in tool_calls_data.items():
                try:
                    args = json.loads(tc_data["args_str"]) if tc_data["args_str"].strip() else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc_id, name=tc_data["name"], arguments=args))

            return LLMResponse(
                content=full_content, tool_calls=tool_calls,
                reasoning="",
                stop_reason="tool_calls" if tool_calls else "stop",
                usage={"estimated": True},
            )

        except Exception as e:
            if self.harness.should_retry(e, attempt):
                delay = min(2 ** attempt, 30)
                self._emit("retry", f"API error, retrying in {delay}s (attempt {attempt})...")
                await asyncio.sleep(delay)
                return await self._call_llm_with_retry(attempt + 1)
            raise

    # === Tool execution ===

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and update the explicit plan."""
        try:
            self.harness.pre_tool(tool_call, self.state)

            tool = self.tools.get(tool_call.name)
            tool.validate_args(**tool_call.arguments)
            output = await tool.execute(**tool_call.arguments)

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
            self._update_plan_from_tool_call(tool_call, result)
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

    def _update_plan_from_tool_call(self, tool_call: ToolCall, result: ToolResult) -> None:
        """Mark plan tasks done based on observed tool execution."""
        plan = self._plan
        if not plan.tasks:
            return

        name = tool_call.name
        args = tool_call.arguments
        path = str(args.get("path", "")).lower()

        if name == "read_file" and "text.md" in path and not result.is_error:
            plan.mark_done("read_paper", evidence=path)
        elif name == "write_file":
            if "report" in path:
                plan.mark_done("generate_report", evidence=path)
            if "analysis/plan.md" in path:
                plan.mark_in_progress("analyze_method")
        elif name == "code_interpreter" and not result.is_error:
            plan.mark_done("verify_data", evidence="code executed")
        elif name == "generate_pptx" and not result.is_error:
            plan.mark_done("generate_pptx", evidence=path)

        self.state.todo_items = plan.to_todo_items()

    # === Message construction ===

    def _init_messages(self, task: str) -> None:
        """Build initial context using explicit plan and hierarchical memory."""
        self._plan = Plan.from_task_text(task)
        self.state.todo_items = self._plan.to_todo_items()
        self._memory = HierarchicalMemory(self.workspace, llm_client=self.llm)

        plan_text = self._plan.to_status_text()
        enhanced_task = (
            f"<task>\n{task}\n</task>\n\n"
            f"<instructions>\n"
            f"1. Follow the explicit plan in <current_plan>; mark tasks done as you finish them\n"
            f"2. Execute the plan step by step\n"
            f"3. AFTER each step, verify the output exists before moving on\n"
            f"4. When ALL plan tasks are done, produce the final report\n"
            f"5. DO NOT claim completion until all output files exist\n"
            f"</instructions>\n"
        )

        self.state.messages = self._memory.build_initial_context(
            system_prompt=self.config.system_prompt,
            task="".join(enhanced_task),
            workspace=self.workspace,
            plan_text=plan_text,
        )

    def _maybe_compress_memory(self) -> None:
        """Compress context before an LLM call if needed."""
        try:
            compressed = self._memory.maybe_compress(
                self.state.token_limit, self.state.tokens_used
            )
            if compressed:
                system_msgs = [m for m in self.state.messages if m.role == Role.SYSTEM]
                recent_non_system = [m for m in self.state.messages if m.role != Role.SYSTEM]
                if system_msgs:
                    self.state.messages = self._memory.to_messages(system_msgs[0]) + recent_non_system
                else:
                    self.state.messages = self._memory.to_messages(None) + recent_non_system
                self._emit("status", "Context compressed using hierarchical memory")
        except Exception:
            pass
    def _to_api_format(self) -> list[dict]:
        """Convert internal messages to OpenAI API format."""
        api_msgs = []
        for msg in self.state.messages:
            if msg.role == Role.SYSTEM:
                api_msgs.append({"role": "system", "content": msg.content or ""})
            elif msg.role == Role.USER:
                api_msgs.append({"role": "user", "content": msg.content or ""})
            elif msg.role == Role.ASSISTANT:
                entry = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    entry["content"] = None
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

    # === Internal helpers ===

    def _make_result(self, output: str, success: bool, error: str = "") -> AgentResult:
        """Build AgentResult and persist trajectory."""
        result = AgentResult(
            final_output=output, messages=self.state.messages,
            steps=self.state.current_step,
            tool_stats=dict(self.state.tool_call_count),
            success=success, error_message=error,
        )
        self._save_trajectory()
        return result

    def _save_trajectory(self) -> None:
        """Persist trajectory to disk for evaluation and continuous improvement."""
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
                for m in self.state.messages[-50:]
            ],
        }
        traj_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit(self, event_type: str, detail: str) -> None:
        """Send event to all registered callbacks."""
        for cb in self.callbacks:
            try:
                cb(event_type, detail)
            except Exception:
                pass
