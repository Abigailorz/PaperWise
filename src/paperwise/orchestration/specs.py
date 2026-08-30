"""Sub-agent specifications and shared helpers for the SmartOrchestrator.

This module consolidates the SubAgentSpec dataclass and the review/revision
specs that were previously split between ``paperwise.agents.orchestrator``
and ``paperwise.orchestration.orchestrator``. Keeping them here removes the
circular/indirect dependency on the legacy AgentOrchestrator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SubAgentSpec:
    """Specification for a single specialist sub-agent.

    Aligned with NodeSpec so it can be registered and executed by the DAG Executor.
    """

    name: str
    role: str
    system_prompt: str
    task_template: str
    allowed_tools: list[str] = field(default_factory=list)
    output_path: str = ""
    max_steps: int = 0
    enable_plan: bool = False
    context_xml: str = ""

    def to_node_spec(self, node_id: str = "", category: str = "") -> NodeSpec:
        """Convert this sub-agent spec into a standardized NodeSpec."""
        return NodeSpec(
            id=node_id or self.name,
            category=category or "general",
            name=self.role or self.name,
            description=self.task_template[:200],
            system_prompt=self.system_prompt,
            task_template=self.task_template,
            allowed_tools=self.allowed_tools,
            output_path=self.output_path,
            max_steps=self.max_steps,
            enable_plan=self.enable_plan,
            context_xml=self.context_xml,
        )


class PaperAnalysisPipeline:
    """Predefined sub-agent specs for the paper-analysis DAG."""

    @staticmethod
    def get_reviewer_spec(paper_dir: Path) -> SubAgentSpec:
        return SubAgentSpec(
            name="reviewer",
            role="Quality Reviewer",
            system_prompt="""You are an adversarial quality reviewer for academic paper analysis reports.
Your role is to CHALLENGE the most important claims in the report.

<review_method>
Work BOUNDED, not exhaustive:
1. Read facts.json (it has per-claim line citations) and report/report.md.
2. Select ONLY the 10-15 most important claims (numbers, comparisons, method
   names, core conclusions). Do NOT try to verify every sentence.
3. Verify each selected claim with ONE targeted `grep` for its key term/number
   in text.md. Use facts.json line citations as anchors — do not re-read the
   whole paper top to bottom.
4. Flag a claim as a potential hallucination if grep finds no supporting evidence.
5. Check completeness: did the report miss major sections (method/results/limitations)?
</review_method>

<efficiency_constraint>
Hard rules to finish within your step budget:
- Prefer grep over read_file. One grep per claim.
- Never read text.md start-to-finish; only grep + read small cited ranges.
- After your FIRST verification pass, IMMEDIATELY write review/findings.json
  (machine-readable, exact schema below), THEN write review/findings.md.
  findings.json MUST exist even if you run low on steps.
</efficiency_constraint>

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

CRITICAL: In addition to findings.md, also save a machine-readable
``review/findings.json`` with this exact schema:
{
  "verdict": "PASS|REVISE|REJECT",
  "counts": {"critical": 0, "major": 0, "minor": 0, "flagged": 0},
  "flagged_claims": [
    {"quote": "...", "evidence": "...", "severity": "critical|major|minor"}
  ],
  "missing_aspects": ["..."]
}

DO NOT modify the report. Only review and flag.""",
            task_template=f"""Adversarially review the report for the paper at: {paper_dir}

Work efficiently — do NOT re-read text.md line by line. Instead:
1. Read facts.json (already extracted, has line citations) and report/report.md.
2. Pick the 10-15 most important / most suspicious factual claims in the report
   (numbers, comparisons, method names, conclusions).
3. Verify each with a targeted `grep` for the key term/number in text.md
   (one grep per claim, not full reads). Use facts.json citations as anchors.
4. Flag any claim whose grep finds no supporting evidence (potential hallucination).
5. Check completeness: did the report miss major sections (method / results / limitations)?
6. EARLY: after your first verification pass, immediately save current findings
   to review/findings.json (machine-readable, exact schema above), then refine
   and also write review/findings.md. Guarantee the JSON exists even if low on steps.

CRITICAL: Be adversarial but bounded — verify the top claims thoroughly rather
than exhaustively re-reading the whole paper. If you cannot find evidence for a
claim, flag it. The user depends on your thoroughness on the IMPORTANT claims.""",
            allowed_tools=["read_file", "grep", "glob", "write_file"],
            output_path="review/findings.md",
            max_steps=40,
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
    """Parse reviewer findings into a Critic-compatible dict.

    Prefer the machine-readable ``review/findings.json`` if it exists;
    fall back to parsing ``findings.md`` with regex.

    Returns:
        {"verdict": str, "critical": int, "major": int, "minor": int,
         "flagged": int, "summary": dict, "status": str, "severity": dict}
    """
    default = {
        "verdict": "UNKNOWN",
        "critical": 0,
        "major": 0,
        "minor": 0,
        "flagged": 0,
        "summary": {},
    }

    json_path = findings_path.with_suffix(".json")
    if not json_path.exists() and findings_path.name.lower().endswith(".md"):
        json_path = findings_path.parent / "findings.json"

    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            counts = data.get("counts", {})
            summary = {k: v for k, v in data.items() if k not in (
                "verdict", "counts", "flagged_claims", "missing_aspects"
            )}
            summary.update({
                "Total claims checked": counts.get("total", 0),
                "Verified": counts.get("verified", 0),
                "Flagged": counts.get("flagged", 0),
            })
            return {
                "verdict": data.get("verdict", "UNKNOWN").upper(),
                "status": data.get("status") or data.get("verdict", "UNKNOWN").upper(),
                "severity": {
                    "critical": counts.get("critical", 0),
                    "major": counts.get("major", 0),
                    "minor": counts.get("minor", 0),
                },
                "critical": counts.get("critical", 0),
                "major": counts.get("major", 0),
                "minor": counts.get("minor", 0),
                "flagged": counts.get("flagged", 0),
                "summary": summary,
            }
        except Exception:
            pass

    text = findings_path.read_text(encoding="utf-8", errors="replace")

    verdict = "UNKNOWN"
    verdict_section = text.split("## Verdict", 1)[-1] if "## Verdict" in text else text
    for keyword in ("REJECT", "REVISE", "PASS"):
        if re.search(rf"\b{keyword}\b", verdict_section, re.IGNORECASE):
            verdict = keyword
            break

    flagged_section = text.split("## Flagged Claims", 1)[-1] if "## Flagged Claims" in text else ""
    flagged_section = flagged_section.split("## Missing Aspects", 1)[0] if "## Missing Aspects" in flagged_section else flagged_section
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
        "status": verdict,
        "severity": {"critical": critical, "major": major, "minor": minor},
        "critical": critical,
        "major": major,
        "minor": minor,
        "flagged": flagged,
        "summary": summary,
    }
