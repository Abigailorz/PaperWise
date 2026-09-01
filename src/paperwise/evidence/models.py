"""Structured evidence objects shared by retrieval, reasoning, and review."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StructureType(str, Enum):
    SECTION = "section"
    FIGURE = "figure"
    TABLE = "table"
    EQUATION = "equation"
    REFERENCE = "reference"


class EvidenceScope(str, Enum):
    """P9.1 — Retrieval scope: single paper or cross-paper library."""

    CURRENT_PAPER = "current_paper"
    CROSS_PAPER = "cross_paper"


@dataclass
class EvidenceSnippet:
    """A retrievable, citable piece of paper structure."""

    evidence_id: str
    content: str
    structure_type: StructureType
    paper_id: str
    paper_title: str = ""
    section: str = ""
    start_line: int = 0
    end_line: int = 0
    page: int = 0
    location: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def citation(self) -> str:
        if self.start_line and self.end_line:
            return f"[source: {self.paper_id}/text.md L{self.start_line}-L{self.end_line}]"
        if self.location:
            return f"[source: {self.paper_id}/{self.location}]"
        return f"[source: {self.paper_id}]"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["structure_type"] = self.structure_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceSnippet":
        data = dict(data)
        data["structure_type"] = StructureType(data.get("structure_type", "section"))
        return cls(**data)


@dataclass
class EvidencePack:
    """The compact evidence context handed to a reasoning node."""

    query: str
    snippets: list[EvidenceSnippet] = field(default_factory=list)
    scope: str = "current_paper"
    papers_covered: list[str] = field(default_factory=list)
    retrieval_queries: list[str] = field(default_factory=list)
    low_recall: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.snippets

    def to_context(self, max_chars: int = 6000) -> str:
        parts = [f"<evidence_pack query=\"{self.query}\">"]
        for snippet in self.snippets:
            parts.append(
                f"<evidence id=\"{snippet.evidence_id}\" type=\"{snippet.structure_type.value}\">"
                f"{snippet.content}\n{snippet.citation()}"
                "</evidence>"
            )
        if self.low_recall:
            parts.append("<retrieval_warning>Insufficient evidence; request replanning.</retrieval_warning>")
        parts.append("</evidence_pack>")
        context = "\n".join(parts)
        if len(context) <= max_chars:
            return context
        return context[:max_chars].rsplit(" ", 1)[0] + "\n</evidence_pack>"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "scope": self.scope,
            "papers_covered": self.papers_covered,
            "retrieval_queries": self.retrieval_queries,
            "low_recall": self.low_recall,
            "snippets": [s.to_dict() for s in self.snippets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidencePack":
        return cls(
            query=data.get("query", ""),
            scope=data.get("scope", "current_paper"),
            papers_covered=data.get("papers_covered", []),
            retrieval_queries=data.get("retrieval_queries", []),
            low_recall=data.get("low_recall", False),
            snippets=[EvidenceSnippet.from_dict(s) for s in data.get("snippets", [])],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "EvidencePack":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
