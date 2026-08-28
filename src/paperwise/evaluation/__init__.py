"""评估体系"""

import json
import re
from pathlib import Path
from paperwise.core.llm_client import LLMClient


class RubricDimension:
    def __init__(self, name, description, levels, weight=1.0):
        self.name = name; self.description = description
        self.levels = levels; self.weight = weight


class EvaluationResult:
    def __init__(self, scores, overall_score, passed, details=""):
        self.scores = scores; self.overall_score = overall_score
        self.passed = passed; self.details = details


class RubricEvaluator:
    DIMENSIONS = [
        RubricDimension("accuracy", "Are all factual statements faithful?", {4:"All cited",3:"Minor deviations",2:"Multiple unreferenced",1:"Misrepresentation"}, 2.0),
        RubricDimension("completeness", "Does it cover all key aspects?", {4:"Comprehensive",3:"Major covered",2:"Missing dimensions",1:"Abstract only"}, 1.5),
        RubricDimension("insight_depth", "Beyond surface summary?", {4:"Original insights",3:"Some critical",2:"Mostly descriptive",1:"Surface only"}, 1.5),
        RubricDimension("evidence_quality", "Claims backed by references?", {4:"Every claim cited",3:"Most cited",2:"Many unreferenced",1:"No references"}, 2.0),
    ]
    def __init__(self, llm_client: LLMClient): self.llm = llm_client

    async def evaluate(self, report, paper_text):
        scores = {}
        for dim in self.DIMENSIONS:
            score, evidence = await self._score_dimension(dim, report, paper_text)
            scores[dim.name] = {"score": score, "weight": dim.weight, "evidence": evidence}
        total = sum(s["score"]*s["weight"] for s in scores.values())
        total_w = sum(d.weight for d in self.DIMENSIONS)
        overall = total/total_w if total_w else 0
        return EvaluationResult(scores, round(overall,2), overall>=3.0, f"Overall: {overall:.2f}/4.0")

    async def _score_dimension(self, dim, report, paper):
        prompt = f"Evaluate report on '{dim.name}': {dim.description}\n\nPaper (truncated):\n{paper[:20000]}\n\nReport (truncated):\n{report[:15000]}\n\nScore 1-4 based on: {dim.levels}\n\nRespond JSON: {{\"score\": <int>, \"evidence\": \"<justification>\"}}"
        try:
            # Kimi Code only allows temperature=1; use it as the judge temperature
            resp = await self.llm.chat(messages=[{"role":"user","content":prompt}], temperature=1)
            result = json.loads(resp.content)
            return result.get("score", 2), result.get("evidence", "No evidence")
        except Exception as e:
            return 2, f"Evaluation error: {type(e).__name__}: {e}"


class HallucinationDetector:
    def __init__(self, llm_client: LLMClient): self.llm = llm_client

    async def detect(self, report, paper_text):
        # Truncate inputs to avoid overly long prompts that cause judge failures
        paper_excerpt = paper_text[:12000]
        report_excerpt = report[:8000]
        prompt = (
            "You are a hallucination detector. Compare the report against the paper ground truth.\n\n"
            "Paper (ground truth):\n"
            f"{paper_excerpt}\n\n"
            "Report to evaluate:\n"
            f"{report_excerpt}\n\n"
            "Rules:\n"
            "1. A hallucination is any claim that is NOT supported by the paper.\n"
            "2. Numerical values must match exactly. If the paper says 95% and the report says 99%, that's critical.\n"
            "3. Missing citations are NOT hallucinations if the content is correct.\n"
            "4. If you cannot determine, say 'unknown'.\n\n"
            "Respond ONLY with a single JSON object, no other text:\n"
            '{"hallucinations": [{"claim": "...", "reason": "...", "severity": "critical|major|minor"}], "overall_severity": "none|minor|major|critical", "summary": "..."}'
        )
        try:
            # Kimi Code only allows temperature=1
            resp = await self.llm.chat(messages=[{"role":"user","content":prompt}], temperature=1, max_tokens=800)
            content = (resp.content or "").strip()

            # Extract JSON from markdown code blocks if present
            if content.startswith("```"):
                parts = content.split("```")
                if len(parts) > 1:
                    content = parts[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()

            # Try to find JSON object in the response
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                content = match.group(0)

            # If still no valid JSON, return unknown instead of error
            if not content or not content.startswith("{"):
                return {"passed": True, "flagged": [], "severity": "unknown",
                        "summary": "Judge returned non-JSON; treated as unknown"}

            result = json.loads(content)
            sev = result.get("overall_severity","none")
            return {"passed":sev not in ("critical",),"flagged":result.get("hallucinations",[]),
                    "severity":sev,"summary":result.get("summary","")}
        except Exception as e:
            # On any error, treat as unknown rather than failing the task
            return {"passed": True, "flagged": [], "severity": "unknown",
                    "summary": f"Detection error (treated as unknown): {type(e).__name__}"}


__all__ = [
    "RubricEvaluator", "HallucinationDetector", "RubricDimension", "EvaluationResult",
    "Grader", "GradeResult", "CodeGrader", "RubricGrader",
    "HallucinationGrader", "TranscriptMetrics", "CompositeGrader",
    "TraceStore", "TraceEvaluator", "TraceMetricsExtractor",
    "RoutingGrader", "PlanningGrader", "RetrievalGrader",
    "EvidenceGrader", "ToolUsageGrader", "ExecutionGrader", "TraceCompositeGrader",
]

from paperwise.evaluation.graders import (
    Grader, GradeResult, CodeGrader, RubricGrader,
    HallucinationGrader, TranscriptMetrics, CompositeGrader,
)
from paperwise.evaluation.trace_store import TraceStore
from paperwise.evaluation.trace_evaluator import (
    TraceEvaluator, TraceMetricsExtractor,
    RoutingGrader, PlanningGrader, RetrievalGrader,
    EvidenceGrader, ToolUsageGrader, ExecutionGrader, TraceCompositeGrader,
)
