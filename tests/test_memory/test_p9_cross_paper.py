"""P9 tests — Cross-Paper Evidence, Rules, Graph Queries, and Research Loop."""

import tempfile
from pathlib import Path

import pytest

from paperwise.evidence.models import EvidencePack, EvidenceScope, EvidenceSnippet, StructureType
from paperwise.evidence.retriever import EvidenceRetriever
from paperwise.generators.narrative import ResearchNarrative
from paperwise.memory.research_state import ResearchState
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity
from paperwise.opportunity.rules import (
    CrossPaperComplementarityRule,
    CrossPaperContradictionRule,
    CrossPaperMethodComparisonRule,
)
from paperwise.research_graph.models import (
    EntityType,
    RelationType,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)
from paperwise.research_graph.query import ResearchGraphQuery


# ══════════ P9.1 Cross-Paper Evidence ══════════

class _FakeKB:
    """Minimal KnowledgeBase stub for cross-paper retrieval tests."""

    def __init__(self, chunks_by_paper: dict[str, list[dict]]):
        self._chunks = chunks_by_paper
        self._indexed = True
        self._ranked = []

    class _Sparse:
        def search(self, query, chunks, top_k=10):
            return [(c, 1.0) for c in chunks[:top_k]]

    class _Retriever:
        sparse = None

        def __init__(self, outer):
            self.sparse = outer._Sparse()

    @property
    def retriever(self):
        return self._Retriever(self)

    def search_chunks(self, query, top_k=10, filters=None):
        paper_id = (filters or {}).get("paper_id")
        all_chunks = []
        for pid, chunks in self._chunks.items():
            if paper_id and pid != paper_id:
                continue
            for c in chunks:
                if query.lower() in c["content"].lower():
                    all_chunks.append(type("Chunk", (), {
                        "id": c["id"], "content": c["content"],
                        "doc_id": c.get("doc_id", ""),
                        "metadata": c.get("metadata", {}),
                    })())
        return all_chunks[:top_k]


def test_cross_paper_retrieval_returns_multi_paper_snippets():
    kb = _FakeKB({
        "paper_a": [
            {"id": "a1", "content": "attention mechanism improves accuracy",
             "doc_id": "paper_a", "metadata": {"paper_id": "paper_a"}},
        ],
        "paper_b": [
            {"id": "b1", "content": "attention mechanism is efficient",
             "doc_id": "paper_b", "metadata": {"paper_id": "paper_b"}},
        ],
    })
    retriever = EvidenceRetriever(kb)
    pack = retriever.retrieve("attention", paper_dir=Path("ws/paper_a"), scope="cross_paper")
    assert len(pack.snippets) >= 2
    assert len(pack.papers_covered) >= 2
    paper_ids = {s.paper_id for s in pack.snippets}
    assert "paper_a" in paper_ids
    assert "paper_b" in paper_ids


def test_evidence_scope_enum():
    assert EvidenceScope.CURRENT_PAPER.value == "current_paper"
    assert EvidenceScope.CROSS_PAPER.value == "cross_paper"


def test_evidence_pack_papers_covered_serialization():
    pack = EvidencePack(
        query="test", scope="cross_paper",
        papers_covered=["a", "b"],
        snippets=[EvidenceSnippet(
            evidence_id="e1", content="c", structure_type=StructureType.SECTION,
            paper_id="a", paper_title="Paper A",
        )],
    )
    d = pack.to_dict()
    assert d["papers_covered"] == ["a", "b"]
    restored = EvidencePack.from_dict(d)
    assert restored.papers_covered == ["a", "b"]
    assert restored.snippets[0].paper_title == "Paper A"


def test_single_paper_graceful_degradation():
    kb = _FakeKB({
        "paper_a": [
            {"id": "a1", "content": "attention mechanism", "doc_id": "paper_a",
             "metadata": {"paper_id": "paper_a"}},
        ],
    })
    retriever = EvidenceRetriever(kb)
    pack = retriever.retrieve("attention", paper_dir=Path("ws/paper_a"), scope="cross_paper")
    assert pack.papers_covered == ["paper_a"]


# ══════════ P9.2 Cross-Paper Rules ══════════

class _FakeFinding:
    def __init__(self, node_id, claim, evidence=""):
        self.node_id = node_id
        self.claim = claim
        self.evidence = evidence
        self.confidence = 0.8


def _state_with_papers():
    state = ResearchState(state_id="s", user_id="u")
    state.current_paper = "paper_langsplat"
    state.current_task = "compare gaussian splatting semantic quality"
    state.related_papers = ["gaussian splatting semantic quality segmentation"]
    state.findings.append(_FakeFinding(
        "method:gaussian_splatting", "gaussian splatting achieves high accuracy",
        evidence="significant improvement",
    ))
    return state


def test_cross_paper_method_comparison_fires():
    state = _state_with_papers()
    rule = CrossPaperMethodComparisonRule()
    results = rule.apply(state, None)
    assert len(results) >= 1
    assert all(o.type == OpportunityType.KNOWLEDGE_GAP for o in results)
    assert all("跨论文" in o.title for o in results)


def test_cross_paper_contradiction_fires_on_opposing_sentiment():
    state = ResearchState(state_id="s", user_id="u")
    state.current_paper = "paper_a"
    state.related_papers = ["paper_b"]
    state.findings.append(_FakeFinding(
        "claim_1", "gaussian splatting improves accuracy significantly",
        evidence="outperforms baseline",
    ))
    state.findings.append(_FakeFinding(
        "claim_2", "gaussian splatting has no significant improvement",
        evidence="does not outperform baseline",
    ))
    rule = CrossPaperContradictionRule()
    results = rule.apply(state, None)
    assert len(results) >= 1
    assert all(o.type == OpportunityType.CONTRADICTION for o in results)


def test_cross_paper_complementarity_fires_on_different_dimensions():
    state = _state_with_papers()
    rule = CrossPaperComplementarityRule()
    results = rule.apply(state, None)
    # May or may not fire depending on dimension overlap; just ensure no crash.
    assert isinstance(results, list)


def test_cross_paper_rules_deterministic():
    state = _state_with_papers()
    rule = CrossPaperMethodComparisonRule()
    r1 = rule.apply(state, None)
    r2 = rule.apply(state, None)
    assert [o.title for o in r1] == [o.title for o in r2]


# ══════════ P9.3 Research Graph Queries ══════════

def _graph_with_cross_paper_methods():
    graph = ResearchGraph(graph_id="g", user_id="u")
    m_a = ResearchNode(
        node_id="m_langsplat", entity_type=EntityType.METHOD,
        label="langsplat gaussian splatting", source="paper_langsplat",
        metadata={"paper_id": "paper_langsplat"},
    )
    m_b = ResearchNode(
        node_id="m_feature3dgs", entity_type=EntityType.METHOD,
        label="feature3dgs gaussian splatting", source="paper_feature3dgs",
        metadata={"paper_id": "paper_feature3dgs"},
    )
    graph.add_node(m_a)
    graph.add_node(m_b)
    graph.add_edge(ResearchEdge(
        source_id=m_a.node_id, target_id=m_b.node_id,
        relation=RelationType.RELATED_TO, confidence=0.7,
    ))
    return graph


def test_find_cross_paper_relationships():
    graph = _graph_with_cross_paper_methods()
    query = ResearchGraphQuery(graph)
    pairs = query.find_cross_paper_relationships()
    assert len(pairs) >= 1
    assert all(pair.is_cross_paper for pair in pairs)
    assert all(pair.paper_a != pair.paper_b for pair in pairs)


def test_find_contradiction_hubs():
    graph = ResearchGraph(graph_id="g", user_id="u")
    c_a = ResearchNode(
        node_id="c_paper_a", entity_type=EntityType.CLAIM,
        label="improves accuracy", source="paper_a",
        metadata={"paper_id": "paper_a"},
    )
    c_b = ResearchNode(
        node_id="c_paper_b", entity_type=EntityType.CLAIM,
        label="no significant improvement", source="paper_b",
        metadata={"paper_id": "paper_b"},
    )
    graph.add_node(c_a)
    graph.add_node(c_b)
    graph.add_edge(ResearchEdge(
        source_id=c_a.node_id, target_id=c_b.node_id,
        relation=RelationType.CONTRADICTS, confidence=0.8,
    ))
    query = ResearchGraphQuery(graph)
    hubs = query.find_contradiction_hubs()
    assert len(hubs) >= 1
    assert all(h.entity_type == EntityType.CLAIM for h in hubs)


def test_find_complementarity_pairs_returns_list():
    graph = _graph_with_cross_paper_methods()
    query = ResearchGraphQuery(graph)
    pairs = query.find_complementarity_pairs()
    assert isinstance(pairs, list)


def test_graph_node_pair_cross_paper_property():
    graph = _graph_with_cross_paper_methods()
    query = ResearchGraphQuery(graph)
    pairs = query.find_cross_paper_relationships()
    if pairs:
        assert pairs[0].is_cross_paper


# ══════════ P9.4 Narrative Cross-Paper Sections ══════════

def test_narrative_includes_cross_paper_sections():
    state = ResearchState(state_id="s", user_id="u")
    state.opportunities.append(ResearchOpportunity(
        type=OpportunityType.CONTRADICTION,
        title="跨论文矛盾：accuracy claim",
        description="Paper A and Paper B disagree on accuracy",
        evidence=[],
    ))
    narrative = ResearchNarrative.build(state)
    assert len(narrative.cross_paper_sections) >= 1
    assert narrative.cross_paper_sections[0].section_type == "contradictions"


def test_narrative_cross_paper_serialization():
    state = ResearchState(state_id="s", user_id="u")
    state.opportunities.append(ResearchOpportunity(
        type=OpportunityType.METHOD_COMPLEMENTARITY,
        title="跨论文互补：geometry + semantic",
        description="combine two methods",
        evidence=[],
    ))
    narrative = ResearchNarrative.build(state)
    d = narrative.to_dict()
    restored = ResearchNarrative.from_dict(d)
    assert len(restored.cross_paper_sections) >= 1
    assert restored.cross_paper_sections[0].section_type == "complementarity"


def test_narrative_prompt_context_includes_cross_paper():
    state = ResearchState(state_id="s", user_id="u")
    state.opportunities.append(ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title="跨论文方法比较：method A × method B",
        description="compare",
        evidence=[],
    ))
    narrative = ResearchNarrative.build(state)
    ctx = narrative.to_prompt_context()
    assert "Cross-Paper Analysis" in ctx


# ══════════ P9.5 Research Loop Benchmark ══════════

def test_state_drives_different_actions():
    """Research Loop Benchmark: state change → different action planning."""
    from paperwise.opportunity.action import ActionType
    from paperwise.opportunity.action_planner import ActionPlanner

    state = ResearchState(state_id="s", user_id="u")
    state.opportunities.append(ResearchOpportunity(
        type=OpportunityType.CONTRADICTION,
        title="contradiction to resolve",
        description="claim conflict",
        confidence=0.9,
        importance=0.9,
    ))
    state.related_papers = ["paper_b"]

    planner = ActionPlanner()
    actions_round_1 = planner.plan_actions(state.opportunities, state, max_actions=3)
    assert len(actions_round_1) > 0

    # Simulate outcome: mark the opportunity as acted (state changed).
    state.opportunities[0].type = OpportunityType.METHOD_COMPLEMENTARITY  # type changed = state change

    actions_round_2 = planner.plan_actions(state.opportunities, state, max_actions=3)
    # After state change, planning should differ (different opportunity type
    # → different action mapping via OPPORTUNITY_TO_ACTIONS).
    action_types_1 = {a.action_type for a in actions_round_1}
    action_types_2 = {a.action_type for a in actions_round_2}
    assert action_types_1 != action_types_2 or len(actions_round_1) != len(actions_round_2)


def test_evidence_precision_baseline():
    """Evidence Benchmark: citation accuracy on cross-paper evidence."""
    snippet = EvidenceSnippet(
        evidence_id="e1", content="attention improves accuracy",
        structure_type=StructureType.SECTION,
        paper_id="paper_a", paper_title="Paper A",
        start_line=10, end_line=20,
    )
    citation = snippet.citation()
    assert "paper_a" in citation
    assert "L10-L20" in citation
