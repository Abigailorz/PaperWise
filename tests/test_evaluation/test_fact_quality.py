import json

import pytest

from paperwise.evaluation import GroundedFactGrader


class _FakeJudge:
    def __init__(self, payload: dict):
        self.payload = payload

    async def chat(self, messages, tools=None, temperature=0.1, max_tokens=1000):
        class _Response:
            content = json.dumps(self.payload, ensure_ascii=False)
        return _Response()


@pytest.mark.asyncio
async def test_scope_violation_is_scored_but_does_not_veto():
    judge = _FakeJudge({
        "factual_accuracy": 1.0,
        "evidence_grounding": 1.0,
        "scope_compliance": 0.75,
        "unsupported_claims": [{
            "claim": "per-scene IoU table",
            "category": "scope_violation",
            "reason": "true in paper but beyond requested headline",
        }],
        "correct_rejection": False,
        "factual_veto": False,
        "factual_severity": "none",
        "summary": "accurate but over-scoped",
    })
    grade = await GroundedFactGrader(judge).grade(
        "199x faster, 1440x1080, 84.3%, plus per-scene table",
        {"paper_text": "paper", "scenario": {"task": "give headline numbers"}},
    )

    assert grade.passed
    assert grade.score > 0.8
    assert grade.raw["unsupported_claim_count"] == 1
    assert not grade.raw["factual_veto"]


@pytest.mark.asyncio
async def test_fabricated_number_vetoes_even_with_good_scope():
    judge = _FakeJudge({
        "factual_accuracy": 0.4,
        "evidence_grounding": 0.2,
        "scope_compliance": 0.95,
        "unsupported_claims": [{
            "claim": "97% accuracy",
            "category": "factual_error",
            "reason": "paper reports 84.3%",
        }],
        "correct_rejection": False,
        "factual_veto": True,
        "factual_severity": "critical",
        "summary": "fabricated value",
    })
    grade = await GroundedFactGrader(judge).grade(
        "97% accuracy",
        {"paper_text": "paper", "scenario": {"task": "give headline numbers"}},
    )

    assert not grade.passed
    assert grade.raw["factual_veto"]
    assert "grounded fact veto" in grade.errors[0]


@pytest.mark.asyncio
async def test_correct_rejection_is_not_penalized_as_fabrication():
    judge = _FakeJudge({
        "factual_accuracy": 1.0,
        "evidence_grounding": 1.0,
        "scope_compliance": 0.7,
        "unsupported_claims": [],
        "correct_rejection": True,
        "factual_veto": False,
        "factual_severity": "none",
        "summary": "correct false-premise rejection",
    })
    grade = await GroundedFactGrader(judge).grade(
        "not reported",
        {
            "paper_text": "paper",
            "scenario": {"task": "What BLEU score?", "forbid_fabrication": True},
        },
    )

    assert grade.passed
    assert grade.raw["correct_rejection"]
