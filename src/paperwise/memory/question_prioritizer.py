"""P8 - deterministic ResearchQuestion prioritization.

When multiple ResearchQuestions exist, the system must decide which to work
on now.  Scoring is purely deterministic (no LLM):

    score = importance
          x (1 + avg(source opportunity confidence))
          x recency_decay

The top ``max_questions`` are marked ``active`` and drive this round's action
planning; the rest stay ``open``.
"""

from __future__ import annotations

from datetime import datetime

from paperwise.memory.research_question import ResearchQuestion
from paperwise.opportunity.models import ResearchOpportunity


class QuestionPrioritizer:
    """Deterministic, bounded ranking of ResearchQuestions."""

    @staticmethod
    def prioritize(
        questions: list[ResearchQuestion],
        opportunities: list,
        max_questions: int = 2,
    ) -> list[ResearchQuestion]:
        """Rank questions and return the top ``max_questions``.

        Deterministic: same input -> same ranking.  No LLM involvement.
        """
        opp_conf = {o.opportunity_id: o.confidence for o in opportunities}
        now = datetime.now()

        def score(question: ResearchQuestion) -> float:
            confs = [
                opp_conf.get(oid, 0.0)
                for oid in question.source_opportunities
            ]
            opp_signal = sum(confs) / len(confs) if confs else 0.0
            try:
                age_hours = (
                    now - datetime.fromisoformat(question.created_at)
                ).total_seconds() / 3600.0
            except (ValueError, TypeError):
                age_hours = 0.0
            recency = 1.0 / (1.0 + age_hours / 24.0)
            return question.importance * (1.0 + opp_signal) * recency

        ranked = sorted(questions, key=score, reverse=True)
        return ranked[:max_questions]
