from pathlib import Path

from paperwise.memory.question_registry import ResearchQuestionRegistry
from paperwise.memory.research_question import make_question_id
from paperwise.memory.research_state import ResearchState
from paperwise.memory.state_updater import StateEvent, StateEventType
from paperwise.opportunity.action import ActionStatus, ActionType, ResearchAction
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity
from paperwise.generators.narrative import ResearchNarrative


def _opportunity(title: str = "Missing benchmark") -> ResearchOpportunity:
    return ResearchOpportunity(
        type=OpportunityType.MISSING_EVIDENCE,
        title=title,
        description="No quantitative evidence is available.",
        confidence=0.9,
        importance=0.8,
        question="What quantitative evidence is missing?",
    )


def test_equivalent_opportunities_merge_to_one_question():
    questions = ResearchQuestionRegistry.derive([_opportunity(), _opportunity()])
    assert len(questions) == 1
    assert len(questions[0].source_opportunities) == 2


def test_question_id_is_stable():
    assert make_question_id(" What quantitative evidence is missing? ") == make_question_id(
        "what quantitative evidence is missing?"
    )


def test_question_and_action_events_are_event_driven():
    state = ResearchState(state_id="state", user_id="default")
    question = ResearchQuestionRegistry.derive([_opportunity()])[0]
    state.apply(StateEvent(
        StateEventType.RESEARCH_QUESTION_CREATED,
        payload={"question": question.to_dict()},
    ))
    action = ResearchAction(
        opportunity_id="opp_1",
        action_type=ActionType.RETRIEVE_EVIDENCE,
        objective="Find missing benchmark",
    )
    state.apply(StateEvent(StateEventType.ACTION_PLANNED, payload={"actions": [action.to_dict()]}))
    assert state.get_open_questions()[0].question_id == question.question_id
    assert state.get_pending_actions()[0].action_id == action.action_id


def test_action_lifecycle_moves_to_completed():
    state = ResearchState(state_id="state", user_id="default")
    action = ResearchAction(
        opportunity_id="opp_1",
        action_type=ActionType.RETRIEVE_EVIDENCE,
        objective="Find evidence",
    )
    state.apply(StateEvent(StateEventType.ACTION_PLANNED, payload={"actions": [action.to_dict()]}))
    state.apply(StateEvent(StateEventType.ACTION_STARTED, payload={"action_id": action.action_id}))
    assert state.get_pending_actions() == []
    assert state.pending_actions[0].status == ActionStatus.RUNNING
    state.apply(StateEvent(StateEventType.ACTION_COMPLETED, payload={
        "action_id": action.action_id,
        "opportunity_id": "opp_1",
        "success": True,
    }))
    assert state.pending_actions == []
    assert state.completed_actions[0].status == ActionStatus.COMPLETED


def test_state_serialization_roundtrip(tmp_path: Path):
    state = ResearchState(state_id="state", user_id="default")
    question = ResearchQuestionRegistry.derive([_opportunity()])[0]
    state.questions.append(question)
    state.pending_actions.append(ResearchAction(
        opportunity_id="opp_1",
        action_type=ActionType.RETRIEVE_EVIDENCE,
        objective="Find evidence",
    ))
    restored = ResearchState.from_dict(state.to_dict())
    assert restored.questions[0].question_id == question.question_id
    assert restored.pending_actions[0].action_id == state.pending_actions[0].action_id


def test_narrative_contains_questions_and_actions():
    state = ResearchState(state_id="state", user_id="default")
    question = ResearchQuestionRegistry.derive([_opportunity()])[0]
    state.questions.append(question)
    state.pending_actions.append(ResearchAction(
        opportunity_id="opp_1",
        action_type=ActionType.RETRIEVE_EVIDENCE,
        objective="Find missing benchmark",
    ))
    narrative = ResearchNarrative.build(state)
    assert narrative.questions_summary[0]["question"] == question.question
    assert narrative.actions_summary[0]["action_type"] == "retrieve_evidence"
    assert "Research Questions" in narrative.to_prompt_context()
