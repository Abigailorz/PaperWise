"""P6 Phase A - StateUpdater: event-driven ResearchState mutations.

All state changes flow through typed events rather than direct mutation.
This makes state transitions auditable, replayable, and testable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paperwise.memory.research_state import ResearchState
    from paperwise.opportunity.models import ResearchOpportunity


class StateEventType(str, Enum):
    """Controlled event vocabulary for ResearchState transitions."""

    EVIDENCE_FOUND = "evidence_found"
    CLAIM_VERIFIED = "claim_verified"
    CLAIM_REJECTED = "claim_rejected"
    GAP_DETECTED = "gap_detected"
    OPPORTUNITY_CREATED = "opportunity_created"
    RESEARCH_QUESTION_CREATED = "research_question_created"
    ACTION_PLANNED = "action_planned"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    HYPOTHESIS_CREATED = "hypothesis_created"


@dataclass
class Hypothesis:
    """A testable research hypothesis."""

    hypothesis_id: str = field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:8]}")
    statement: str = ""
    rationale: str = ""
    confidence: float = 0.5
    source_opportunity: str = ""
    status: str = "proposed"  # proposed | testing | supported | refuted
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class StateEvent:
    """A single typed state mutation."""

    event_type: StateEventType
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "StateEvent":
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        kwargs["event_type"] = StateEventType(kwargs.get("event_type", "evidence_found"))
        return cls(**kwargs)


class StateUpdater:
    """Apply StateEvents to ResearchState. Only path for state mutation."""

    @staticmethod
    def apply(state: ResearchState, event: StateEvent) -> ResearchState:
        """Apply a typed event. Dispatches to _on_* handler."""
        event_name = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        handler = getattr(StateUpdater, f"_on_{event_name}", None)
        if handler is None:
            raise ValueError(f"Unknown state event: {event.event_type}")
        handler(state, event)
        state.mark_updated()
        return state

    @staticmethod
    def _on_evidence_found(state: ResearchState, event: StateEvent) -> None:
        p = event.payload
        state.add_finding_from_node(
            node_id=p.get("node_id", ""),
            claim=p.get("claim", ""),
            evidence=p.get("evidence", ""),
            confidence=min(1.0, p.get("confidence", 0.8)),
        )

    @staticmethod
    def _on_claim_verified(state: ResearchState, event: StateEvent) -> None:
        node_id = event.payload.get("node_id", "")
        for finding in state.findings:
            if finding.node_id == node_id:
                finding.confidence = min(1.0, finding.confidence + 0.1)

    @staticmethod
    def _on_claim_rejected(state: ResearchState, event: StateEvent) -> None:
        node_id = event.payload.get("node_id", "")
        reason = event.payload.get("reason", "claim rejected")
        for finding in state.findings:
            if finding.node_id == node_id:
                finding.confidence = max(0.0, finding.confidence - 0.3)
        state.add_gap(f"Claim rejected: {reason}", node_id=node_id, urgency="medium")

    @staticmethod
    def _on_gap_detected(state: ResearchState, event: StateEvent) -> None:
        p = event.payload
        state.add_gap(
            description=p.get("description", ""),
            node_id=p.get("node_id", ""),
            urgency=p.get("urgency", "medium"),
            suggested_action=p.get("suggested_action", ""),
        )

    @staticmethod
    def _on_opportunity_created(state: ResearchState, event: StateEvent) -> None:
        opp_data = event.payload.get("opportunity")
        from paperwise.opportunity.models import ResearchOpportunity
        if isinstance(opp_data, dict):
            state.add_opportunity(ResearchOpportunity.from_dict(opp_data))
        elif isinstance(opp_data, ResearchOpportunity):
            state.add_opportunity(opp_data)

    @staticmethod
    def _on_research_question_created(state: ResearchState, event: StateEvent) -> None:
        from paperwise.memory.research_question import ResearchQuestion
        question_data = event.payload.get("question")
        if not isinstance(question_data, (dict, ResearchQuestion)):
            return
        question = ResearchQuestion.from_dict(question_data) if isinstance(question_data, dict) else question_data
        if question.question_id not in {item.question_id for item in state.questions}:
            state.questions.append(question)

    @staticmethod
    def _on_action_planned(state: ResearchState, event: StateEvent) -> None:
        from paperwise.opportunity.action import ResearchAction
        for item in event.payload.get("actions", []):
            action = ResearchAction.from_dict(item) if isinstance(item, dict) else item
            if isinstance(action, ResearchAction) and action.action_id not in {
                existing.action_id for existing in state.pending_actions
            }:
                state.pending_actions.append(action)

    @staticmethod
    def _on_action_started(state: ResearchState, event: StateEvent) -> None:
        from paperwise.opportunity.models import OpportunityStatus
        from paperwise.opportunity.action import ActionStatus
        action_id = event.payload.get("action_id", "")
        opp_id = event.payload.get("opportunity_id", "")
        for action in state.pending_actions:
            if action.action_id == action_id:
                action.status = ActionStatus.RUNNING
                opp_id = opp_id or action.opportunity_id
        for opp in state.opportunities:
            if opp.opportunity_id == opp_id:
                opp.status = OpportunityStatus.ACTING

    @staticmethod
    def _on_action_completed(state: ResearchState, event: StateEvent) -> None:
        from paperwise.opportunity.models import OpportunityStatus
        from paperwise.opportunity.action import ActionStatus
        action_id = event.payload.get("action_id", "")
        opp_id = event.payload.get("opportunity_id", "")
        success = event.payload.get("success", False)
        selected = None
        for action in state.pending_actions:
            if action.action_id == action_id:
                selected = action
                action.status = ActionStatus.COMPLETED if success else ActionStatus.FAILED
                opp_id = opp_id or action.opportunity_id
                break
        if selected is not None:
            state.pending_actions = [item for item in state.pending_actions if item.action_id != action_id]
            state.completed_actions.append(selected)
        for opp in state.opportunities:
            if opp.opportunity_id == opp_id:
                if success:
                    opp.status = OpportunityStatus.ACTED
                    opp.confidence = min(1.0, opp.confidence + 0.1)
                else:
                    opp.status = OpportunityStatus.PENDING
                    opp.confidence = max(0.0, opp.confidence - 0.15)

    @staticmethod
    def _on_hypothesis_created(state: ResearchState, event: StateEvent) -> None:
        p = event.payload
        hyp = Hypothesis(
            statement=p.get("statement", ""),
            rationale=p.get("rationale", ""),
            confidence=p.get("confidence", 0.5),
            source_opportunity=p.get("source_opportunity", ""),
        )
        if not hasattr(state, "hypotheses"):
            state.hypotheses = []
        state.hypotheses.append(hyp)
