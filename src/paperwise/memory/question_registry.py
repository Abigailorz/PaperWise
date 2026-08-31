"""P7 - deterministic mapping from Opportunities to ResearchQuestions."""

from __future__ import annotations

from paperwise.memory.research_question import ResearchQuestion, make_question_id
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


class ResearchQuestionRegistry:
    """Merge equivalent opportunity signals into durable questions."""

    @staticmethod
    def derive(
        opportunities: list[ResearchOpportunity],
        max_questions: int = 5,
    ) -> list[ResearchQuestion]:
        questions: dict[str, ResearchQuestion] = {}
        for opportunity in opportunities:
            if opportunity.status.value in ("dismissed", "expired"):
                continue
            question_text = opportunity.question
            if not question_text:
                question_text = f"What evidence resolves {opportunity.title}: {opportunity.description[:180]}?"
            question_id = make_question_id(question_text)
            question = questions.get(question_id)
            if question is None:
                if len(questions) >= max_questions:
                    continue
                question = ResearchQuestion(
                    question_id=question_id,
                    question=question_text,
                    importance=max(0.5, min(1.0, opportunity.importance)),
                )
                questions[question_id] = question
            question.merge_signal(
                opportunity_id=opportunity.opportunity_id,
                evidence_ref=opportunity.evidence[0].location if opportunity.evidence else "",
            )
        return list(questions.values())
