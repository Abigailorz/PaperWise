from paperwise.memory.outcome_evaluator import (
    OutcomeEvaluator, QuestionOutcome,
)
from paperwise.memory.question_prioritizer import QuestionPrioritizer
from paperwise.memory.question_registry import ResearchQuestionRegistry
from paperwise.memory.research_question import ResearchQuestion
from paperwise.memory.research_state import ResearchState
from paperwise.memory.state_updater import StateEvent, StateEventType
from paperwise.opportunity.action import ActionStatus, ActionType, ResearchAction
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


def _opportunity(opp_id, title="Gap", confidence=0.9,
                 otype=OpportunityType.MISSING_EVIDENCE):
    return ResearchOpportunity(
        type=otype,
        title=title,
        description="desc",
        opportunity_id=opp_id,
        confidence=confidence,
        importance=0.8,
    )


def _question(qid, text, importance=0.8, opp_ids=()):
    return ResearchQuestion(
        question_id=qid,
        question=text,
        importance=importance,
        source_opportunities=list(opp_ids),
    )


def test_prioritizer_is_deterministic_and_bounded():
    questions = [
        _question("rq_low", "low priority q", importance=0.2, opp_ids=["o1"]),
        _question("rq_high", "high priority q", importance=0.9, opp_ids=["o2"]),
        _question("rq_mid", "mid priority q", importance=0.5, opp_ids=["o3"]),
    ]
    opportunities = [
        _opportunity("o1", confidence=0.3),
        _opportunity("o2", confidence=0.9),
        _opportunity("o3", confidence=0.6),
    ]
    ranked = QuestionPrioritizer.prioritize(questions, opportunities, max_questions=2)
    assert [q.question_id for q in ranked] == ["rq_high", "rq_mid"]
    ranked2 = QuestionPrioritizer.prioritize(questions, opportunities, max_questions=2)
    assert [q.question_id for q in ranked2] == [q.question_id for q in ranked]


def test_question_becomes_active_when_actions_start():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q text", opp_ids=["opp_1"])
    state.questions.append(question)
    state.apply(StateEvent(
        StateEventType.QUESTION_STATUS_CHANGED,
        payload={"question_id": "rq_1", "status": "active"},
    ))
    assert state.questions[0].status == "active"


def _action(aid, status):
    action = ResearchAction(
        action_id=aid,
        opportunity_id="opp_1",
        action_type=ActionType.RETRIEVE_EVIDENCE,
        objective="obj",
    )
    action.status = status
    return action


def test_outcome_resolved_when_new_evidence_added():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q", opp_ids=["opp_1"])
    question.evidence_refs = ["ev_old"]
    updated = ResearchQuestion.from_dict(question.to_dict())
    updated.evidence_refs.append("ev_new")
    state.questions.append(updated)
    actions = [_action("a1", ActionStatus.COMPLETED)]
    result = OutcomeEvaluator.evaluate(question, actions, state)
    assert result.outcome == QuestionOutcome.RESOLVED


def test_outcome_unresolved_when_action_fails():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q", opp_ids=["opp_1"])
    state.questions.append(question)
    state.questions[0].evidence_refs = ["ev_old"]
    actions = [_action("a1", ActionStatus.FAILED)]
    result = OutcomeEvaluator.evaluate(state.questions[0], actions, state)
    assert result.outcome == QuestionOutcome.UNRESOLVED


def test_outcome_partially_resolved_without_new_evidence():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q", opp_ids=["opp_1"])
    state.questions.append(question)
    state.questions[0].evidence_refs = ["ev_old"]
    actions = [_action("a1", ActionStatus.COMPLETED)]
    result = OutcomeEvaluator.evaluate(state.questions[0], actions, state)
    assert result.outcome == QuestionOutcome.PARTIALLY_RESOLVED


def test_outcome_contradicted_without_new_evidence():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q", opp_ids=["opp_1"])
    state.questions.append(question)
    state.questions[0].evidence_refs = ["ev_old"]
    state.opportunities.append(_opportunity(
        "opp_1", otype=OpportunityType.CONTRADICTION))
    actions = [_action("a1", ActionStatus.COMPLETED)]
    result = OutcomeEvaluator.evaluate(state.questions[0], actions, state)
    assert result.outcome == QuestionOutcome.CONTRADICTED


def test_question_evaluated_event_updates_status_and_count():
    state = ResearchState(state_id="s", user_id="u")
    question = _question("rq_1", "q")
    state.questions.append(question)
    state.apply(StateEvent(
        StateEventType.QUESTION_EVALUATED,
        payload={
            "question_id": "rq_1",
            "status": "answered",
            "outcome": "resolved",
        },
    ))
    assert state.questions[0].status == "answered"
    assert state.questions[0].outcome == "resolved"
    assert state.questions[0].evaluation_count == 1
