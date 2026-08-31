"""P7 - durable ResearchQuestion domain object.

An Opportunity is a transient signal.  A ResearchQuestion is the stable
decision record that survives across PaperWise runs.  Question text is
normalized and hashed so equivalent opportunities merge instead of duplicating.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


QUESTION_STATUSES = {"open", "active", "answered", "parked"}


def _normalize_question(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return re.sub(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$", "", text)


@dataclass
class ResearchQuestion:
    """A durable, mergeable research decision."""

    question_id: str = ""
    question: str = ""
    status: str = "open"
    importance: float = 0.7
    source_opportunities: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    related_hypotheses: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        if not self.question:
            raise ValueError("ResearchQuestion.question cannot be empty")
        if self.status not in QUESTION_STATUSES:
            raise ValueError(f"Invalid ResearchQuestion status: {self.status}")
        if not self.question_id:
            self.question_id = f"rq_{hashlib.sha1(_normalize_question(self.question).encode('utf-8')).hexdigest()[:12]}"

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    def merge_signal(
        self,
        opportunity_id: str = "",
        evidence_ref: str = "",
        hypothesis_id: str = "",
    ) -> None:
        if opportunity_id:
            self.source_opportunities = list(dict.fromkeys(self.source_opportunities + [opportunity_id]))
        if evidence_ref:
            self.evidence_refs = list(dict.fromkeys(self.evidence_refs + [evidence_ref]))
        if hypothesis_id:
            self.related_hypotheses = list(dict.fromkeys(self.related_hypotheses + [hypothesis_id]))
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchQuestion":
        kwargs = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        return cls(**kwargs)


def make_question_id(question: str) -> str:
    """Expose stable ID derivation for graph and persistence tests."""
    normalized = _normalize_question(question)
    return f"rq_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}"
