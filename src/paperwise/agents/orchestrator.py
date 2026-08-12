"""多 Agent 编排器 — Manager + Worker 不共享上下文架构

对应书中:
- 10.1 节：多 Agent 协作的分类框架
- 10.4.4 节：管理者模式（中心化协调）
- 10.4.2 节：Agent 间的通信与控制
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from paperwise.core.types import AgentConfig, AgentResult
from paperwise.core.llm_client import LLMClient
from paperwise.tools.registry import ToolRegistry
from paperwise.harness.harness import Harness


@dataclass
class SubAgentSpec:
    """子 Agent 规格定义"""
    name: str
    role: str
    system_prompt: str
    task_template: str
    allowed_tools: list[str]  # 允许使用的工具子集
    output_path: str           # 产物输出路径（共享文件系统中的位置）


@dataclass
class OrchestrationResult:
    """编排结果"""
    success: bool = True
    sub_results: dict[str, AgentResult] = field(default_factory=dict)
    total_steps: int = 0
    combined_output: str = ""
    error: str = ""


def parse_findings(findings_path: Path) -> dict:
    """解析审核 Agent 的 findings.md，提取判定与严重度统计。

    findings.md 结构（见 get_reviewer_spec 的 output_format）：
      ## Review Summary
      - Total claims checked: N / Verified: N / Flagged: N / Hallucinations: N
      ## Flagged Claims
      每条含 severity (critical/major/minor)
      ## Verdict
      - PASS / REVISE / REJECT

    Returns:
        {"verdict": str, "critical": int, "major": int, "minor": int,
         "flagged": int, "summary": str}
    """
    text = findings_path.read_text(encoding="utf-8", errors="replace")

    # Verdict：仅在 "## Verdict" 段内匹配（避免命中正文中的词）
    verdict = "UNKNOWN"
    verdict_section = text.split("## Verdict", 1)[-1] if "## Verdict" in text else text
    for keyword in ("REJECT", "REVISE", "PASS"):
        if re.search(rf"\b{keyword}\b", verdict_section, re.IGNORECASE):
            verdict = keyword
            break

    # 严重度统计：统计 Flagged Claims 段中出现的严重度词
    flagged_section = text.split("## Flagged Claims", 1)[-1]
    flagged_section = flagged_section.split("## Missing Aspects", 1)[0]
    critical = len(re.findall(r"\bcritical\b", flagged_section, re.IGNORECASE))
    major = len(re.findall(r"\bmajor\b", flagged_section, re.IGNORECASE))
    minor = len(re.findall(r"\bminor\b", flagged_section, re.IGNORECASE))
    flagged = len(re.findall(r"(?m)^\s*[-*]\s+", flagged_section))

    # Review Summary 数字（若存在）
    summary = {}
    for key in ("Total claims checked", "Verified", "Flagged", "Hallucinations"):
        m = re.search(rf"{key}\s*:?\s*(\d+)", text, re.IGNORECASE)
        if m:
            summary[key] = int(m.group(1))

    return {
        "verdict": verdict,
        "critical": critical,
        "major": major,
        "minor": minor,
        "flagged": flagged,
        "summary": summary,
    }


class AgentOrchestrator:
    """多 Agent 编排器 — Manager 模式

    设计决策（书中 10.1.1 节）：
    - 不共享上下文：各 Worker 独立上下文，通过文件系统交换产物
    - Manager 负责：任务分解、分配、监控、汇总
    - Worker 负责：在自己的上下文中完成子任务

    子 Agent 间通信（IPC）：
    - 工具调用参数：Manager → Worker 任务分发（结构化 JSON）
    - 共享文件系统：Worker 间传递中间产物（parsed_paper/ → report/）
    """

    def __init__(
        self,
        llm_client: LLMClient,
        workspace: Path,
        model: str = "deepseek-chat",
        max_steps_per_agent: int = 25,
    ):
        self.llm = llm_client
        self.workspace = Path(workspace)
        self.model = model
        self.max_steps = max_steps_per_agent
        self.callbacks: list[Callable] = []

    def on_event(self, callback: Callable) -> None:
        """注册事件回调。"""
        self.callbacks.append(callback)

    def _emit(self, event: str, detail: str) -> None:
        for cb in self.callbacks:
            try:
                cb(event, detail)
            except Exception:
                pass

    async def run_pipeline(
        self,
        specs: list[SubAgentSpec],
        shared_context: dict = None,
    ) -> OrchestrationResult:
        """运行 Agent 管道 — 顺序执行，产物通过文件系统传递。

        对应书中 10.3 节：共享上下文的多 Agent 协作
        这里使用"不共享上下文 + 共享文件系统"实现相同效果

        Args:
            specs: 按执行顺序排列的子 Agent 规格列表
            shared_context: 所有 Agent 共享的初始上下文

        Returns:
            OrchestrationResult
        """
        result = OrchestrationResult()

        for i, spec in enumerate(specs):
            self._emit("agent_start", f"[{i+1}/{len(specs)}] {spec.name}: {spec.role}")

            try:
                # 创建 Agent
                agent = await self._create_agent(spec)

                # 构建任务（含上游 Agent 产物路径）
                task = spec.task_template
                if shared_context:
                    for key, value in shared_context.items():
                        task = task.replace(f"{{{{{key}}}}}", str(value))

                # 执行
                agent_result = await agent.run(task)
                result.sub_results[spec.name] = agent_result
                result.total_steps += agent_result.steps

                if agent_result.success:
                    self._emit("agent_done", f"{spec.name}: done in {agent_result.steps} steps")
                else:
                    self._emit("agent_error", f"{spec.name}: {agent_result.error_message}")
                    result.success = False
                    result.error = f"Agent '{spec.name}' failed: {agent_result.error_message}"
                    break

            except Exception as e:
                self._emit("agent_error", f"{spec.name}: {e}")
                result.success = False
                result.error = str(e)
                break

        return result

    async def run_parallel(
        self,
        specs: list[SubAgentSpec],
        shared_context: dict = None,
    ) -> OrchestrationResult:
        """并行运行多个独立 Agent。

        对应书中 10.4.3 节：对等协作模式
        各 Agent 独立上下文，互不阻塞，结果汇总

        Args:
            specs: 可并行执行的子 Agent 列表
            shared_context: 共享上下文

        Returns:
            OrchestrationResult 含所有子结果
        """
        result = OrchestrationResult()

        async def run_one(spec: SubAgentSpec) -> tuple[str, AgentResult]:
            agent = await self._create_agent(spec)
            task = spec.task_template
            if shared_context:
                for key, value in shared_context.items():
                    task = task.replace(f"{{{{{key}}}}}", str(value))
            return spec.name, await agent.run(task)

        tasks = [run_one(spec) for spec in specs]
        self._emit("parallel_start", f"Running {len(specs)} agents in parallel...")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                result.success = False
                result.error = str(r)
            else:
                name, agent_result = r
                result.sub_results[name] = agent_result
                result.total_steps += agent_result.steps

        return result

    async def run_paper_analysis(
        self,
        paper_dir: Path,
        max_review_rounds: int = 3,
        shared_context: dict = None,
    ) -> dict:
        """端到端论文分析 + 审核回流闭环（revise-until-pass）。

        流程：Analyst → ReportWriter → [Reviewer → 若有问题 → RevisionWriter] 循环
        审核判定 PASS 且无 critical/major 问题即结束；
        超过 max_review_rounds 仍不通过 → 标记 needs_manual_review。

        Returns:
            {"status": "passed"|"needs_manual_review", "rounds": [...],
             "record_path": str}
        """
        paper_dir = Path(paper_dir)
        from paperwise.agents.orchestrator import PaperAnalysisPipeline

        self._emit("pipeline", "Phase 1/2: Analyst + Report Writer")
        pipeline_result = await self.run_pipeline(
            [
                PaperAnalysisPipeline.get_analyst_spec(paper_dir),
                PaperAnalysisPipeline.get_report_writer_spec(paper_dir),
            ],
            shared_context,
        )
        if not pipeline_result.success:
            return {
                "status": "failed",
                "rounds": [],
                "error": pipeline_result.error,
            }

        status = "passed"
        rounds: list[dict] = []

        for rnd in range(1, max_review_rounds + 1):
            self._emit("review_round", f"Round {rnd}/{max_review_rounds} adversarial review")

            review_result = await self.run_pipeline(
                [PaperAnalysisPipeline.get_reviewer_spec(paper_dir)],
                shared_context,
            )

            findings_path = paper_dir / "review" / "findings.md"
            findings = (
                parse_findings(findings_path) if findings_path.exists()
                else {"verdict": "UNKNOWN", "critical": 0, "major": 0,
                      "minor": 0, "flagged": 0, "summary": {}}
            )
            record = {
                "round": rnd,
                "reviewer_success": review_result.success,
                **findings,
            }
            rounds.append(record)

            passed = (
                findings["verdict"] in ("PASS", "UNKNOWN")
                and findings["critical"] == 0
                and findings["major"] == 0
            )
            if passed:
                self._emit("review_done", f"Round {rnd} verdict: PASS")
                break

            if rnd < max_review_rounds:
                self._emit("review_revise", f"Round {rnd} verdict: {findings['verdict']} — revising")
                revision = await self.run_pipeline(
                    [PaperAnalysisPipeline.get_revision_spec(paper_dir, findings_path)],
                    shared_context,
                )
                record["revision_success"] = revision.success
            else:
                status = "needs_manual_review"
                self._emit("review_manual", "Max rounds reached — marking for manual review")

        record_path = paper_dir / "review" / "review_record.json"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps({
                "paper_dir": str(paper_dir),
                "status": status,
                "rounds": rounds,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"status": status, "rounds": rounds, "record_path": str(record_path)}

    async def _create_agent(self, spec: SubAgentSpec):
        """创建配置好的 Agent 实例。"""
        from paperwise.core.agent import Agent
        from paperwise.tools.registry import ToolRegistry

        # 子 Agent 使用独立的工作子目录
        agent_workspace = self.workspace / spec.name
        agent_workspace.mkdir(parents=True, exist_ok=True)

        # 工具集（仅暴露允许的工具）
        tools = ToolRegistry.create_default(agent_workspace)
        if spec.allowed_tools:
            for name in list(tools.list_names()):
                if name not in spec.allowed_tools:
                    tools.unregister(name)

        # 注册消息邮箱 + receive_message 工具（Manager 可通过 send_message 投递）
        from paperwise.core.bus import AgentBus
        from paperwise.tools.collab_tools import ReceiveMessageTool
        AgentBus.instance().register(spec.name)
        tools.register(ReceiveMessageTool(agent_workspace, agent_name=spec.name))
        for tool_name in tools.list_names():
            tools.get(tool_name)._agent_name = spec.name

        harness = Harness(agent_workspace, max_steps=self.max_steps)

        config = AgentConfig(
            name=spec.name,
            system_prompt=spec.system_prompt,
            model=self.model,
            max_steps=self.max_steps,
        )

        agent = Agent(
            config=config,
            tools=tools,
            llm_client=self.llm,
            harness=harness,
            workspace_dir=agent_workspace,
        )

        # 转发事件
        agent.on_event(lambda t, d: self._emit(f"sub:{spec.name}:{t}", d))
        return agent


# === 预定义 Agent 规格 ===

class PaperAnalysisPipeline:
    """论文分析的标准 Agent 管道定义。

    管道: Parser → Analyst → ReportWriter → Reviewer
    """

    @staticmethod
    def get_analyst_spec(paper_dir: Path) -> SubAgentSpec:
        from paperwise.agents.paper_analyst import PaperAnalystConfig
        return SubAgentSpec(
            name="analyst",
            role="Deep Paper Analyst",
            system_prompt=PaperAnalystConfig.get_system_prompt(),
            task_template=PaperAnalystConfig.get_analysis_task(paper_dir),
            allowed_tools=["read_file", "grep", "glob", "code_interpreter",
                          "write_file", "edit_file"],
            output_path="analysis/",
        )

    @staticmethod
    def get_report_writer_spec(paper_dir: Path) -> SubAgentSpec:
        from paperwise.generators.report import ReportGenerator
        gen = ReportGenerator(paper_dir)
        return SubAgentSpec(
            name="report_writer",
            role="Report Writer",
            system_prompt=gen.get_report_system_prompt(),
            task_template=gen.get_report_task(str(paper_dir)),
            allowed_tools=["read_file", "write_file", "edit_file", "glob", "grep"],
            output_path="report/",
        )

    @staticmethod
    def get_reviewer_spec(paper_dir: Path) -> SubAgentSpec:
        return SubAgentSpec(
            name="reviewer",
            role="Quality Reviewer",
            system_prompt="""You are an adversarial quality reviewer for academic paper analysis reports.
Your role is to CHALLENGE every claim in the report.

<review_method>
1. For each factual claim in the report, search the paper for evidence
2. Flag any claim that cannot be verified
3. Identify hallucinations: fabricated numbers, methods, or conclusions
4. Check if the report missed important aspects of the paper
5. Be adversarial: assume the report is wrong until proven right
</review_method>

<output_format>
Save findings to review/findings.md with this structure:
## Review Summary
- Total claims checked: N
- Verified: N
- Flagged: N
- Hallucinations: N

## Flagged Claims
For each flagged claim: quote the report text, cite the paper evidence (or lack thereof), severity (critical/major/minor)

## Missing Aspects
What important aspects of the paper did the report miss?

## Verdict
- PASS: All claims verified, all aspects covered
- REVISE: Minor issues, revise and resubmit
- REJECT: Critical hallucinations or major omissions
</output_format>

DO NOT modify the report. Only review and flag.""",
            task_template=f"""Adversarially review the report for the paper at: {paper_dir}

1. Read {paper_dir}/text.md (the original paper)
2. Read {paper_dir}/report/report.md (the generated report)
3. For EVERY factual claim in the report:
   a. Search the paper for supporting evidence
   b. If found, note the evidence
   c. If NOT found, flag as potential hallucination
4. Check numerical claims: re-verify all numbers against the paper
5. Check completeness: are all important aspects covered?
6. Save findings to review/findings.md

CRITICAL: Be adversarial. If you cannot find evidence for a claim, flag it.
Do not assume the report is correct. The user depends on your thoroughness.""",
            allowed_tools=["read_file", "grep", "glob", "write_file"],
            output_path="review/",
        )

    @staticmethod
    def get_adversarial_reviewer_spec(paper_dir: Path, section: str) -> SubAgentSpec:
        """创建针对特定章节的对抗式审查 Agent。

        对应书中 10.4.3 节：对等协作与相互制衡。
        与普通 reviewer 不同，adversarial reviewer 被指令"假设报告是错的"。
        """
        return SubAgentSpec(
            name=f"adversary_{section}",
            role=f"Adversarial Reviewer for {section}",
            system_prompt=f"""You are an ADVERSARIAL reviewer specialized in verifying '{section}' sections.

Your SOLE job is to find errors, omissions, and fabrications. You are NOT to praise.
Assume every statement in the report is wrong until you find proof in the paper.

<rules>
1. Extract every factual claim from report/sections/{section}.md
2. For each claim, search the paper for evidence
3. Any claim without evidence = FLAGGED
4. Any number that doesn't match the paper = FLAGGED
5. Report what the paper ACTUALLY says vs what the report claims
</rules>

Be ruthless. The user needs to know what's accurate and what's not.""",
            task_template=f"""Adversarially review the '{section}' section of the report at: {paper_dir}

1. Read {paper_dir}/text.md (paper)
2. Read {paper_dir}/report/sections/{section}.md (report section)
3. Cross-reference every claim
4. Save findings to review/adversarial_{section}.md""",
            allowed_tools=["read_file", "grep", "write_file"],
            output_path=f"review/adversarial_{section}.md",
        )

    @staticmethod
    def get_revision_spec(paper_dir: Path, findings_path: Path) -> SubAgentSpec:
        """创建修订 Agent — 根据审核 findings 修正报告。

        对应书中 1.2 节纠正机制：验证发现问题后自动修正。
        修订遵循"最小改动"原则：只改被标记的部分，保留正确内容。
        """
        paper_dir = Path(paper_dir)
        findings_path = Path(findings_path)
        return SubAgentSpec(
            name="revision_writer",
            role="Report Revision Writer",
            system_prompt="""You are PaperWise Revision Writer. Your job is to fix a
report based on an adversarial review's findings.

<revision_principles>
1. Read every flagged claim in the findings carefully
2. For each flagged claim: locate it in report/sections/*.md, verify against
   the paper (text.md), and rewrite it with correct evidence and line citations
3. Fix ONLY what is flagged. Do not rewrite correct sections
4. Remove or correct fabricated content (hallucinations) completely
5. If a claim cannot be verified in the paper, remove it or mark it clearly
   as "unverified" rather than guessing
6. After fixing sections, re-assemble report/report.md (frontmatter + TOC + all sections)
7. Append a short revision log at the bottom of report.md listing what changed
   and why (reference the findings by severity)
</revision_principles>

<output_format>
Keep the existing section structure. Only content inside sections changes.
The final report must still be at report/report.md.
</output_format>
""",
            task_template=f"""Revise the analysis report for the paper at: {paper_dir}
based on the adversarial review findings.

1. Read {paper_dir}/text.md (the original paper)
2. Read {findings_path} (the review findings — these are the issues to fix)
3. Read the current report: {paper_dir}/report/report.md and report/sections/*.md
4. Apply the revision principles:
   a. Fix every flagged claim with correct, cited evidence
   b. Remove or mark unverifiable claims
   c. Do NOT change claims that were NOT flagged
5. Re-assemble report/report.md with all corrected sections
6. Append a revision log (what changed and why)

CRITICAL: Fix what is broken, keep what is correct. Accuracy over speed.""",
            allowed_tools=["read_file", "grep", "glob", "write_file", "edit_file"],
            output_path="report/",
        )
