"""Evidence-centric retrieval for PaperWise."""

from paperwise.evidence.grounding import CitationGroundingAuditor, GroundingReport
from paperwise.evidence.models import (
    EvidencePack,
    EvidenceSnippet,
    StructureType,
)
from paperwise.evidence.retriever import EvidenceRetriever
from paperwise.evidence.retriever import section_chunks

__all__ = [
    "CitationGroundingAuditor",
    "EvidencePack",
    "EvidenceSnippet",
    "EvidenceRetriever",
    "GroundingReport",
    "StructureType",
    "section_chunks",
]
