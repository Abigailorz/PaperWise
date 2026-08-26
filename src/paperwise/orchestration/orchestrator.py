"""Smart orchestrator: route simple tasks to a single agent and complex tasks through a DAG of specialist agents.

When ``enable_orchestration`` is false, the legacy single-agent path is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from paperwise.core.agent import Agent
from paperwise.core.types import AgentConfig, AgentResult
from paperwise.core.plan import Plan
from paperwise.orchestration.classifier import TaskClassifier, TaskComplexity
from paperwise.orchestration.paper_dag import PaperDAGPlanner
from paperwise.orchestration.specs import SubAgentSpec, PaperAnalysisPipeline, parse_findings
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
      max_review_rounds: int = 1,
  ):
      self.llm = llm_client
      self.workspace = Path(workspace)
      self.workspace.mkdir(parents=True, exist_ok=True)
      self.base_config = base_config or AgentConfig()
      self.classifier = classifier or TaskClassifier(llm_client, workspace)
      self.max_review_rounds = max_review_rounds

  async def run(self, task: str, paper_dir: Optional[Path] = None) -> AgentResult:
      """Run a task through the appropriate execution path."""
      complexity = self.classifier.classify(task)
      paper_dir = Path(paper_dir) if paper_dir else self.workspace

      if complexity.is_simple:
          return await self._run_simple(task, paper_dir)

      return await self._run_complex(task, paper_dir)

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
      """DAG multi-agent execution for complex tasks.

      All sub-agents run in the shared ``paper_dir`` workspace so that
      artifacts (facts.json, verified.json, report/report.md, review/findings.md)
      are visible to downstream agents.
      """
      plan = PaperDAGPlanner.build(task)

      # Ensure the paper directory has the files the sub-agents expect.
      if not (paper_dir / "text.md").exists() and (self.workspace / "paper" / "text.md").exists():
          paper_dir = self.workspace / "paper"

      if not (paper_dir / "text.md").exists():
          return AgentResult(
              final_output=f"[No paper found] text.md missing in {paper_dir}",
              success=False,
              error_message="missing_text_md",
          )

      total_steps = 0
      sub_results: dict[str, AgentResult] = {}

      # 1. Reader Agent: extract facts into facts.json
      if plan.get("read_paper"):
          reader_result = await self._run_reader(task, paper_dir)
          sub_results["reader"] = reader_result
          total_steps += reader_result.steps
          plan.mark_done("read_paper", evidence=str(paper_dir / "facts.json"))
          if not reader_result.success or not (paper_dir / "facts.json").exists():
              return AgentResult(
                  final_output=f"[Reader failed] {reader_result.error_message}",
                  success=False,
                  error_message=f"reader_failed: {reader_result.error_message}",
              )

      # 2. Verifier Agent: verify numerical claims (optional)
      if plan.get("verify_data"):
          verifier_result = await self._run_verifier(task, paper_dir)
          sub_results["verifier"] = verifier_result
          total_steps += verifier_result.steps
          plan.mark_done("verify_data", evidence=str(paper_dir / "verified.json"))
          if not verifier_result.success:
              self._write_status(paper_dir, "verification_failed", verifier_result.error_message)

      # 3. Writer Agent: produce report / slides
      writer_result = await self._run_writer(task, paper_dir)
      sub_results["writer"] = writer_result
      total_steps += writer_result.steps
      if plan.get("generate_report"):
          plan.mark_done("generate_report", evidence=str(paper_dir / "report" / "report.md"))
      if plan.get("generate_pptx"):
          plan.mark_done("generate_pptx", evidence="slides.pptx")

      if not writer_result.success or not (paper_dir / "report" / "report.md").exists():
          return AgentResult(
              final_output=f"[Writer failed] {writer_result.error_message}",
              success=False,
              error_message=f"writer_failed: {writer_result.error_message}",
          )

      # 4. Review + revision loop
      if plan.get("review_report"):
          for rnd in range(1, self.max_review_rounds + 1):
              review_result = await self._run_reviewer(task, paper_dir)
              sub_results["reviewer"] = review_result
              total_steps += review_result.steps
              plan.mark_done("review_report", evidence=str(paper_dir / "review" / "findings.md"))

              findings_path = paper_dir / "review" / "findings.md"
              if findings_path.exists():
                  findings = parse_findings(findings_path)
              else:
                  findings = {"verdict": "UNKNOWN", "critical": 0, "major": 0}

              if review_result.success and findings["critical"] == 0 and findings["major"] == 0:
                  break

              if rnd < self.max_review_rounds:
                  revision_result = await self._run_revision_writer(task, paper_dir)
                  sub_results["revision_writer"] = revision_result
                  total_steps += revision_result.steps
                  plan.mark_done("revise_report", evidence=f"round{rnd}")
                  if not revision_result.success or not (paper_dir / "report" / "report.md").exists():
                      return AgentResult(
                          final_output=f"[Revision failed] {revision_result.error_message}",
                          success=False,
                          error_message=f"revision_failed: {revision_result.error_message}",
                      )

      # Collect final output
      final_text = self._assemble_final_output(paper_dir)
      return AgentResult(
          final_output=final_text,
          success=True,
          steps=total_steps,
          tool_stats={},
      )

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

  async def _run_sub_agent(self, spec: SubAgentSpec, paper_dir: Path) -> AgentResult:
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

      config = AgentConfig(
          name=spec.name,
          system_prompt=spec.system_prompt,
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
