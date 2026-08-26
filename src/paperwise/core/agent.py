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
from paperwise.core.agent_loop import AgentLoopMixin
from paperwise.harness.harness import Harness
from paperwise.harness.constraints import ConstraintViolation
from paperwise.tools.registry import ToolRegistry


class Agent(AgentLoopMixin):
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
        """Execute the task, routing through orchestration when enabled."""
        if not getattr(self.config, "enable_orchestration", True):
            return await self._legacy_run(task)
        # Lazy import avoids circular dependency with orchestration.orchestrator
        from paperwise.orchestration import SmartOrchestrator
        orchestrator = SmartOrchestrator(
            llm_client=self.llm,
            workspace=self.workspace,
            base_config=self.config,
        )
        result = await orchestrator.run(task, paper_dir=self.workspace)
        # Copy sub-agent metrics into the outer agent state so downstream eval code
        # can read from agent.state as before.
        self.state.messages = result.messages or self.state.messages
        self.state.current_step = result.steps
        self.state.tool_call_count = result.tool_stats or self.state.tool_call_count
        self.state.tokens_used = result.tokens_used
        return result

    async def _legacy_run(self, task: str) -> AgentResult:
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
                if not getattr(self.config, "enable_budget_note", True):
                    budget_note = None
                if budget_note:
                    budget_msg = Message(role=Role.USER, content=budget_note)
                    self.state.messages.append(budget_msg)
                    self._memory.add_turn(budget_msg)

                # === Optional hierarchical memory compression before LLM ===
                if getattr(self.config, "enable_hierarchical_memory", True):
                    await self._maybe_compress_memory()

                # === Call LLM (streaming) ===
                response = await self._call_llm_with_retry()

                # === Post-LLM: update state ===
                self.harness.post_llm(self.state, response)
                # Track estimated cost using the active model pricing
                if response.usage:
                    self.state.cost_used += self.llm.estimate_cost(response.usage)
                self._emit("tokens", f"~{self.state.tokens_used:,}/{self.state.token_limit:,} tokens  cost=${self.state.cost_used:.3f}")

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



    # === Budget-Aware guidance ===


    # === Early termination verification ===



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


    # === Message construction ===

    def _init_messages(self, task: str) -> None:
        """Build initial context using explicit plan and hierarchical memory."""
        self._memory = HierarchicalMemory(self.workspace, llm_client=self.llm)

        if getattr(self.config, "enable_plan", True) and not self._plan.tasks:
            self._plan = Plan.from_task_text(task)
            self.state.todo_items = self._plan.to_todo_items()
            plan_text = self._plan.to_status_text()
            enhanced_task = (
                f"<task>\n{task}\n</task>\n\n"
                f"<instructions>\n"
                f"1. Follow the explicit plan in <current_plan>; mark tasks done as you finish them\n"
                f"2. If the plan includes 'read_paper', you MUST call read_file or grep on text.md FIRST\n"
                f"   before giving any final answer. A final answer without reading the paper is a hallucination.\n"
                f"3. Execute the plan step by step\n"
                f"4. AFTER each step, verify the output exists before moving on\n"
                f"5. When ALL plan tasks are done, produce the final report\n"
                f"6. DO NOT claim completion until all output files exist\n"
                f"</instructions>\n"
            )
        else:
            self._plan = Plan()
            self.state.todo_items = []
            plan_text = ""
            enhanced_task = (
                f"<task>\n{task}\n</task>\n\n"
                f"<instructions>\n"
                f"1. Reason step by step to complete the task\n"
                f"2. Read the paper (text.md) with read_file or grep before answering any factual question\n"
                f"3. Use available tools when needed\n"
                f"4. Verify any output files exist before claiming completion\n"
                f"</instructions>\n"
            )

        citation_rule = (
            "\n<citation_rules>\n"
            "Every factual claim MUST cite the source using [source: text.md Lxxx-Lyyy]. "
            "If a fact cannot be found in the paper, write [source: not reported in paper]. "
            "Claims without a valid source will be treated as hallucinations.\n"
            "</citation_rules>\n"
        )
        self.state.messages = self._memory.build_initial_context(
            system_prompt=self.config.system_prompt + citation_rule,
            task="".join(enhanced_task),
            workspace=self.workspace,
            plan_text=plan_text,
        )

    async def _maybe_compress_memory(self) -> None:
        """Compress context before an LLM call if needed."""
        try:
            compressed = await self._memory.amaybe_compress(
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
            tokens_used=self.state.tokens_used,
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
