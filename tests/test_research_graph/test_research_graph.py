from paperwise.evidence import EvidencePack, EvidenceSnippet, StructureType
from paperwise.memory.research_state import Finding, KnowledgeGap, ResearchState
from paperwise.opportunity.models import EvidenceRef, OpportunityType, ResearchOpportunity
from paperwise.research_graph import (
    EntityType,
    RelationType,
    ResearchGraphBuilder,
    ResearchGraphStore,
)


def make_state() -> ResearchState:
    state = ResearchState(state_id="rs_test", user_id="alice", session_id="s1")
    state.current_task = "Compare adaptive retrieval methods for 3D language grounding"
    state.current_paper = "/tmp/langsplat"
    state.findings.append(Finding(
        node_id="analyze_method",
        claim="Language Gaussian Splatting improves grounding quality.",
        evidence="[source: text.md L12-L18]",
        confidence=0.9,
    ))
    state.next_steps.append("Compare feature and language field combinations")
    state.opportunities.append(ResearchOpportunity(
        type=OpportunityType.METHOD_COMPLEMENTARITY,
        title="Feature and language methods are complementary",
        description="Both methods address language grounding but use different features.",
        confidence=0.75,
        related_entities=["langsplat", "feature3dgs"],
        evidence=[EvidenceRef(
            source_type="paper_section",
            source_id="section:langsplat:12",
            excerpt="Language grounding improves.",
            location="text.md L12-L18",
        )],
    ))
    return state


def make_pack() -> EvidencePack:
    return EvidencePack(query="language grounding", snippets=[
        EvidenceSnippet(
            evidence_id="section:langsplat:12",
            content="Language Gaussian Splatting improves grounding quality.",
            structure_type=StructureType.SECTION,
            paper_id="langsplat",
            section="Experiments",
            start_line=12,
            end_line=18,
            score=0.8,
        ),
        EvidenceSnippet(
            evidence_id="table:langsplat:1",
            content="Table 1: PSNR, SSIM, and language query accuracy.",
            structure_type=StructureType.TABLE,
            paper_id="langsplat",
            page=5,
            score=0.6,
        ),
    ])


def test_builder_builds_expected_entities_and_relations():
    graph = ResearchGraphBuilder().build(
        make_state(),
        [make_pack()],
        {
            "title": "LangSplat",
            "method": "Language Gaussian Splatting",
            "datasets": [{"name": "LERF"}],
            "experiments": [{"title": "Language query benchmark"}],
            "claims": [{"claim": "LangSplat outperforms CLIP baselines", "evidence": ["L32-L40"]}],
        },
    )
    stats = graph.stats()
    assert stats["node_types"]["user"] == 1
    assert stats["node_types"]["project"] == 1
    assert stats["node_types"]["research_question"] == 1
    assert stats["node_types"]["paper"] >= 1
    assert stats["node_types"]["method"] == 1
    assert stats["node_types"]["claim"] == 1
    assert stats["node_types"]["evidence"] >= 3
    assert stats["node_types"]["dataset"] == 1
    assert stats["node_types"]["experiment"] == 1
    assert stats["node_types"]["finding"] == 1
    assert stats["node_types"]["opportunity"] == 1
    assert stats["node_types"]["hypothesis"] == 1

    paper_nodes = [node for node in graph.nodes if node.entity_type == EntityType.PAPER
                   and node.label == "LangSplat"]
    method_nodes = [node for node in graph.nodes if node.entity_type == EntityType.METHOD]
    opportunity_nodes = [node for node in graph.nodes if node.entity_type == EntityType.OPPORTUNITY]
    paper_id = paper_nodes[0].node_id
    method_id = method_nodes[0].node_id
    opportunity_id = opportunity_nodes[0].node_id
    assert any(edge.source_id == paper_id and edge.target_id == method_id
               and edge.relation == RelationType.PROPOSES for edge in graph.edges)
    assert any(edge.target_id == opportunity_id and edge.relation == RelationType.COMPLEMENTS
               for edge in graph.edges)
    assert any(edge.evidence_ids for edge in graph.edges
               if edge.target_id == opportunity_id)


def test_graph_serialization_roundtrip_and_merge(tmp_path):
    builder = ResearchGraphBuilder()
    first = builder.build(make_state(), [make_pack()], {"title": "LangSplat", "method": "LangSplat"})
    second = builder.build(make_state(), [make_pack()], {"title": "LangSplat", "method": "LangSplat"})
    first.merge(second)
    stats = first.stats()
    assert stats["node_types"]["method"] == 1

    path = tmp_path / "research_graph.json"
    first.save(path)
    restored = type(first).load(path)
    assert restored.to_dict()["nodes"] == first.to_dict()["nodes"]
    assert restored.to_dict()["edges"] == first.to_dict()["edges"]


def test_store_merges_graphs_across_runs(tmp_path):
    store = ResearchGraphStore(tmp_path, user_id="alice")
    state = make_state()
    first = ResearchGraphBuilder().build(state, [make_pack()], {"title": "LangSplat"})
    store.merge(first)
    second_state = make_state()
    second_state.findings.append(Finding(
        node_id="verify_data", claim="A second verified finding.", evidence="L20-L24"
    ))
    second = ResearchGraphBuilder().build(second_state, [], {})
    store.merge(second)

    merged = store.load()
    assert merged.stats()["node_types"]["user"] == 1
    assert merged.stats()["node_types"]["finding"] == 2
