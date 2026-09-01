"""P6 Phase A - Graph Query Layer.

Planner must not operate Graph Store directly. All graph reads go through
ResearchGraphQuery, which provides typed, testable query methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from paperwise.research_graph.models import (
    EntityType,
    RelationType,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)


@dataclass
class GraphNodePair:
    """P9.3 — A pair of related nodes from different papers."""

    node_a: ResearchNode
    node_b: ResearchNode
    paper_a: str = ""
    paper_b: str = ""
    relation: RelationType = RelationType.RELATED_TO
    shared_tokens: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_cross_paper(self) -> bool:
        return bool(self.paper_a and self.paper_b and self.paper_a != self.paper_b)


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

    # ══════════ P9.3 — Cross-Paper Queries ══════════

    def _paper_of_node(self, node: ResearchNode) -> str:
        """Extract paper identity from a node's metadata or source field."""
        return str(
            node.metadata.get("paper_id", "")
            or node.source
            or ""
        )

    @staticmethod
    def _token_overlap(label_a: str, label_b: str) -> list[str]:
        """Shared tokens between two labels (deterministic)."""
        import re
        tokens_a = set(re.findall(r"[a-z][a-z\-_0-9]{2,}", (label_a or "").lower()))
        tokens_b = set(re.findall(r"[a-z][a-z\-_0-9]{2,}", (label_b or "").lower()))
        stopwords = {"the", "this", "that", "with", "from", "and", "for", "method", "approach"}
        return sorted(tokens_a & tokens_b - stopwords)

    def find_cross_paper_relationships(self) -> list[GraphNodePair]:
        """METHOD↔METHOD edges spanning different papers."""
        methods = self._by_type(EntityType.METHOD)
        pairs: list[GraphNodePair] = []
        seen: set[tuple[str, str]] = set()
        for i, method_a in enumerate(methods):
            paper_a = self._paper_of_node(method_a)
            if not paper_a:
                continue
            for method_b in methods[i + 1:]:
                paper_b = self._paper_of_node(method_b)
                if not paper_b or paper_b == paper_a:
                    continue
                pair_key = tuple(sorted([method_a.node_id, method_b.node_id]))
                if pair_key in seen:
                    continue
                shared = self._token_overlap(method_a.label, method_b.label)
                confidence = min(1.0, len(shared) / 3.0) if shared else 0.0
                if confidence < 0.3:
                    continue
                seen.add(pair_key)
                pairs.append(GraphNodePair(
                    node_a=method_a,
                    node_b=method_b,
                    paper_a=paper_a,
                    paper_b=paper_b,
                    relation=RelationType.RELATED_TO,
                    shared_tokens=shared,
                    confidence=confidence,
                ))
        return pairs

    def find_contradiction_hubs(self) -> list[ResearchNode]:
        """Entities with opposing CLAIM edges from different papers.

        A contradiction hub is a CLAIM node that has CONTRADICTS edges
        connecting to claims sourced from a different paper.
        """
        results: list[ResearchNode] = []
        for claim in self._by_type(EntityType.CLAIM):
            contradicts = self._neighbors(claim.node_id, RelationType.CONTRADICTS, "both")
            if not contradicts:
                continue
            own_paper = self._paper_of_node(claim)
            cross_paper = any(
                self._paper_of_node(other) != own_paper
                for _, other in contradicts
            )
            if cross_paper:
                results.append(claim)
        return results

    def find_complementarity_pairs(self) -> list[GraphNodePair]:
        """METHOD pairs addressing different DIMENSION nodes.

        Two methods connected via COMPLEMENTS or sharing tokens, where each
        is associated with a different dimension/aspect in the graph.
        """
        methods = self._by_type(EntityType.METHOD)
        pairs: list[GraphNodePair] = []
        seen: set[tuple[str, str]] = set()

        # Build method → dimension mapping via EVALUATES or RELATED_TO edges.
        method_dims: dict[str, set[str]] = {}
        for method in methods:
            dims: set[str] = set()
            for _, neighbor in self._neighbors(method.node_id, RelationType.EVALUATES, "out"):
                if neighbor.entity_type == EntityType.DATASET:
                    dims.add(neighbor.label.lower()[:60])
            for _, neighbor in self._neighbors(method.node_id, RelationType.RELATED_TO, "both"):
                if neighbor.entity_type in (EntityType.DATASET, EntityType.FINDING):
                    dims.add(neighbor.label.lower()[:60])
            if dims:
                method_dims[method.node_id] = dims

        for i, method_a in enumerate(methods):
            dims_a = method_dims.get(method_a.node_id, set())
            paper_a = self._paper_of_node(method_a)
            if not paper_a:
                continue
            for method_b in methods[i + 1:]:
                dims_b = method_dims.get(method_b.node_id, set())
                paper_b = self._paper_of_node(method_b)
                if not paper_b or paper_b == paper_a:
                    continue
                # Must share tokens AND have at least some dimension overlap
                # (indicating complementary focus on related aspects).
                shared = self._token_overlap(method_a.label, method_b.label)
                if not shared:
                    continue
                dim_overlap = dims_a & dims_b
                if not dim_overlap and (dims_a or dims_b):
                    # Different dimensions with shared technique = complementary
                    confidence = min(1.0, len(shared) / 3.0)
                    pair_key = tuple(sorted([method_a.node_id, method_b.node_id]))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    pairs.append(GraphNodePair(
                        node_a=method_a,
                        node_b=method_b,
                        paper_a=paper_a,
                        paper_b=paper_b,
                        relation=RelationType.COMPLEMENTS,
                        shared_tokens=shared,
                        confidence=confidence,
                    ))
        return pairs
