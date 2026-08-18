"""Shared agent control logic mixin.

Extracts common logic between Agent (one-shot) and AgentSession (chat):
- exit conditions
- budget-aware prompting
- stagnation detection
- plan tracking
- judge review
"""

import json
import re
import time
from typing import Optional

from paperwise.core.types import Message, Role, ToolCall, ToolResult
from paperwise.core.plan import Plan, TaskStatus


class AgentLoopMixin:
    """Mixin containing the shared ReAct control logic.

    Both Agent and AgentSession must provide:
    - self.state (with messages)
    - self._plan (Plan)
    - self.harness (Harness)
    - self.workspace (Path)
    - self.llm (LLM client)
    - self._emit(event_type, detail)
    """

    # ══════════ Exit conditions ══════════

    def _check_exit(self) -> Optional[str]:
        """Check hard limits, stagnation, and plan completion."""
        steps = getattr(self, "_total_steps", None)
        max_steps = getattr(self, "_max_steps_per_turn", None)
        hard_cap = getattr(self, "_hard_cap", None)
        if steps is None:
            steps = getattr(self.state, "current_step", 0)
        if max_steps is None:
            max_steps = getattr(self.state, "max_steps", 25)

        tokens_used = getattr(self, "_tokens_used", getattr(self.state, "tokens_used", 0))
        token_limit = getattr(self, "_token_limit", getattr(self.state, "token_limit", 180_000))

        limit = hard_cap if hard_cap is not None else max_steps
        if steps >= limit:
            return f"step_limit ({limit})"

        if tokens_used > token_limit:
            return f"token_budget ({tokens_used}/{token_limit})"

        if self.harness.is_circuit_open():
            return f"circuit_breaker ({self.harness.consecutive_errors} errors)"

        start_time = getattr(self, "_start_time", None)
        time_budget = getattr(self, "_time_budget", None)
        if start_time is not None and time_budget is not None:
            elapsed = time.time() - start_time
            if elapsed > time_budget:
                return f"time_budget ({elapsed:.0f}s)"

        if self.harness.consecutive_errors >= 5:
            return f"consecutive_errors ({self.harness.consecutive_errors})"

        stagnation = self._detect_stagnation(window=4)
        if stagnation:
            return f"stagnation: {stagnation}"

        plan = getattr(self, "_plan", None)
        if plan and plan.tasks and plan.done and len(plan.tasks) > 1 and steps > 0:
            return "plan_completed"

        return None

    def _detect_stagnation(self, window: int = 4) -> Optional[str]:
        """Detect repeated identical tool calls over a window."""
        messages = self.state.messages
        if len(messages) < window * 2:
            return None
        recent = messages[-window * 2:]
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

    # ══════════ Budget-aware guidance ══════════

    def _budget_note(self) -> Optional[str]:
        """Graduated budget guidance based on remaining steps/tokens and plan."""
        steps = getattr(self, "_total_steps", getattr(self.state, "current_step", 0))
        max_steps = getattr(self, "_max_steps_per_turn", getattr(self.state, "max_steps", 25))
        tokens_used = getattr(self, "_tokens_used", getattr(self.state, "tokens_used", 0))
        token_limit = getattr(self, "_token_limit", getattr(self.state, "token_limit", 180_000))

        steps_ratio = steps / max(max_steps, 1)
        tokens_ratio = tokens_used / max(token_limit, 1)
        usage = max(steps_ratio, tokens_ratio)

        plan_hint = ""
        plan = getattr(self, "_plan", None)
        if plan and plan.tasks and not plan.done:
            done, total = plan.progress
            plan_hint = f" Plan progress: {done}/{total} tasks done."

        if usage > 0.9:
            return (
                "<budget_alert>CRITICAL: Budget almost exhausted "
                f"({steps}/{max_steps} steps, {tokens_used}/{token_limit} tokens).{plan_hint} "
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

    # ══════════ Completion / judge ══════════

    def _looks_complete(self, text: str) -> bool:
        """Check whether the text looks like a final answer."""
        complete_markers = [
            "report has been generated", "report is complete",
            "final answer", "task complete", "all sections",
            "analysis complete", "ppt has been generated", "slides are ready",
            # Chinese markers (session responds in Chinese)
            "报告已生成", "报告已完成", "任务完成", "所有部分",
            "分析完成", "ppt已生成", "幻灯片已就绪",
            "最终回答", "总结",
        ]
        text_lower = text.lower()
        return any(m.lower() in text_lower for m in complete_markers)

    async def _verify_completion(self) -> bool:
        """Verify outputs exist and optionally run a judge review."""
        checks = []
        report_path = self.workspace / "report" / "report.md"
        plan = getattr(self, "_plan", None)

        if plan and plan.tasks:
            # Plan-aware checks: the plan drives what outputs are expected.
            if not plan.done:
                done, total = plan.progress
                self._emit("verify", f"Plan incomplete ({done}/{total})")
                return False

            if plan.get("generate_report"):
                checks.append(("report.md exists", report_path.exists()))
                if report_path.exists():
                    size = len(report_path.read_text(encoding="utf-8"))
                    checks.append(("report size > 500", size > 500))

            if plan.get("generate_pptx"):
                output_dir = self.workspace / "output"
                ppts = list(self.workspace.glob("*.pptx"))
                if output_dir.exists():
                    ppts.extend(output_dir.glob("*.pptx"))
                checks.append(("pptx file", len(ppts) > 0))

            if plan.get("verify_data"):
                checks.append(("verify_data done", plan.get("verify_data").status == TaskStatus.DONE))
        else:
            # Fallback: no explicit plan task, check canonical outputs.
            checks.append(("report.md exists", report_path.exists()))
            if report_path.exists():
                size = len(report_path.read_text(encoding="utf-8"))
                checks.append(("report size > 500", size > 500))
            analysis_dir = self.workspace / "analysis"
            checks.append(("analysis directory", analysis_dir.exists()))

        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        self._emit("verify", f"Completion check: {passed}/{total} passed")

        if total and passed < total * 0.6:
            return False

        return await self._judge_review()

    async def _judge_review(self) -> bool:
        """Run a cheap judge review if a judge model is configured."""
        from paperwise.config.settings import get_settings
        settings = get_settings()
        if not settings.judge_api_key_resolved:
            return True

        try:
            judge = settings.build_judge_llm()
            report_path = self.workspace / "report" / "report.md"
            report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
            task = (getattr(self, "_current_task_description", None)
                    or getattr(self.state, "task_description", "")
                    or "complete the task")
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
                    fb = data.get("feedback", "Improve the report.")
                    fb_msg = Message(
                        role=Role.USER,
                        content=f"<judge_feedback>{fb}</judge_feedback>",
                    )
                    self.state.messages.append(fb_msg)
                    hm = getattr(self, "_hierarchical_memory", None) or getattr(self, "_memory", None)
                    if hm and hasattr(hm, "add_turn"):
                        hm.add_turn(fb_msg)
                return bool(data.get("passed", True))
        except Exception as e:
            self._emit("warn", f"Judge review skipped: {e}")
        return True

    # ══════════ Plan tracking ══════════

    def _update_plan_from_tool_call(self, tool_call: ToolCall, result: ToolResult) -> None:
        """Mark plan tasks done based on observed tool execution."""
        plan = getattr(self, "_plan", None)
        if not plan or not plan.tasks:
            return

        name = tool_call.name
        args = tool_call.arguments
        path = str(args.get("path", "")).lower()

        if name == "read_file" and "text.md" in path and not result.is_error:
            plan.mark_done("read_paper", evidence=path)
        elif name == "write_file":
            if path.endswith("report.md"):
                plan.mark_done("generate_report", evidence=path)
            if "analysis/plan.md" in path:
                plan.mark_in_progress("analyze_method")
        elif name == "code_interpreter" and not result.is_error:
            plan.mark_done("verify_data", evidence="code executed")
        elif name == "generate_pptx" and not result.is_error:
            plan.mark_done("generate_pptx", evidence=path)

        # Keep todo_items in sync with the plan
        if hasattr(self.state, "todo_items"):
            self.state.todo_items = plan.to_todo_items()
