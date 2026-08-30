from paperwise.evidence import EvidencePack, EvidenceSnippet, StructureType
from paperwise.opportunity.detector import OpportunityDetector
from paperwise.opportunity.evidence_bridge import EvidenceOpportunityBridge
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


def make_snippet(evidence_id, paper_id, content, low_score=0.0):
    return EvidenceSnippet(
        evidence_id=evidence_id,
        content=content,
        structure_type=StructureType.SECTION,
        paper_id=paper_id,
        start_line=1,
        end_line=4,
        score=low_score,
    )


def make_pack(query, snippets, low_recall=False):
    return EvidencePack(
        query=query,
        snippets=snippets,
        scope="library",
        retrieval_queries=[query],
        low_recall=low_recall,
    )


def test_low_recall_evidence_creates_missing_evidence_opportunity():
    bridge = EvidenceOpportunityBridge()
    pack = make_pack(
        "adaptive pruning for language gaussian splatting",
        [make_snippet("s1", "paper-a", "Adaptive pruning reduces memory use.")],
        low_recall=True,
    )
    opportunities = bridge.derive([pack])
    assert len(opportunities) == 1
    assert opportunities[0].type == OpportunityType.MISSING_EVIDENCE
    assert opportunities[0].evidence[0].source_id == "s1"
    assert "expand_evidence" in opportunities[0].suggested_actions


def test_cross_paper_evidence_creates_method_complementarity():
    bridge = EvidenceOpportunityBridge()
    pack = make_pack(
        "language gaussian splatting comparison",
        [
            make_snippet("current", "paper-a", "Language Gaussian Splatting uses adaptive density control."),
            make_snippet("related", "paper-b", "Language Gaussian Splatting uses uncertainty estimation."),
        ],
    )
    opportunities = bridge.derive([pack])
    assert opportunities
    opportunity = opportunities[0]
    assert opportunity.type == OpportunityType.METHOD_COMPLEMENTARITY
    assert {"paper-a", "paper-b"} <= set(opportunity.related_entities)
    assert opportunity.evidence


def test_bridge_attaches_relevant_grounding_to_existing_candidate():
    bridge = EvidenceOpportunityBridge()
    candidate = ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title="Missing uncertainty baseline",
        description="Uncertainty estimation is missing.",
        related_entities=["uncertainty estimation"],
    )
    pack = make_pack(
        "uncertainty estimation",
        [make_snippet("grounded", "paper-b", "Uncertainty estimation supports selective rendering.")],
    )
    enriched = bridge.attach([candidate], [pack])
    assert enriched[0].evidence[0].source_id == "grounded"


def test_detector_accepts_evidence_packs():
    detector = OpportunityDetector()
    opportunities = detector.detect(
        research_state=None,
        evidence_packs=[make_pack("unrelated query", [], low_recall=False)],
    )
    assert opportunities == []
