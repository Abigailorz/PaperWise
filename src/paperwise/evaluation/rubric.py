"""Rubric-based evaluation using an LLM-as-judge."""

from __future__ import annotations

import json
from pathlib import Path

from paperwise.core.llm_client import LLMClient


class RubricDimension:
    def __init__(self, name, description, levels, weight=1.0):
        self.name = name
        self.description = description
        self.levels = levels
        self.weight = weight


class EvaluationResult:
    def __init__(self, scores, overall_score, passed, details=""):
        self.scores = scores
        self.overall_score = overall_score
        self.passed = passed
        self.details = details


class RubricEvaluator:
    DIMENSIONS = [
        RubricDimension("accuracy", "Are all factual statements faithful?", {4: "All cited", 3: "Minor deviations", 2: "Multiple unreferenced", 1: "Misrepresentation"}, 2.0),
        RubricDimension("completeness", "Does it cover all key aspects?", {4: "Comprehensive", 3: "Major covered", 2: "Missing dimensions", 1: "Abstract only"}, 1.5),
        RubricDimension("insight_depth", "Beyond surface summary?", {4: "Original insights", 3: "Some critical", 2: "Mostly descriptive", 1: "Surface only"}, 1.5),
        RubricDimension("evidence_quality", "Claims backed by references?", {4: "Every claim cited", 3: "Most cited", 2: "Many unreferenced", 1: "No references"}, 2.0),
    ]

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def evaluate(self, report, paper_text):
        scores = {}
        for dim in self.DIMENSIONS:
            score, evidence = await self._score_dimension(dim, report, paper_text)
            scores[dim.name] = {"score": score, "weight": dim.weight, "evidence": evidence}
        total = sum(s["score"] * s["weight"] for s in scores.values())
        total_w = sum(d.weight for d in self.DIMENSIONS)
        overall = total / total_w if total_w else 0
        return EvaluationResult(scores, round(overall, 2), overall >= 3.0, f"Overall: {overall:.2f}/4.0")

    async def _score_dimension(self, dim, report, paper):
        prompt = (
            f"Evaluate report on '{dim.name}': {dim.description}\n\n"
            f"Paper (truncated):\n{paper[:20000]}\n\n"
            f"Report (truncated):\n{report[:15000]}\n\n"
            f"Score 1-4 based on: {dim.levels}\n\n"
            'Respond JSON: {"score": <int>, "evidence": "<justification>"}'
        )
        try:
            # Kimi Code only allows temperature=1; use it as the judge temperature
            resp = await self.llm.chat(messages=[{"role": "user", "content": prompt}], temperature=1)
            result = json.loads(resp.content)
            return result.get("score", 2), result.get("evidence", "No evidence")
        except Exception as e:
            return 2, f"Evaluation error: {type(e).__name__}: {e}"
