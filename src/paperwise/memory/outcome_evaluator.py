"""P8 - deterministic question outcome evaluation.

After a round of actions completes, each targeted ResearchQuestion is judged
against what actually changed in ResearchState.  Five outcomes:

    resolved | partially_resolved | unresolved | contradicted | new_question

Evaluation is deterministic (same state delta -> same outcome); the LLM is
never asked to grade outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from paperwise.memory.research_question import ResearchQuestion
from paperwise.memory.research_state import ResearchState
from paperwise.opportunity.action import ActionStatus, ResearchAction


class QuestionOutcome(str, Enum):
    """Controlled outcome vocabulary for a research question round."""

    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    UNRESOLVED = "unresolved"
    CONTRADICTED = "contradicted"
    NEW_QUESTION = "new_question"


@dataclass
class OutcomeResult:
    """The result of evaluating one question after an action round."""

    question_id: str
    outcome: QuestionOutcome
    rationale: str
    evidence_before: int
    evidence_after: int


class OutcomeEvaluator:
    """Judge a question's progress from the state delta its actions produced."""

    @staticmethod
    def evaluate(
        question: ResearchQuestion,
        actions: list[ResearchAction],
        state: ResearchState,
    ) -> OutcomeResult:
        """Evaluate one question after its actions ran.

        Deterministic rules, checked in order:

        1. Any action failed                     -> unresolved
        2. New evidence refs appeared            -> resolved
        3. Actions succeeded, evidence unchanged -> partially_resolved
        4. A CONTRADICTION opportunity is linked -> contradicted (when no new
           evidence was gained) or new_question (when the contradiction is
           backed by new evidence, which raises a follow-up question to settle)
        """
        evidence_before = len(question.evidence_refs)
        evidence_after = len(
            state.questions[
                next(
                    i for i, q in enumerate(state.questions)
                    if q.question_id == question.question_id
                )
            ].evidence_refs
        )
        evidence_delta = evidence_after - evidence_before
        failed = [a for a in actions if a.status == ActionStatus.FAILED]
        succeeded = [a for a in actions if a.status == ActionStatus.COMPLETED]

        if failed:
            return OutcomeResult(
                question_id=question.question_id,
                outcome=QuestionOutcome.UNRESOLVED,
                rationale=f"{len(failed)} of {len(actions)} actions failed.",
                evidence_before=evidence_before,
                evidence_after=evidence_after,
            )

        contradicted = any(
            opp.opportunity_id in question.source_opportunities
            and opp.type.value == "contradiction"
            for opp in state.opportunities
        )
        if contradicted and evidence_delta > 0:
            return OutcomeResult(
                question_id=question.question_id,
                outcome=QuestionOutcome.NEW_QUESTION,
                rationale="Linked contradiction backed by new evidence; spawning a follow-up question.",
                evidence_before=evidence_before,
                evidence_after=evidence_after,
            )
        if contradicted:
            return OutcomeResult(
                question_id=question.question_id,
                outcome=QuestionOutcome.CONTRADICTED,
                rationale="Linked contradiction opportunity with no new evidence.",
                evidence_before=evidence_before,
                evidence_after=evidence_after,
            )

        if evidence_delta > 0:
            return OutcomeResult(
                question_id=question.question_id,
                outcome=QuestionOutcome.RESOLVED,
                rationale=f"Action round added {evidence_delta} evidence ref(s).",
                evidence_before=evidence_before,
                evidence_after=evidence_after,
            )

        return OutcomeResult(
            question_id=question.question_id,
            outcome=QuestionOutcome.PARTIALLY_RESOLVED,
            rationale=f"{len(succeeded)} action(s) succeeded but evidence set unchanged.",
            evidence_before=evidence_before,
            evidence_after=evidence_after,
        )

    @staticmethod
    def outcome_to_status(outcome: QuestionOutcome) -> str:
        """Map an outcome to the question's next lifecycle status."""
        return {
            QuestionOutcome.RESOLVED: "answered",
            QuestionOutcome.PARTIALLY_RESOLVED: "active",
            QuestionOutcome.UNRESOLVED: "open",
            QuestionOutcome.CONTRADICTED: "active",
            QuestionOutcome.NEW_QUESTION: "active",
        }[outcome]
