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
from paperwise.config.settings import get_settings


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

      cost_used = getattr(self, "_cost_used", getattr(self.state, "cost_used", 0.0))
      cost_limit = getattr(self, "_cost_limit", getattr(self.state, "cost_limit", getattr(get_settings(), "cost_budget_usd", 5.0)))
      if cost_used > cost_limit:
          return f"cost_budget (${cost_used:.3f}/${cost_limit:.2f})"

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
      tool_calls = []
      for m in messages:
          if m.role == Role.ASSISTANT and m.tool_calls:
              tool_calls.extend([
                  (tc.name, json.dumps(tc.arguments, sort_keys=True))
                  for tc in m.tool_calls
              ])
      if len(tool_calls) >= window + 1 and len(set(tool_calls[-(window + 1):])) == 1:
          name, _ = tool_calls[-1]
          return f"repeated {name} calls"
      return None

  # ══════════ Budget-aware guidance ══════════

  def _budget_note(self) -> Optional[str]:
      """Graduated budget guidance based on remaining steps/tokens and plan."""
      cfg = getattr(self, "config", None)
      if cfg is not None and not getattr(cfg, "enable_budget_note", True):
          return None
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
      """Check whether the agent has actually finished the task.

      Combines explicit plan progress, required output existence,
      and completion markers. No keyword-only guessing.
      """
      plan = getattr(self, "_plan", None)
      if plan and plan.tasks:
          # If the plan requires reading the paper, that must be done first.
          read_task = plan.get("read_paper")
          if read_task and read_task.status != TaskStatus.DONE:
              return False

          # All planned tasks must be marked done when an output artifact is required.
          if plan.get("generate_report") or plan.get("generate_pptx"):
              if not plan.done:
                  return False

          # Required outputs for finished tasks must exist.
          if plan.get("generate_report"):
              if not (self.workspace / "report" / "report.md").exists():
                  return False
          if plan.get("generate_pptx"):
              if not list(self.workspace.glob("*.pptx")) and not list((self.workspace / "output").glob("*.pptx")):
                  return False

          # For simple read+answer tasks, allow completion once the paper has been read
          # and the response contains a substantive answer.
          if not plan.get("generate_report") and not plan.get("generate_pptx"):
              text_lower = text.lower().strip()
              has_substance = len(text_lower) > 40
              has_marker = any(
                  m in text_lower
                  for m in (
                      "final answer", "task complete", "analysis complete",
                      "in summary", "to summarize", "overall", "conclusion",
                  )
              )
              return has_substance and has_marker

      # Fall back to text markers for tasks without an explicit plan.
      markers = [
          "report has been generated", "report is complete",
          "final answer", "task complete", "all sections",
          "analysis complete", "ppt has been generated", "slides are ready",
      ]
      lowered = text.lower()
      return any(m in lowered for m in markers)

  async def _verify_completion(self) -> bool:
      """Verify that outputs exist, are non-trivial, cite the paper, and pass judge review.

      Output checks are conditional on the plan: only tasks explicitly planned
      (generate_report, generate_pptx) require file outputs. Simple Q&A tasks
      pass once the plan is complete.
      """
      plan = getattr(self, "_plan", None)

      # 1. Plan must be complete.
      if plan and plan.tasks and not plan.done:
          done, total = plan.progress
          self._emit("verify", f"Plan incomplete ({done}/{total})")
          return False

      from paperwise.harness.verification import OutputVerifier
      verifier = OutputVerifier(self.workspace)
      checks = []

      needs_report = bool(plan and plan.get("generate_report"))
      needs_pptx = bool(plan and plan.get("generate_pptx"))

      if needs_report:
          report_path = self.workspace / "report" / "report.md"
          if report_path.exists():
              report_text = report_path.read_text(encoding="utf-8")
              checks.append(("report exists", True))
              checks.append(("report size > 500", len(report_text) > 500))
              cit = verifier.verify_citations(report_text, "paper/text.md")
              checks.append(("citations valid", cit.passed))
          else:
              checks.append(("report exists", False))
              checks.append(("report size > 500", False))
              checks.append(("citations valid", False))

      if needs_pptx:
          ppts = list(self.workspace.glob("*.pptx"))
          out_dir = self.workspace / "output"
          if out_dir.exists():
              ppts.extend(out_dir.glob("*.pptx"))
          checks.append(("pptx exists", bool(ppts)))

      if not checks:
          # No output artifacts required; plan completion is sufficient.
          self._emit("verify", "Completion check: no output required, plan done")
          return True

      passed = sum(1 for _, ok in checks if ok)
      total = len(checks)
      self._emit("verify", f"Completion check: {passed}/{total} passed")

      if total and passed < total * 0.8:
          return False

      # Only run the expensive judge review when there is an actual report to review.
      if needs_report and (self.workspace / "report" / "report.md").exists():
          return await self._judge_review()
      return True


  async def _judge_review(self) -> bool:
      """Run a cheap judge review if a judge model is configured."""
      cfg = getattr(self, "config", None)
      if cfg is not None and not getattr(cfg, "enable_judge_review", True):
          return True
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
      path = str(args.get("path", "")).replace("\\", "/").lower()

      if name == "read_file" and "text.md" in path and not result.is_error:
          plan.mark_done("read_paper", evidence=path)
      elif name == "write_file":
          if path.endswith("report.md"):
              plan.mark_done("generate_report", evidence=path)
          if path.endswith(("methodology.md", "experiments.md",
                            "method_experiments_analysis.md")):
              plan.mark_done("analyze_method", evidence=path)
          if path.endswith(("limitations.md", "critical_limitations_analysis.md")):
              plan.mark_done("critical_analysis", evidence=path)
          if "analysis/plan.md" in path:
              plan.mark_in_progress("analyze_method")
      elif name == "code_interpreter" and not result.is_error:
          plan.mark_done("verify_data", evidence="code executed")
      elif name == "generate_pptx" and not result.is_error:
          plan.mark_done("generate_pptx", evidence=path)

      # Keep todo_items in sync with the plan
      if hasattr(self.state, "todo_items"):
          self.state.todo_items = plan.to_todo_items()
