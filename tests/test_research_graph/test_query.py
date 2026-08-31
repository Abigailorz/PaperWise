"""P6 Phase A tests: ResearchGraphQuery."""

from __future__ import annotations

import pytest

from paperwise.research_graph.models import (
    EntityType,
    RelationType,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)
from paperwise.research_graph.query import ResearchGraphQuery


def _build_graph() -> ResearchGraph:
    graph = ResearchGraph(graph_id="g1", user_id="default")

    q = ResearchNode("q1", EntityType.RESEARCH_QUESTION, "3D segmentation")
    paper_a = ResearchNode("p_a", EntityType.PAPER, "Paper A")
    paper_b = ResearchNode("p_b", EntityType.PAPER, "Paper B")
    claim = ResearchNode("c1", EntityType.CLAIM, "Method A is fast", confidence=0.0)
    evidence = ResearchNode("e1", EntityType.EVIDENCE, "Table 2")
    method_a = ResearchNode("m_a", EntityType.METHOD, "Method A")
    method_b = ResearchNode("m_b", EntityType.METHOD, "Method B")
    opp = ResearchNode("o1", EntityType.OPPORTUNITY, "Gap in evaluation")

    graph.add_node(q)
    graph.add_node(paper_a)
    graph.add_node(paper_b)
    graph.add_node(claim)
    graph.add_node(evidence)
    graph.add_node(method_a)
    graph.add_node(method_b)
    graph.add_node(opp)

    graph.add_edge(ResearchEdge(q.node_id, paper_a.node_id, RelationType.RELATED_TO))
    graph.add_edge(ResearchEdge(q.node_id, paper_b.node_id, RelationType.RELATED_TO))
    graph.add_edge(ResearchEdge(claim.node_id, evidence.node_id, RelationType.SUPPORTED_BY))
    graph.add_edge(ResearchEdge(method_a.node_id, method_b.node_id, RelationType.COMPLEMENTS))
    graph.add_edge(ResearchEdge(q.node_id, opp.node_id, RelationType.HAS_GAP))
    return graph


class TestResearchGraphQuery:
    def test_find_related_papers(self):
        query = ResearchGraphQuery(_build_graph())
        papers = query.find_related_papers("3D segmentation")
        assert len(papers) == 2

    def test_find_supporting_evidence(self):
        query = ResearchGraphQuery(_build_graph())
        evidence = query.find_supporting_evidence("Method A is fast")
        assert len(evidence) == 1
        assert evidence[0].label == "Table 2"

    def test_find_unverified_claims(self):
        graph = _build_graph()
        unverified = ResearchNode("c2", EntityType.CLAIM, "Unverified claim", confidence=0.0)
        graph.add_node(unverified)
        query = ResearchGraphQuery(graph)
        unverified_claims = query.find_unverified_claims()
        assert len(unverified_claims) == 1
        assert unverified_claims[0].label == "Unverified claim"

    def test_find_method_complements(self):
        query = ResearchGraphQuery(_build_graph())
        complements = query.find_method_complements("Method A")
        assert len(complements) == 1
        assert complements[0].label == "Method B"

    def test_find_research_gaps(self):
        query = ResearchGraphQuery(_build_graph())
        gaps = query.find_research_gaps()
        assert len(gaps) == 1
        assert gaps[0].label == "Gap in evaluation"
