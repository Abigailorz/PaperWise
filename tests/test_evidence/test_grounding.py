from paperwise.evidence import EvidencePack, EvidenceSnippet, StructureType
from paperwise.evidence.grounding import CitationGroundingAuditor


def make_pack(start=1, end=4) -> EvidencePack:
    snippet = EvidenceSnippet(
        evidence_id="section:paper:1",
        content="The method reaches 92.3 accuracy.",
        structure_type=StructureType.SECTION,
        paper_id="paper",
        start_line=start,
        end_line=end,
    )
    return EvidencePack(query="accuracy", snippets=[snippet])


def test_grounded_claim_passes():
    auditor = CitationGroundingAuditor()
    report = auditor.audit("Accuracy improved [source: paper/text.md L1-L4].", [make_pack()])
    assert report.grounded_claims == 1
    assert report.citation_coverage == 1.0
    assert report.evidence_coverage == 1.0
    assert report.grounding_score == 1.0


def test_missing_citation_fails():
    auditor = CitationGroundingAuditor()
    report = auditor.audit("Accuracy improved without evidence.", [make_pack()])
    assert report.citation_coverage == 0.0
    assert report.evidence_coverage == 0.0
    assert report.grounding_score == 0.0
