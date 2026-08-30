"""P5 Research Graph domain model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class EntityType(str, Enum):
    USER = "user"
    PROJECT = "project"
    RESEARCH_QUESTION = "research_question"
    PAPER = "paper"
    METHOD = "method"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    FINDING = "finding"
    OPPORTUNITY = "opportunity"
    HYPOTHESIS = "hypothesis"


class RelationType(str, Enum):
    OWNS = "owns"
    STUDIES = "studies"
    RELATED_TO = "related_to"
    PROPOSES = "proposes"
    SUPPORTED_BY = "supported_by"
    EVALUATES = "evaluates"
    USES = "uses"
    HAS_GAP = "has_gap"
    CONTRADICTS = "contradicts"
    COMPLEMENTS = "complements"
    SUGGESTS_HYPOTHESIS = "suggests_hypothesis"
    DERIVED_FROM = "derived_from"


@dataclass
class ResearchNode:
    node_id: str
    entity_type: EntityType
    label: str
    description: str = ""
    confidence: float = 0.0
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entity_type"] = self.entity_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchNode":
        data = dict(data)
        data["entity_type"] = EntityType(data.get("entity_type", "claim"))
        return cls(**data)


@dataclass
class ResearchEdge:
    source_id: str
    target_id: str
    relation: RelationType
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def edge_id(self) -> str:
        return f"{self.source_id}::{self.relation.value}::{self.target_id}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["relation"] = self.relation.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchEdge":
        data = dict(data)
        data["relation"] = RelationType(data.get("relation", "related_to"))
        return cls(**data)


@dataclass
class ResearchGraph:
    graph_id: str
    user_id: str
    project_id: str = "default"
    nodes: list[ResearchNode] = field(default_factory=list)
    edges: list[ResearchEdge] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def node_map(self) -> dict[str, ResearchNode]:
        return {node.node_id: node for node in self.nodes}

    def add_node(self, node: ResearchNode) -> ResearchNode:
        existing = self.node_map()
        if node.node_id in existing:
            current = existing[node.node_id]
            if node.description:
                current.description = node.description
            if node.label:
                current.label = node.label
            current.confidence = max(current.confidence, node.confidence)
            current.metadata.update(node.metadata)
            return current
        self.nodes.append(node)
        self.touch()
        return node

    def add_edge(self, edge: ResearchEdge) -> bool:
        existing = {edge.edge_id: edge for edge in self.edges}
        if edge.edge_id in existing:
            current = existing[edge.edge_id]
            current.confidence = max(current.confidence, edge.confidence)
            current.evidence_ids = list(dict.fromkeys(current.evidence_ids + edge.evidence_ids))
            current.metadata.update(edge.metadata)
            return False
        self.edges.append(edge)
        self.touch()
        return True

    def merge(self, other: "ResearchGraph") -> "ResearchGraph":
        for node in other.nodes:
            self.add_node(node)
        for edge in other.edges:
            self.add_edge(edge)
        self.touch()
        return self

    def neighbors(self, node_id: str) -> list[tuple[ResearchEdge, ResearchNode]]:
        node_map = self.node_map()
        result = []
        for edge in self.edges:
            if edge.source_id == node_id and edge.target_id in node_map:
                result.append((edge, node_map[edge.target_id]))
            elif edge.target_id == node_id and edge.source_id in node_map:
                result.append((edge, node_map[edge.source_id]))
        return result

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for node in self.nodes:
            key = node.entity_type.value
            by_type[key] = by_type.get(key, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_types": dict(sorted(by_type.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "updated_at": self.updated_at,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "stats": self.stats(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchGraph":
        return cls(
            graph_id=data["graph_id"],
            user_id=data.get("user_id", "default"),
            project_id=data.get("project_id", "default"),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            nodes=[ResearchNode.from_dict(node) for node in data.get("nodes", [])],
            edges=[ResearchEdge.from_dict(edge) for edge in data.get("edges", [])],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "ResearchGraph":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
