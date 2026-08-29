"""Smart orchestrator: route simple tasks to a single agent and complex tasks through a DAG of specialist agents.

When ``enable_orchestration`` is false, the legacy single-agent path is used.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from paperwise.core.agent import Agent
from paperwise.core.types import AgentConfig, AgentResult, TraceEventType
from paperwise.core.plan import Plan, Task
from paperwise.core.trace_collector import TraceCollector, create_trace_collector
from paperwise.orchestration.classifier import TaskClassifier
from paperwise.orchestration.paper_dag import PaperDAGPlanner
from paperwise.orchestration.specs import SubAgentSpec, PaperAnalysisPipeline, parse_findings
from paperwise.orchestration.types import GraphState, NodeSpec
from paperwise.orchestration.dag_executor import DAGExecutor, build_plan_from_workflow, ExecutionConfig, DAGExecutorError
from paperwise.orchestration.dynamic_planner import DynamicDAGPlanner, PlanCompositionPolicy
from paperwise.orchestration.memory_adapter import OrchestratorMemoryAdapter
from paperwise.memory.research_state import ResearchState, ResearchStateManager, KnowledgeGap
from paperwise.memory.proactive_engine import ProactiveEngine, Recommendation
from paperwise.orchestration.artifact_manager import ArtifactManager
from paperwise.orchestration.replanner import ReplanAgent
from paperwise.tools.registry import ToolRegistry
from paperwise.harness.harness import Harness


class SmartOrchestrator:
  """Entry point for complexity-aware task execution.

  - Simple tasks run as a single Agent with a minimal plan.
  - Complex tasks run through a DAG: Reader -> (Verifier) -> Writer -> Reviewer -> (Revision).
  """

  def __init__(
      self,
      llm_client,
      workspace: Path,
      base_config: Optional[AgentConfig] = None,
      classifier: Optional[TaskClassifier] = None,
      max_review_rounds: int = 3,
      trace_collector: Optional[TraceCollector] = None,
      use_dynamic_plan: bool = False,
  ):
      self.llm = llm_client
      self.workspace = Path(workspace)
      self.workspace.mkdir(parents=True, exist_ok=True)
      self.base_config = base_config or AgentConfig()
      self.classifier = classifier or TaskClassifier(llm_client, workspace)
      self.replanner = ReplanAgent()
      self.research_state_manager = ResearchStateManager(self.workspace, user_id="default")
      self.memory_adapter = OrchestratorMemoryAdapter(
          workspace=self.workspace,
          user_id="default",
          research_state_manager=self.research_state_manager,
      )
      self.dynamic_planner = DynamicDAGPlanner()
      self.plan_policy = PlanCompositionPolicy(use_dynamic_plan=use_dynamic_plan)
      self.proactive_engine = ProactiveEngine(self.workspace, user_id="default")
      self.max_review_rounds = max_review_rounds
      self.trace_collector = trace_collector or create_trace_collector(enabled=False)
      self._current_context_xml: str = ""

  async def run(self, task: str, paper_dir: Optional[Path] = None) -> AgentResult:
      """Run a task through the appropriate execution path.

      Implements a two-stage fallback: tasks classified as ambiguous simple
      (``escalate_on_failure=True``) first try the fast single-agent path and
      fall back to the full DAG if that fails.
      """
      trace = self.trace_collector.start_trace(
          task=task,
          metadata={"source": "orchestrator", "workspace": str(self.workspace)},
      )
      route = await self.classifier.classify(task)
      self.trace_collector.add_event(
          TraceEventType.ROUTER_DECISION,
          data={"route": self._route_to_dict(route)},
      )
      paper_dir = Path(paper_dir) if paper_dir else self.workspace

      try:
          if route.is_simple and not route.escalate_on_failure:
              result = await self._run_simple(task, paper_dir)
          elif route.is_simple and route.escalate_on_failure:
              simple_result = await self._run_simple(task, paper_dir)
              if simple_result.success and not simple_result.error_message:
                  result = simple_result
              else:
                  result = await self._run_complex(task, paper_dir)
          else:
              result = await self._run_complex(task, paper_dir)

          result.trace_id = trace.trace_id
          self.trace_collector.end_trace(result)
          return result
      except Exception as e:
          self.trace_collector.add_event(
              TraceEventType.ERROR,
              data={"exception": type(e).__name__, "message": str(e)},
          )
          result = AgentResult(
              final_output=f"[Orchestrator error: {e}]",
              success=False, error_message=str(e),
          )
          result.trace_id = trace.trace_id
          self.trace_collector.end_trace(result)
          return result

  @staticmethod
  def _route_to_dict(route) -> dict:
      """将 TaskRoute 转为可序列化字典。"""
      return {
          "task_type": getattr(route, "task_type", "").value if hasattr(getattr(route, "task_type", ""), "value") else str(getattr(route, "task_type", "")),
          "complexity": getattr(route, "complexity", "").value if hasattr(getattr(route, "complexity", ""), "value") else str(getattr(route, "complexity", "")),
          "workflow": getattr(route, "workflow", ""),
          "confidence": getattr(route, "confidence", ""),
          "reason": getattr(route, "reason", ""),
          "escalate_on_failure": getattr(route, "escalate_on_failure", False),
      }

  async def _run_simple(self, task: str, paper_dir: Path) -> AgentResult:
      """Lightweight single-agent execution for simple Q&A."""
      config = AgentConfig(
          name="simple-agent",
          system_prompt=self.base_config.system_prompt or "You are a rigorous academic-paper analysis agent.",
          model=self.base_config.model,
          max_steps=min(self.base_config.max_steps, 10),
          token_budget=self.base_config.token_budget,
          temperature=self.base_config.temperature,
          enable_plan=True,
          enable_budget_note=False,
          enable_judge_review=False,
          enable_hierarchical_memory=False,
          enable_orchestration=False,
      )

      tools = ToolRegistry.create_default(paper_dir)
      harness = Harness(paper_dir, max_steps=config.max_steps)
      agent = Agent(
          config=config,
          tools=tools,
          llm_client=self.llm,
          harness=harness,
          workspace_dir=paper_dir,
          trace_collector=self.trace_collector,
      )

      # Inject a minimal plan: read paper, then answer
      agent._plan = Plan()
      agent._plan.add("Read the paper to locate relevant information", task_id="read_paper")
      agent._plan.add("Answer the user's question with evidence", depends_on=["read_paper"], task_id="answer")
      agent.state.todo_items = agent._plan.to_todo_items()

      agent_result = await agent.run(task)
      # Propagate sub-agent metrics to the orchestrator result
      agent_result.steps = max(agent.state.current_step, 1)
      agent_result.tool_stats = dict(agent.state.tool_call_count)
      agent_result.tokens_used = agent.state.tokens_used
      return agent_result

  async def _run_complex(self, task: str, paper_dir: Path) -> AgentResult:
      """Execute a complex task through the dynamic DAG executor.

      The DAG includes reading, optional verification, analysis, report/PPT
      generation, adversarial review and revision. Review/revision is repeated
      up to max_review_rounds until critical/major issues are gone.
      """
      if not (paper_dir / "text.md").exists():
          if (self.workspace / "paper" / "text.md").exists():
              paper_dir = self.workspace / "paper"
      if not (paper_dir / "text.md").exists():
          return AgentResult(
              final_output=f"[No paper found] text.md missing in {paper_dir}",
              success=False,
              error_message="missing_text_md",
          )

      research_state = self.research_state_manager.new(current_task=task)
      research_state.current_paper = str(paper_dir)
      research_state.dag_status = "running"
      self.research_state_manager.save(research_state)

      context_package = self.memory_adapter.assemble_context(research_state)
      self._current_context_xml = context_package.to_xml()
      self.trace_collector.add_event(
          TraceEventType.CONTEXT_ASSEMBLED,
          data={"context_size": context_package.size()},
      )

      route = await self.classifier.classify(task)
      plan = self._select_plan(task, route, research_state)
      plan = self.memory_adapter.apply_gaps_to_plan(plan, research_state)
      plan = self.memory_adapter.apply_strategies_to_plan(
          plan, task_type=research_state.intent or "analysis"
      )
      self.trace_collector.add_event(
          TraceEventType.PLAN_GENERATED,
          data={"plan": plan.to_dict(), "tasks": [t.to_dict() for t in plan.tasks], "dynamic": self.plan_policy.use_dynamic_plan},
      )
      state = GraphState(
          task=task,
          budget={
              "token_limit": self.base_config.token_budget,
              "step_limit": self.base_config.max_steps,
          },
      )
      state.set_artifact("paper_dir", paper_dir)
      state.set_artifact("task_text", task)
      state.set_artifact("context_xml", context_package.to_xml())
      state.set_artifact("research_state_id", research_state.state_id)

      executor = DAGExecutor(
          config=ExecutionConfig(
              enable_replan=True,
              replan_callback=self._replan,
              trace_collector=self.trace_collector,
          ),
      )
      executor.register_condition("requires_pptx", self._condition_requires_pptx)
      executor.register_condition("requires_verification", self._condition_requires_verification)
      executor.register_handler("read_paper", self._handle_read_paper)
      executor.register_handler("verify_data", self._handle_verify_data)
      executor.register_handler("analyze_method", self._handle_analyze_method)
      executor.register_handler("generate_report", self._handle_generate_report)
      executor.register_handler("generate_pptx", self._handle_generate_pptx)
      executor.register_handler("review_report", self._handle_review_report)
      executor.register_handler("revise_report", self._handle_revise_report)
      # Corrective nodes produced by ReplanAgent
      executor.register_handler("re_read_section", self._handle_read_paper)
      executor.register_handler("re_verify_with_code", self._handle_verify_data)
      executor.register_handler("revision", self._handle_revise_report)
      executor.register_handler("expand_evidence", self._handle_analyze_method)
      executor.register_handler("dynamic_research", self._handle_analyze_method)

      total_steps = 0
      final_findings = {"verdict": "UNKNOWN", "critical": 0, "major": 0, "minor": 0}

      try:
          exec_result = await executor.run(plan, state, self._emit_progress)
          research_state = self.memory_adapter.update_state_from_execution(
              research_state,
              completed_nodes=list(exec_result.get("completed_nodes", [])),
              failed_nodes=list(exec_result.get("failed_nodes", [])),
              gaps=[
                  KnowledgeGap(node_id=n, description=f"Node {n} failed or was replanned")
                  for n in exec_result.get("failed_nodes", [])
              ],
          )
      except DAGExecutorError as budget_err:
          # Graceful degradation: return whatever artifacts were produced so far.
          return AgentResult(
              final_output=(
                  f"[Budget or execution limit reached: {budget_err}]\n\n"
                  + self._assemble_final_output(paper_dir)
              ),
              success=False,
              error_message=str(budget_err),
              steps=total_steps + state.budget.get("steps_used", 0),
              tool_stats={},
          )
      total_steps += exec_result.get("steps", 0)

      for rnd in range(1, self.max_review_rounds + 1):
          findings = self._latest_findings(paper_dir)
          final_findings = findings
          if findings["critical"] == 0 and findings["major"] == 0:
              break
          if rnd >= self.max_review_rounds:
              break
          loop_plan = Plan()
          loop_plan.add(
              "Adversarially review the output",
              task_id="review_report",
              depends_on=[],
          )
          loop_plan.add(
              "Revise the output based on review findings",
              task_id="revise_report",
              depends_on=["review_report"],
          )
          loop_state = GraphState(
              task=task,
              budget={
                  "token_limit": self.base_config.token_budget,
                  "step_limit": self.base_config.max_steps,
              },
          )
          loop_state.artifacts = state.artifacts.copy()
          loop_state.set_artifact("paper_dir", paper_dir)
          loop_state.set_artifact("task_text", task)
          loop_exec = await executor.run(loop_plan, loop_state, self._emit_progress)
          total_steps += loop_exec.get("steps", 0)
          state.artifacts.update(loop_state.artifacts)

      final_text = self._assemble_final_output(paper_dir)
      success = final_findings["critical"] == 0 and final_findings["major"] == 0
      if not success:
          final_text = (
              f"[Warning: review left {final_findings['critical']} critical and "
              f"{final_findings['major']} major issues after {self.max_review_rounds} rounds]\n\n"
              + final_text
          )
      recommendations = await self.proactive_engine.decide(research_state)
      research_state.next_steps = [r.title for r in recommendations]
      self.research_state_manager.save(research_state)
      self._write_status(paper_dir, "recommendations", [r.to_dict() for r in recommendations])

      result = AgentResult(
          final_output=final_text,
          success=success,
          steps=total_steps,
          tool_stats={},
      )

      # Memory learning: record episode and procedure
      current_trace = self.trace_collector.current_trace()
      self.memory_adapter.record_episode(research_state, current_trace, result)
      self.memory_adapter.learn_procedure(
          task_type=research_state.intent or "analysis",
          plan=plan,
          success=success,
      )
      # P3: Reviewer findings -> learning signals -> strategy library
      self.memory_adapter.learn_from_review(
          task_type=research_state.intent or "analysis",
          findings=final_findings,
      )

      return result

  def _build_complex_plan(self, task: str) -> Plan:
      """Build the dynamic DAG for a complex paper-analysis task."""
      plan = Plan()
      plan.add("Read paper and extract facts", task_id="read_paper")
      plan.add(
          "Analyze methodology and main claims",
          task_id="analyze_method",
          depends_on=["read_paper"],
          parallel_group="analysis",
      )
      plan.add(
          "Verify numerical claims with code",
          task_id="verify_data",
          depends_on=["read_paper"],
          parallel_group="analysis",
          condition="requires_verification",
      )
      plan.add(
          "Generate structured analysis report",
          task_id="generate_report",
          depends_on=["analyze_method", "verify_data"],
      )
      plan.add(
          "Generate academic presentation slides",
          task_id="generate_pptx",
          depends_on=["analyze_method", "verify_data"],
          condition="requires_pptx",
      )
      plan.add(
          "Adversarially review the output",
          task_id="review_report",
          depends_on=["generate_report", "generate_pptx"],
      )
      return plan

  def _select_plan(self, task: str, route, research_state: ResearchState) -> Plan:
      """根据配置选择静态 Plan 或动态 Plan。"""
      if self.plan_policy.use_dynamic_plan:
          plan = self.dynamic_planner.build_plan(task, route, research_state, self.plan_policy)
          if DynamicDAGPlanner.is_topologically_valid(plan) and plan.tasks:
              return plan
          # 动态规划失败时回退到静态 Plan
      return self._build_complex_plan(task)

  def _condition_requires_pptx(self, state: GraphState, _task) -> bool:
      task = state.get_artifact("task_text") or ""
      keywords = [r"\bppt\b", r"\bpptx\b", r"\bslides?\b", r"\bpresentation\b", "PPT", "幻灯片"]
      import re as _re
      return any(_re.search(k, task, _re.IGNORECASE) for k in keywords)

  def _condition_requires_verification(self, state: GraphState, _task) -> bool:
      task = state.get_artifact("task_text") or ""
      keywords = [r"\bverify\b", r"\bvalidate\b", r"\bnumerical\b", r"\bcode\b", "验证", "数值", "代码"]
      import re as _re
      return any(_re.search(k, task, _re.IGNORECASE) for k in keywords)

  def _emit(self, event_type: str, message: str) -> None:
      """Emit an orchestration event; currently a no-op logger."""
      print(f"[orchestrator:{event_type}] {message}")

  def _emit_progress(self, node_id: str, status: str) -> None:
      self._emit("step", f"{node_id}: {status}")
      event_map = {
          "started": TraceEventType.NODE_START,
          "done": TraceEventType.NODE_DONE,
          "failed": TraceEventType.NODE_FAILED,
          "replan": TraceEventType.REPLAN,
      }
      event_type = event_map.get(status)
      if event_type:
          self.trace_collector.add_event(
              event_type,
              data={"node_id": node_id, "status": status},
              node_id=node_id,
          )
      elif status.startswith("retry_"):
          self.trace_collector.add_event(
              TraceEventType.RETRY,
              data={"node_id": node_id, "status": status},
              node_id=node_id,
          )

  async def _replan(self, plan: Plan, failed_task: Task, reason: str, state: GraphState) -> Plan:
      """Dynamic replan hook used by DAGExecutor."""
      return await self.replanner.replan(plan, failed_task, reason, state)

  def _latest_findings(self, paper_dir: Path) -> dict:
      findings_path = paper_dir / "review" / "findings.md"
      if findings_path.exists():
          return parse_findings(findings_path)
      return {"verdict": "UNKNOWN", "critical": 0, "major": 0, "minor": 0}

  async def _handle_read_paper(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_reader(state.get_artifact("task_text"), paper_dir)
      if not result.success or not (paper_dir / "facts.json").exists():
          raise RuntimeError(result.error_message or "reader failed")
      am = ArtifactManager(paper_dir)
      paper, method = am.from_facts_json(paper_dir / "facts.json")
      am.save("paper", paper)
      am.save("method", method)
      state.set_artifact("paper_artifact", paper)
      state.set_artifact("method_artifact", method)
      return paper_dir / "facts.json"

  async def _handle_verify_data(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_verifier(state.get_artifact("task_text"), paper_dir)
      verified_path = paper_dir / "verified.json"
      am = ArtifactManager(paper_dir)
      verified_claims = am.from_verified_json(verified_path)
      am.save("verified_claims", verified_claims)
      state.set_artifact("verified_claims", verified_claims)
      return verified_path

  async def _handle_analyze_method(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      return paper_dir / "facts.json"

  async def _handle_generate_report(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_report_writer(state.get_artifact("task_text"), paper_dir)
      report_path = paper_dir / "report" / "report.md"
      if not result.success or not report_path.exists():
          raise RuntimeError(result.error_message or "report writer failed")
      am = ArtifactManager(paper_dir)
      report_artifact = am.from_report(report_path)
      am.save("report", report_artifact)
      state.set_artifact("report_artifact", report_artifact)
      return report_path

  async def _handle_generate_pptx(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_pptx_writer(state.get_artifact("task_text"), paper_dir)
      pptx_path = paper_dir / "slides.pptx"
      if not result.success or not pptx_path.exists():
          raise RuntimeError(result.error_message or "pptx writer failed")
      am = ArtifactManager(paper_dir)
      slide_artifact = am.from_pptx(pptx_path)
      am.save("slides", slide_artifact)
      state.set_artifact("slide_artifact", slide_artifact)
      return pptx_path

  async def _handle_review_report(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_reviewer(state.get_artifact("task_text"), paper_dir)
      findings = parse_findings(paper_dir / "review" / "findings.md")
      state.set_artifact("critic_result", findings)
      return findings

  async def _handle_revise_report(self, node: NodeSpec, task, state: GraphState):
      paper_dir = Path(state.get_artifact("paper_dir"))
      result = await self._run_revision_writer(state.get_artifact("task_text"), paper_dir)
      report_path = paper_dir / "report" / "report.md"
      if not result.success or not report_path.exists():
          raise RuntimeError(result.error_message or "revision failed")
      am = ArtifactManager(paper_dir)
      report_artifact = am.from_report(report_path)
      am.save("report", report_artifact)
      state.set_artifact("report_artifact", report_artifact)
      return report_path

  async def _run_report_writer(self, task: str, paper_dir: Path) -> AgentResult:
      spec = SubAgentSpec(
          name="report_writer",
          role="Report Writer",
          system_prompt=(
              "You are an academic report writer. Use the extracted facts and verified numbers "
              "to produce a well-structured analysis. Every factual claim must cite the source. "
              "If a claim cannot be verified, explicitly mark it as unverified."
          ),
          task_template=(
              f"Write a comprehensive analysis report for the task: {task}\n\n"
              f"Based on the paper at {paper_dir}.\n\n"
              "1. Read facts.json and verified.json (if they exist).\n"
              "2. Write report/sections/*.md and assemble report/report.md.\n"
              "3. Cite sources as [source: text.md Lxxx-Lyyy]."
          ),
          output_path="report/report.md",
          max_steps=35,
          enable_plan=True,
      )
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_pptx_writer(self, task: str, paper_dir: Path) -> AgentResult:
      spec = SubAgentSpec(
          name="pptx_writer",
          role="Presentation Writer",
          system_prompt=(
              "You are an academic presentation writer. Use the extracted facts and verified numbers "
              "to produce clear slides. Every factual claim must cite the source."
          ),
          task_template=(
              f"Generate academic slides for the task: {task}\n\n"
              f"Based on the paper at {paper_dir}.\n\n"
              "1. Read facts.json and verified.json (if they exist).\n"
              "2. Use skill_load nature-paper2ppt when appropriate, else generate_pptx.\n"
              "3. Cite sources as [source: text.md Lxxx-Lyyy]."
          ),
          allowed_tools=["read_file", "write_file", "edit_file", "apply_patch", "glob", "grep", "generate_pptx", "skill_load", "skill_list"],
          output_path="slides.pptx",
          max_steps=35,
          enable_plan=True,
      )
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_reader(self, task: str, paper_dir: Path) -> AgentResult:
      """Run the Reader sub-agent."""
      spec = SubAgentSpec(
          name="reader",
          role="Paper Reader and Fact Extractor",
          system_prompt=(
              "You are a careful academic paper reader. Your job is to read the paper "
              "and extract the key facts needed to answer the user's task. "
              "Save a structured JSON summary to facts.json in the workspace. "
              "Every factual claim must cite the source line range in text.md "
              "using [source: text.md Lxxx-Lyyy]."
          ),
          task_template=(
              f"Read the paper at {paper_dir} and extract key facts for: {task}\n\n"
              "1. Read text.md.\n"
              "2. Extract: title, authors, problem, method, key findings, numbers, limitations.\n"
              "3. Save the result as facts.json in the workspace.\n"
              "4. Include citations [source: text.md Lxxx-Lyyy] for each claim."
          ),
          allowed_tools=["read_file", "grep", "write_file"],
          output_path="facts.json",
          max_steps=12,
      )
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_verifier(self, task: str, paper_dir: Path) -> AgentResult:
      spec = SubAgentSpec(
          name="verifier",
          role="Numerical and Code Verifier",
          system_prompt=(
              "You are a numerical verifier. Read the paper, identify all quantitative claims, "
              "and verify them using the code_interpreter tool when possible. "
              "Save a JSON summary of verified and unverified claims to verified.json in the workspace."
          ),
          task_template=(
              f"Verify numerical claims in the paper at {paper_dir} related to: {task}\n\n"
              "1. Read facts.json (if it exists) and text.md.\n"
              "2. Identify all numbers, tables, and formulas relevant to the task.\n"
              "3. Use code_interpreter to recompute or sanity-check at least one key number.\n"
              "4. Save results as verified.json with 'claim', 'paper_value', 'computed_value', 'status' fields."
          ),
          allowed_tools=["read_file", "grep", "code_interpreter", "write_file"],
          output_path="verified.json",
          max_steps=15,
      )
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_writer(self, task: str, paper_dir: Path) -> AgentResult:
      import re
      has_pptx = any(re.search(p, task, re.IGNORECASE) for p in PaperDAGPlanner._PPTX_KEYWORDS)
      allowed = ["read_file", "write_file", "edit_file", "apply_patch", "glob", "grep"]
      if has_pptx:
          allowed.extend(["generate_pptx", "skill_load", "skill_list"])

      spec = SubAgentSpec(
          name="writer",
          role="Report and Presentation Writer",
          system_prompt=(
              "You are an academic report writer. Use the extracted facts and verified numbers "
              "to produce a well-structured analysis. Every factual claim must cite the source. "
              "If a claim cannot be verified, explicitly mark it as 'unverified'."
          ),
          task_template=(
              f"Write a comprehensive answer for the task: {task}\n\n"
              f"Based on the paper at {paper_dir}.\n\n"
              "1. Read facts.json and verified.json (if they exist).\n"
              "2. If generating a report, write report/sections/*.md and assemble report/report.md.\n"
              "3. If generating slides, use skill_load nature-paper2ppt when appropriate, else generate_pptx.\n"
              "4. Cite sources as [source: text.md Lxxx-Lyyy]."
          ),
          allowed_tools=allowed,
          output_path="report/report.md",
          max_steps=35,
          enable_plan=True,
      )
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_reviewer(self, task: str, paper_dir: Path) -> AgentResult:
      spec = PaperAnalysisPipeline.get_reviewer_spec(paper_dir)
      spec.name = "reviewer"
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_revision_writer(self, task: str, paper_dir: Path) -> AgentResult:
      findings_path = paper_dir / "review" / "findings.md"
      spec = PaperAnalysisPipeline.get_revision_spec(paper_dir, findings_path)
      spec.name = "revision_writer"
      return await self._run_sub_agent(spec, paper_dir)

  async def _run_sub_agent(self, spec: SubAgentSpec, paper_dir: Path, context_xml: str = "") -> AgentResult:
      """Run a single SubAgentSpec directly as an Agent in the shared workspace.

      The Agent runs with ``enable_orchestration=False`` so it does not recurse
      into the SmartOrchestrator, and with ``enable_plan=False`` so the sub-agent
      follows its own explicit instructions rather than inferring a plan from the task text.
      """
      tools = ToolRegistry.create_default(paper_dir)
      if spec.allowed_tools:
          for name in list(tools.list_names()):
              if name not in spec.allowed_tools:
                  tools.unregister(name)

      max_steps = spec.max_steps or 25
      harness = Harness(paper_dir, max_steps=max_steps)

      system_prompt = spec.system_prompt or "You are a rigorous academic-paper analysis agent."
      context_xml = context_xml or spec.context_xml or self._current_context_xml
      if context_xml:
          system_prompt += f"\n\n<context_for_this_node>\n{context_xml}\n</context_for_this_node>"

      config = AgentConfig(
          name=spec.name,
          system_prompt=system_prompt,
          model=self.base_config.model,
          max_steps=max_steps,
            enable_plan=spec.enable_plan,
          enable_budget_note=False,
          enable_judge_review=False,
          enable_hierarchical_memory=False,
          enable_orchestration=False,
      )

      agent = Agent(
          config=config,
          tools=tools,
          llm_client=self.llm,
          harness=harness,
          workspace_dir=paper_dir,
          trace_collector=self.trace_collector,
      )

      result = await agent.run(spec.task_template)

      # Enforce artifact existence for the sub-agent.
      expected = paper_dir / spec.output_path
      if spec.output_path and not expected.exists():
          if result.success:
              result.success = False
              result.error_message = f"missing expected artifact: {spec.output_path}"
          result.final_output = f"[Missing artifact {spec.output_path}]\n{result.final_output}"

      return result

  def _assemble_final_output(self, paper_dir: Path) -> str:
      """Read the primary artifact and return it as the final output."""
      candidates = [
          paper_dir / "report" / "report.md",
          paper_dir / "output" / "report.md",
          paper_dir / "report.md",
      ]
      for path in candidates:
          if path.exists():
              return path.read_text(encoding="utf-8", errors="replace")

      # Fallback: concat facts + verified
      parts = []
      for name in ("facts.json", "verified.json"):
          path = paper_dir / name
          if path.exists():
              parts.append(f"--- {name} ---\n" + path.read_text(encoding="utf-8", errors="replace"))
      return "\n\n".join(parts) if parts else "[No output artifact generated]"

  def _write_status(self, paper_dir: Path, key: str, message: str) -> None:
      status_path = paper_dir / "orchestration_status.json"
      data = {}
      if status_path.exists():
          try:
              data = json.loads(status_path.read_text(encoding="utf-8"))
          except Exception:
              pass
      data[key] = message
      status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
