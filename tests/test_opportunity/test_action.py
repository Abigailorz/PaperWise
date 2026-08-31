"""P6 Phase A tests: ResearchAction, ActionPlanner.plan_actions, StateUpdater, GraphQuery."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from paperwise.memory.research_state import ResearchState
from paperwise.memory.state_updater import StateEvent, StateEventType, StateUpdater
from paperwise.opportunity.action import (
    ActionRisk,
    ActionStatus,
    ActionType,
    ResearchAction,
)
from paperwise.opportunity.action_planner import ActionPlanner
from paperwise.opportunity.models import OpportunityStatus, OpportunityType, ResearchOpportunity


def _make_opp(
    opp_type: OpportunityType = OpportunityType.KNOWLEDGE_GAP,
    status: OpportunityStatus = OpportunityStatus.PENDING,
    confidence: float = 0.8,
    importance: float = 0.7,
) -> ResearchOpportunity:
    return ResearchOpportunity(
        type=opp_type,
        title="Test opportunity",
        description="A test opportunity for unit tests",
        confidence=confidence,
        importance=importance,
        status=status,
    )


class TestResearchAction:
    def test_create_basic(self):
        action = ResearchAction(action_type=ActionType.RETRIEVE_EVIDENCE)
        assert action.action_id.startswith("act_")
        assert action.status == ActionStatus.PENDING
        assert action.risk_level == ActionRisk.LOW
        assert action.is_auto_executable

    def test_risk_auto_assignment(self):
        action = ResearchAction(action_type=ActionType.GENERATE_HYPOTHESIS)
        assert action.risk_level == ActionRisk.MEDIUM
        assert action.requires_user_approval
        assert not action.is_auto_executable

    def test_high_risk_requires_approval(self):
        action = ResearchAction(action_type=ActionType.ASK_USER)
        assert action.risk_level == ActionRisk.HIGH
        assert action.requires_user_approval

    def test_serialization_roundtrip(self):
        action = ResearchAction(
            action_type=ActionType.COMPARE_METHODS,
            objective="Compare method A vs B",
            confidence=0.8,
        )
        data = action.to_dict()
        restored = ResearchAction.from_dict(data)
        assert restored.action_type == ActionType.COMPARE_METHODS
        assert restored.objective == "Compare method A vs B"
        assert restored.confidence == 0.8


class TestActionPlannerPlanActions:
    def test_knowledge_gap_mapping(self):
        planner = ActionPlanner()
        opp = _make_opp(OpportunityType.KNOWLEDGE_GAP)
        state = ResearchState(state_id="s1", user_id="default")
        actions = planner.plan_actions([opp], state)
        types = [a.action_type for a in actions]
        assert ActionType.RETRIEVE_EVIDENCE in types
        assert ActionType.ANALYZE_GAP in types

    def test_missing_evidence_mapping(self):
        planner = ActionPlanner()
        opp = _make_opp(OpportunityType.MISSING_EVIDENCE)
        state = ResearchState(state_id="s1", user_id="default")
        actions = planner.plan_actions([opp], state)
        types = [a.action_type for a in actions]
        assert ActionType.RETRIEVE_EVIDENCE in types
        assert ActionType.VERIFY_CLAIM in types

    def test_budget_enforced(self):
        planner = ActionPlanner()
        opps = [_make_opp() for _ in range(5)]
        state = ResearchState(state_id="s1", user_id="default")
        actions = planner.plan_actions(opps, state, max_actions=3)
        assert len(actions) <= 3

    def test_deterministic(self):
        planner = ActionPlanner()
        opp = _make_opp()
        state = ResearchState(state_id="s1", user_id="default")
        a1 = planner.plan_actions([opp], state)
        a2 = planner.plan_actions([opp], state)
        assert [a.action_type for a in a1] == [a.action_type for a in a2]

    def test_non_pending_skipped(self):
        planner = ActionPlanner()
        opp = _make_opp(status=OpportunityStatus.ACTED)
        state = ResearchState(state_id="s1", user_id="default")
        actions = planner.plan_actions([opp], state)
        assert len(actions) == 0

    def test_actions_to_dag(self):
        planner = ActionPlanner()
        opp = _make_opp(OpportunityType.MISSING_EVIDENCE)
        state = ResearchState(state_id="s1", user_id="default")
        actions = planner.plan_actions([opp], state)
        plan = planner.actions_to_dag(actions)
        task_ids = [t.id for t in plan.tasks]
        assert "read_paper" in task_ids
        assert all(tid in {"read_paper", "expand_evidence", "verify_data"} for tid in task_ids)


class TestStateUpdater:
    def _state(self) -> ResearchState:
        return ResearchState(state_id="s1", user_id="default")

    def test_evidence_found(self):
        state = self._state()
        event = StateEvent(
            event_type=StateEventType.EVIDENCE_FOUND,
            payload={"node_id": "n1", "claim": "test claim", "evidence": "src", "confidence": 0.9},
        )
        StateUpdater.apply(state, event)
        assert len(state.findings) == 1
        assert state.findings[0].confidence == 0.9

    def test_claim_verified(self):
        state = self._state()
        state.add_finding_from_node("n1", "claim", confidence=0.6)
        event = StateEvent(
            event_type=StateEventType.CLAIM_VERIFIED,
            payload={"node_id": "n1"},
        )
        StateUpdater.apply(state, event)
        assert state.findings[0].confidence == pytest.approx(0.7)

    def test_claim_rejected_creates_gap(self):
        state = self._state()
        state.add_finding_from_node("n1", "claim", confidence=0.8)
        event = StateEvent(
            event_type=StateEventType.CLAIM_REJECTED,
            payload={"node_id": "n1", "reason": "unsupported"},
        )
        StateUpdater.apply(state, event)
        assert len(state.gaps) == 1
        assert state.findings[0].confidence < 0.8

    def test_opportunity_created(self):
        state = self._state()
        opp = _make_opp()
        event = StateEvent(
            event_type=StateEventType.OPPORTUNITY_CREATED,
            payload={"opportunity": opp.to_dict()},
        )
        StateUpdater.apply(state, event)
        assert len(state.opportunities) == 1

    def test_action_lifecycle(self):
        state = self._state()
        opp = _make_opp()
        state.add_opportunity(opp)
        start = StateEvent(
            event_type=StateEventType.ACTION_STARTED,
            payload={"opportunity_id": opp.opportunity_id},
        )
        StateUpdater.apply(state, start)
        assert opp.status == OpportunityStatus.ACTING
        done = StateEvent(
            event_type=StateEventType.ACTION_COMPLETED,
            payload={"opportunity_id": opp.opportunity_id, "success": True},
        )
        StateUpdater.apply(state, done)
        assert opp.status == OpportunityStatus.ACTED

    def test_hypothesis_created(self):
        state = self._state()
        event = StateEvent(
            event_type=StateEventType.HYPOTHESIS_CREATED,
            payload={"statement": "A+B improves mIoU"},
        )
        StateUpdater.apply(state, event)
        assert len(state.hypotheses) == 1

    def test_unknown_event_raises(self):
        state = self._state()
        event = StateEvent(event_type=StateEventType.EVIDENCE_FOUND)
        event.event_type = "bogus"
        with pytest.raises(ValueError):
            StateUpdater.apply(state, event)


class TestResearchStateExtensions:
    def test_expire_stale(self):
        state = ResearchState(state_id="s1", user_id="default")
        opp = _make_opp()
        opp.created_at = (datetime.now() - timedelta(hours=100)).isoformat()
        state.add_opportunity(opp)
        expired = state.expire_stale_opportunities(ttl_hours=72)
        assert len(expired) == 1
        assert opp.status == OpportunityStatus.EXPIRED

    def test_fresh_not_expired(self):
        state = ResearchState(state_id="s1", user_id="default")
        opp = _make_opp()
        state.add_opportunity(opp)
        expired = state.expire_stale_opportunities(ttl_hours=72)
        assert len(expired) == 0
        assert opp.status == OpportunityStatus.PENDING

    def test_get_pending_actions(self):
        state = ResearchState(state_id="s1", user_id="default")
        a1 = ResearchAction(action_type=ActionType.RETRIEVE_EVIDENCE)
        a2 = ResearchAction(action_type=ActionType.VERIFY_CLAIM, status=ActionStatus.RUNNING)
        state.pending_actions = [a1, a2]
        pending = state.get_pending_actions()
        assert len(pending) == 1
        assert pending[0].action_id == a1.action_id
