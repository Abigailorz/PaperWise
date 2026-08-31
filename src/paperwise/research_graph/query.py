"""P6 Phase A - Graph Query Layer.

Planner must not operate Graph Store directly. All graph reads go through
ResearchGraphQuery, which provides typed, testable query methods.
"""

from __future__ import annotations

from typing import Optional

from paperwise.research_graph.models import (
    EntityType,
    RelationType,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)


class ResearchGraphQuery:
    """Typed queries over a ResearchGraph. Read-only."""

    def __init__(self, graph: ResearchGraph):
        self.graph = graph
        self._node_map = graph.node_map()

    def _neighbors(
        self,
        node_id: str,
        relation: Optional[RelationType] = None,
        direction: str = "out",
    ) -> list[tuple[ResearchEdge, ResearchNode]]:
        """Get neighbor edges + nodes for a given node."""
        results: list[tuple[ResearchEdge, ResearchNode]] = []
        for edge in self.graph.edges:
            match = (
                (edge.source_id == node_id and direction in ("out", "both"))
                or (edge.target_id == node_id and direction in ("in", "both"))
            )
            if not match:
                continue
            if relation and edge.relation != relation:
                continue
            if direction == "out":
                other_id = edge.target_id
            elif direction == "in":
                other_id = edge.source_id
            else:
                other_id = edge.target_id if edge.source_id == node_id else edge.source_id
            node = self._node_map.get(other_id)
            if node:
                results.append((edge, node))
        return results

    def _by_type(self, entity_type: EntityType) -> list[ResearchNode]:
        return [n for n in self.graph.nodes if n.entity_type == entity_type]

    def find_related_papers(self, question: str) -> list[ResearchNode]:
        """Papers connected to a research question via RELATED_TO."""
        questions = [
            q for q in self._by_type(EntityType.RESEARCH_QUESTION)
            if question.lower() in q.label.lower() or question.lower() in q.description.lower()
        ]
        papers: list[ResearchNode] = []
        seen: set[str] = set()
        for q in questions:
            for _, node in self._neighbors(q.node_id, RelationType.RELATED_TO, "out"):
                if node.entity_type == EntityType.PAPER and node.node_id not in seen:
                    seen.add(node.node_id)
                    papers.append(node)
        return papers

    def find_supporting_evidence(self, claim_text: str) -> list[ResearchNode]:
        """Evidence nodes supporting a claim via SUPPORTED_BY."""
        claims = [
            c for c in self._by_type(EntityType.CLAIM)
            if claim_text.lower() in c.label.lower() or claim_text.lower() in c.description.lower()
        ]
        evidence: list[ResearchNode] = []
        seen: set[str] = set()
        for c in claims:
            for _, node in self._neighbors(c.node_id, RelationType.SUPPORTED_BY, "out"):
                if node.entity_type == EntityType.EVIDENCE and node.node_id not in seen:
                    seen.add(node.node_id)
                    evidence.append(node)
        return evidence

    def find_contradictions(self, claim_text: str) -> list[ResearchNode]:
        """Nodes that contradict a claim via CONTRADICTS."""
        claims = [
            c for c in self._by_type(EntityType.CLAIM)
            if claim_text.lower() in c.label.lower()
        ]
        results: list[ResearchNode] = []
        seen: set[str] = set()
        for c in claims:
            for _, node in self._neighbors(c.node_id, RelationType.CONTRADICTS, "both"):
                if node.node_id not in seen:
                    seen.add(node.node_id)
                    results.append(node)
        return results

    def find_method_complements(self, method_label: str) -> list[ResearchNode]:
        """Methods connected via COMPLEMENTS to a given method."""
        methods = [
            m for m in self._by_type(EntityType.METHOD)
            if method_label.lower() in m.label.lower()
        ]
        results: list[ResearchNode] = []
        seen: set[str] = set()
        for m in methods:
            for _, node in self._neighbors(m.node_id, RelationType.COMPLEMENTS, "both"):
                if node.entity_type == EntityType.METHOD and node.node_id not in seen:
                    seen.add(node.node_id)
                    results.append(node)
        return results

    def find_open_opportunities(self, project_id: str = "default") -> list[ResearchNode]:
        """Opportunity nodes in a project."""
        return self._by_type(EntityType.OPPORTUNITY)

    def find_unverified_claims(self, project_id: str = "default") -> list[ResearchNode]:
        """Claims that lack a SUPPORTED_BY edge to evidence."""
        supported_ids: set[str] = set()
        for edge in self.graph.edges:
            if edge.relation == RelationType.SUPPORTED_BY:
                supported_ids.add(edge.source_id)
        return [
            c for c in self._by_type(EntityType.CLAIM)
            if c.node_id not in supported_ids
        ]

    def find_research_gaps(self, project_id: str = "default") -> list[ResearchNode]:
        """Opportunity nodes reachable via HAS_GAP from research questions."""
        questions = self._by_type(EntityType.RESEARCH_QUESTION)
        results: list[ResearchNode] = []
        seen: set[str] = set()
        for q in questions:
            for _, node in self._neighbors(q.node_id, RelationType.HAS_GAP, "out"):
                if node.node_id not in seen:
                    seen.add(node.node_id)
                    results.append(node)
        return results
