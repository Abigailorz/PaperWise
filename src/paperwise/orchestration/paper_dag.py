"""DAG planner for complex paper-analysis tasks.

Builds a topologically-sound Plan whose nodes map to specialist sub-agents:
reader, verifier, writer, reviewer, revision_writer.
"""

from __future__ import annotations

import re

from paperwise.core.plan import Plan, TaskStatus


class PaperDAGPlanner:
    """Build complexity-aware DAG plans for paper analysis tasks."""

    _VERIFY_KEYWORDS = [
        r"\bverify\b",
        r"\bvalidat(?:e|ion)\b",
        r"\bnumerical\b",
        r"\bnumber\b",
        r"\bcode\b",
        r"\b验证\b",
        r"\b数值\b",
        r"\b代码\b",
    ]
    _REPORT_KEYWORDS = [
        r"\breport\b",
        r"\banalysis\b",
        r"\bsummary\b",
        r"\breview\b",
        r"\b报告\b",
        r"\b分析\b",
        r"\b总结\b",
    ]
    _PPTX_KEYWORDS = [
        r"\bppt\b",
        r"\bpptx\b",
        r"\bslides?\b",
        r"\bpresentation\b",
        r"\bPPT\b",
        r"\b幻灯片\b",
    ]
    _CRITICAL_KEYWORDS = [
        r"\bcritical\b",
        r"\blimitation\b",
        r"\bweakness\b",
        r"\b批判\b",
        r"\b不足\b",
        r"\b缺点\b",
    ]

    @classmethod
    def build(cls, task: str) -> Plan:
        """Build a DAG Plan for a complex task.

        Core DAG:
            read_paper
                ├── analyze_method
                │       └── (writer if no verification)
                ├── verify_data (optional)
                │       └── writer
                └── writer (if verify off and analyze off)
            writer -> review_report -> revise_report (review loop)
        """
        text = task.lower()
        needs_verify = any(re.search(p, text, re.IGNORECASE) for p in cls._VERIFY_KEYWORDS)
        needs_report = any(re.search(p, text, re.IGNORECASE) for p in cls._REPORT_KEYWORDS)
        needs_pptx = any(re.search(p, text, re.IGNORECASE) for p in cls._PPTX_KEYWORDS)
        needs_critical = any(re.search(p, text, re.IGNORECASE) for p in cls._CRITICAL_KEYWORDS)

        plan = Plan()

        # Phase 1: read paper
        plan.add("Read paper and extract key facts", task_id="read_paper")

        # Phase 2: method analysis (feeds into report)
        if needs_report or needs_pptx or needs_critical:
            plan.add(
                "Analyze methodology and main claims",
                depends_on=["read_paper"],
                task_id="analyze_method",
            )

        # Phase 3: numerical verification (optional)
        if needs_verify:
            plan.add(
                "Verify numerical claims with code",
                depends_on=["read_paper"],
                task_id="verify_data",
            )

        # Phase 4: writer -> produce report / slides
        writer_deps = []
        if needs_report or needs_pptx or needs_critical:
            writer_deps.append("analyze_method")
        if needs_verify:
            writer_deps.append("verify_data")
        if not writer_deps:
            # No other phases selected; degenerate complex task -> still write an answer artifact
            writer_deps = ["read_paper"]

        if needs_pptx:
            plan.add(
                "Generate academic presentation slides",
                depends_on=writer_deps,
                task_id="generate_pptx",
            )
        if needs_report or needs_critical or not needs_pptx:
            plan.add(
                "Generate structured analysis report",
                depends_on=writer_deps,
                task_id="generate_report",
            )

        # Phase 5: review + revision loop (only for report/pptx/critical outputs)
        if needs_report or needs_pptx or needs_critical:
            review_deps = []
            if plan.get("generate_report"):
                review_deps.append("generate_report")
            if plan.get("generate_pptx"):
                review_deps.append("generate_pptx")

            plan.add(
                "Adversarially review the output for hallucinations and omissions",
                depends_on=review_deps,
                task_id="review_report",
            )
            plan.add(
                "Revise the output based on review findings",
                depends_on=["review_report"],
                task_id="revise_report",
            )

        return plan

    @classmethod
    def build_simple(cls, task: str) -> Plan:
        """Build a minimal plan for simple Q&A tasks."""
        plan = Plan()
        plan.add("Read the paper to locate relevant information", task_id="read_paper")
        plan.add("Answer the user's question", depends_on=["read_paper"], task_id="answer")
        return plan

    @staticmethod
    def has_complex_artifacts(plan: Plan) -> bool:
        """Return True if the plan will produce report/pptx artifacts."""
        return bool(plan.get("generate_report") or plan.get("generate_pptx"))
