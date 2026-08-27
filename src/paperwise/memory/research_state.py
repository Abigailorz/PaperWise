"""ResearchState: the bridge between memory and DAG execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from paperwise.memory.storage import create_storage


@dataclass
class Finding:
    """A confirmed finding from DAG execution."""
    node_id: str
    claim: str
    evidence: str = ""
    confidence: float = 0.8


@dataclass
class KnowledgeGap:
    """A knowledge gap discovered during execution."""
    gap_id: str
    description: str
    node_id: str = ""
    urgency: str = "medium"  # low | medium | high
    suggested_action: str = ""


@dataclass
class ResearchState:
    """Mutable state of the current research task."""
    state_id: str
    user_id: str
    session_id: Optional[str] = None
    current_task: str = ""
    intent: str = ""  # simple_qa | analysis | comparison | report | ppt | verify | open_ended
    complexity: str = "simple"  # simple | medium | complex
    current_paper: Optional[str] = None
    related_papers: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    gaps: list[KnowledgeGap] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    dag_status: str = "idle"  # idle | running | paused | completed | failed | budget_exhausted
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchState":
        # Rehydrate dataclass fields for findings/gaps
        findings = [Finding(**f) for f in data.get("findings", [])]
        gaps = [KnowledgeGap(**g) for g in data.get("gaps", [])]
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        kwargs["findings"] = findings
        kwargs["gaps"] = gaps
        return cls(**kwargs)

    def mark_updated(self) -> None:
        self.updated_at = datetime.now().isoformat()


class ResearchStateManager:
    """Persist and load ResearchState for a session/user."""

    def __init__(self, workspace: Path, user_id: str = "default", backend: str = "sqlite"):
        self.user_id = user_id
        self.store = create_storage(backend, workspace / ".research_state")
        self._state: Optional[ResearchState] = None

    def new(self, current_task: str, session_id: Optional[str] = None) -> ResearchState:
        sid = f"rs_{uuid.uuid4().hex[:8]}"
        state = ResearchState(
            state_id=sid,
            user_id=self.user_id,
            session_id=session_id,
            current_task=current_task,
        )
        self._state = state
        self._save()
        return state

    def get(self) -> Optional[ResearchState]:
        if self._state is None:
            data = self.store.get("research_state", self.user_id)
            if data and "state" in data:
                try:
                    self._state = ResearchState.from_dict(data["state"])
                except Exception:
                    pass
        return self._state

    def update(self, state: ResearchState) -> None:
        self._state = state
        state.mark_updated()
        self._save()

    def add_finding(self, node_id: str, claim: str, evidence: str = "", confidence: float = 0.8) -> None:
        state = self.get()
        if state is None:
            return
        state.findings.append(Finding(node_id=node_id, claim=claim, evidence=evidence, confidence=confidence))
        self.update(state)

    def add_gap(self, description: str, node_id: str = "", urgency: str = "medium", suggested_action: str = "") -> None:
        state = self.get()
        if state is None:
            return
        gid = f"gap_{uuid.uuid4().hex[:6]}"
        state.gaps.append(KnowledgeGap(
            gap_id=gid, description=description, node_id=node_id,
            urgency=urgency, suggested_action=suggested_action,
        ))
        self.update(state)

    def close_gap(self, gap_id: str) -> bool:
        state = self.get()
        if state is None:
            return False
        before = len(state.gaps)
        state.gaps = [g for g in state.gaps if g.gap_id != gap_id]
        if len(state.gaps) < before:
            self.update(state)
            return True
        return False

    def _save(self) -> None:
        if self._state is None:
            return
        try:
            self.store.put("research_state", self.user_id, {"state": self._state.to_dict()})
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"ResearchState save failed: {e}")
