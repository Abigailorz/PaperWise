"""Sub-agent specifications and shared helpers for the SmartOrchestrator.

This module consolidates the SubAgentSpec dataclass and the review/revision
specs that were previously split between ``paperwise.agents.orchestrator``
and ``paperwise.orchestration.orchestrator``. Keeping them here removes the
circular/indirect dependency on the legacy AgentOrchestrator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SubAgentSpec:
    """Specification for a single specialist sub-agent."""

    name: str
    role: str
    system_prompt: str
    task_template: str
    allowed_tools: list[str] = field(default_factory=list)
    output_path: str = ""
    max_steps: int = 0
    enable_plan: bool = False


class PaperAnalysisPipeline:
    """Predefined sub-agent specs for the paper-analysis DAG."""

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

1. Read text.md (the original paper)
2. Read report/report.md (the generated report)
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
            output_path="review/findings.md",
            max_steps=25,
            enable_plan=True,
        )

    @staticmethod
    def get_revision_spec(paper_dir: Path, findings_path: Path) -> SubAgentSpec:
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

1. Read text.md (the original paper)
2. Read {findings_path} (the review findings - these are the issues to fix)
3. Read the current report: report/report.md and report/sections/*.md
4. Apply the revision principles:
   a. Fix every flagged claim with correct, cited evidence
   b. Remove or mark unverifiable claims
   c. Do NOT change claims that were NOT flagged
5. Re-assemble report/report.md with all corrected sections
6. Append a revision log (what changed and why)

CRITICAL: Fix what is broken, keep what is correct. Accuracy over speed.""",
            allowed_tools=["read_file", "grep", "glob", "write_file", "edit_file", "apply_patch"],
            output_path="report/report.md",
            max_steps=35,
            enable_plan=True,
        )


def parse_findings(findings_path: Path) -> dict:
    """Parse reviewer findings.md and extract verdict + severity counts.

    Returns:
        {"verdict": str, "critical": int, "major": int, "minor": int,
         "flagged": int, "summary": dict}
    """
    text = findings_path.read_text(encoding="utf-8", errors="replace")

    verdict = "UNKNOWN"
    verdict_section = text.split("## Verdict", 1)[-1] if "## Verdict" in text else text
    for keyword in ("REJECT", "REVISE", "PASS"):
        if re.search(rf"\b{keyword}\b", verdict_section, re.IGNORECASE):
            verdict = keyword
            break

    flagged_section = text.split("## Flagged Claims", 1)[-1]
    flagged_section = flagged_section.split("## Missing Aspects", 1)[0]
    critical = len(re.findall(r"\bcritical\b", flagged_section, re.IGNORECASE))
    major = len(re.findall(r"\bmajor\b", flagged_section, re.IGNORECASE))
    minor = len(re.findall(r"\bminor\b", flagged_section, re.IGNORECASE))
    flagged = len(re.findall(r"(?m)^\s*[-*]\s+", flagged_section))

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
