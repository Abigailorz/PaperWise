"""P6 Phase A - ResearchAction domain object.

An Action is a typed, risk-classified, auditable unit of research work.
Action types are fixed (8 types); the LLM can only parameterize the
objective and scope, never create new types or bypass constraints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    """Fixed action types - controlled vocabulary, not LLM-generated."""

    RETRIEVE_EVIDENCE = "retrieve_evidence"
    VERIFY_CLAIM = "verify_claim"
    COMPARE_METHODS = "compare_methods"
    ANALYZE_GAP = "analyze_gap"
    SEARCH_RELATED_WORK = "search_related_work"
    GENERATE_HYPOTHESIS = "generate_hypothesis"
    DESIGN_EXPERIMENT = "design_experiment"
    ASK_USER = "ask_user"


class ActionRisk(str, Enum):
    """Risk classification determines approval policy."""

    LOW = "low"        # automatic execution
    MEDIUM = "medium"  # configurable approval
    HIGH = "high"      # mandatory user confirmation


class ActionStatus(str, Enum):
    """Lifecycle: pending -> approved -> running -> completed/failed/rejected."""

    PENDING = "pending"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


#: Deterministic risk level per action type.
ACTION_RISK_LEVELS: dict[ActionType, ActionRisk] = {
    ActionType.RETRIEVE_EVIDENCE: ActionRisk.LOW,
    ActionType.VERIFY_CLAIM: ActionRisk.LOW,
    ActionType.COMPARE_METHODS: ActionRisk.LOW,
    ActionType.ANALYZE_GAP: ActionRisk.LOW,
    ActionType.SEARCH_RELATED_WORK: ActionRisk.LOW,
    ActionType.GENERATE_HYPOTHESIS: ActionRisk.MEDIUM,
    ActionType.DESIGN_EXPERIMENT: ActionRisk.MEDIUM,
    ActionType.ASK_USER: ActionRisk.HIGH,
}


@dataclass
class ResearchAction:
    """A typed, risk-classified unit of research work derived from an Opportunity."""

    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    opportunity_id: str = ""
    action_type: ActionType = ActionType.RETRIEVE_EVIDENCE
    objective: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    input_refs: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    priority: float = 0.5
    confidence: float = 0.0
    risk_level: ActionRisk = ActionRisk.LOW
    status: ActionStatus = ActionStatus.PENDING
    requires_user_approval: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        """Auto-set risk_level from action_type if not explicitly overridden."""
        if self.risk_level == ActionRisk.LOW and self.action_type in ACTION_RISK_LEVELS:
            self.risk_level = ACTION_RISK_LEVELS[self.action_type]
        if self.risk_level in (ActionRisk.MEDIUM, ActionRisk.HIGH):
            self.requires_user_approval = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action_type"] = self.action_type.value
        data["risk_level"] = self.risk_level.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchAction":
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        kwargs["action_type"] = ActionType(kwargs.get("action_type", "retrieve_evidence"))
        kwargs["risk_level"] = ActionRisk(kwargs.get("risk_level", "low"))
        kwargs["status"] = ActionStatus(kwargs.get("status", "pending"))
        return cls(**kwargs)

    @property
    def is_auto_executable(self) -> bool:
        """LOW-risk actions can run automatically; others need approval."""
        return self.risk_level == ActionRisk.LOW and not self.requires_user_approval
