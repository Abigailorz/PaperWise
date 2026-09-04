"""Dimensioned fact-quality evaluation.

The old hallucination grader treated "a fact outside the requested golden
scope" and "a fact that contradicts the paper" in the same veto channel.  This
module separates them so an agent is not rewarded for answering less.
"""

from __future__ import annotations

import json
import re
from typing import Any

from paperwise.core.llm_client import LLMClient
from paperwise.evaluation.graders import GradeResult


def _extract_json(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) > 1:
            content = parts[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        content = match.group(0)
    if not content.startswith("{"):
        raise ValueError("judge did not return a JSON object")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("judge did not return a JSON object")
    return value


def _ratio(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


class GroundedFactDetector:
    """LLM evaluator for factual correctness, grounding and answer scope."""

    def __init__(self, llm_client: LLMClient, paper_chars: int = 60_000):
        self.llm = llm_client
        self.paper_chars = paper_chars

    def _golden_digest(self, golden: dict[str, Any] | None) -> str:
        if not golden:
            return "No benchmark golden digest was supplied."
        criteria = golden.get("evaluation_criteria", {})
        digest = {
            "expected_findings": golden.get("expected_findings", {}),
            "required_citations": criteria.get("required_citations", []),
            "must_include_numbers": criteria.get("must_include_numbers", []),
            "must_mention_limitations": criteria.get("must_mention_limitations", []),
        }
        return json.dumps(digest, ensure_ascii=False, indent=2)

    async def evaluate(
        self,
        report: str,
        paper_text: str,
        scenario: dict[str, Any] | None = None,
        golden: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scenario = scenario or {}
        report_excerpt = (report or "")[:16_000]
        prompt = (
            "You are a grounded fact-quality evaluator for an academic research agent.\n\n"
            "Classify problems separately. Never treat a true fact beyond the requested "
            "scope as a factual hallucination.\n\n"
            "REQUESTED SCOPE:\n"
            f"{scenario.get('task', '(not supplied)')}\n\n"
            "GOLDEN DIGEST (the minimum required answer, not an upper bound):\n"
            f"{self._golden_digest(golden)}\n\n"
            "PAPER SOURCE:\n"
            f"{(paper_text or '')[:self.paper_chars]}\n\n"
            "AGENT OUTPUT:\n"
            f"{report_excerpt}\n\n"
            "Definitions:\n"
            "- factual_error: contradicts the paper or invents a value. This is the only veto.\n"
            "- ungrounded: not traceable to the supplied paper source or golden digest.\n"
            "- scope_violation: true in the paper but beyond what the task requested. "
            "This lowers scope compliance only; it must not be called a hallucination.\n"
            "- correct_rejection: for a false-premise question, the agent says the requested "
            "value is not reported and does not supply a number.\n\n"
            "Respond ONLY with a single JSON object:\n"
            '{"factual_accuracy": 0.0, "evidence_grounding": 0.0, '
            '"scope_compliance": 0.0, "unsupported_claims": [{"claim": "...", '
            '"category": "scope_violation|ungrounded|factual_error", "reason": "..."}], '
            '"correct_rejection": false, "factual_veto": false, '
            '"factual_severity": "none|minor|major|critical", "summary": "..."}'
        )
        try:
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            )
            data = _extract_json(resp.content or "")
        except Exception as exc:
            return {
                "factual_accuracy": 0.5,
                "evidence_grounding": 0.5,
                "scope_compliance": 1.0,
                "unsupported_claims": [],
                "unsupported_claim_count": 0,
                "correct_rejection": False,
                "factual_veto": False,
                "factual_severity": "unknown",
                "summary": f"Evaluation error (treated as unknown): {type(exc).__name__}",
            }

        claims = data.get("unsupported_claims", [])
        if not isinstance(claims, list):
            claims = []
        factual_veto = bool(data.get("factual_veto", False))
        severity = str(data.get("factual_severity", "none")).lower()
        if severity in ("major", "critical"):
            factual_veto = True
        return {
            "factual_accuracy": _ratio(data.get("factual_accuracy"), 0.5),
            "evidence_grounding": _ratio(data.get("evidence_grounding"), 0.5),
            "scope_compliance": _ratio(data.get("scope_compliance"), 1.0),
            "unsupported_claims": claims,
            "unsupported_claim_count": len(claims),
            "correct_rejection": bool(data.get("correct_rejection", False)),
            "factual_veto": factual_veto,
            "factual_severity": severity or "none",
            "summary": str(data.get("summary", "")),
        }


class GroundedFactGrader:
    """Score fact quality without letting scope expansion veto the task."""

    def __init__(self, llm_client: LLMClient):
        self.detector = GroundedFactDetector(llm_client)

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        det = await self.detector.evaluate(
            output,
            context.get("paper_text", ""),
            context.get("scenario"),
            context.get("golden"),
        )
        factual = det["factual_accuracy"]
        grounding = det["evidence_grounding"]
        scope = det["scope_compliance"]
        score = 0.70 * factual + 0.15 * grounding + 0.15 * scope
        passed = factual >= 0.8 and not det["factual_veto"]
        errors = []
        if not passed:
            errors.append(
                f"grounded fact veto: accuracy={factual:.2f}, "
                f"severity={det['factual_severity']}"
            )
        return GradeResult(
            passed=passed,
            score=score,
            details=[
                f"factual_accuracy={factual:.2f}",
                f"evidence_grounding={grounding:.2f}",
                f"scope_compliance={scope:.2f}",
                f"unsupported_claims={det['unsupported_claim_count']}",
                f"correct_rejection={str(det['correct_rejection']).lower()}",
            ],
            errors=errors,
            raw=det,
        )
