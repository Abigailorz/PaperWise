"""Tests for P4 Phase 3 — Opportunity Surfacer（相关研究时 surfaced pending 机会）。"""

from paperwise.opportunity import (
    OpportunityStatus,
    OpportunitySurfacer,
    OpportunityType,
    ResearchOpportunity,
    EvidenceRef,
)


def _opp(title, entities, status=OpportunityStatus.PENDING, confidence=0.8):
    return ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title=title,
        description=title,
        confidence=confidence,
        evidence=[EvidenceRef(source_type="knowledge_gap", source_id="g", excerpt=title)],
        related_entities=entities,
        status=status,
    )


def test_surface_relevant_pending():
    surfacer = OpportunitySurfacer()
    related = _opp("uncertainty estimation for gaussian splatting", ["uncertainty"])
    unrelated = _opp("protein folding database", ["protein"])
    opps = [related, unrelated]

    surfaced = surfacer.surface("novel view synthesis with gaussian splatting", opps)
    assert related in surfaced
    assert unrelated not in surfaced


def test_surface_excludes_non_pending():
    surfacer = OpportunitySurfacer()
    acted = _opp("gaussian splatting uncertainty", ["gaussian"], status=OpportunityStatus.ACTED)
    dismissed = _opp("gaussian splatting pruning", ["gaussian"], status=OpportunityStatus.DISMISSED)

    surfaced = surfacer.surface("gaussian splatting rendering", [acted, dismissed])
    assert surfaced == []


def test_surface_respects_limit_and_ranking():
    surfacer = OpportunitySurfacer(limit=2)
    opps = [
        _opp("gaussian splatting method", ["gaussian"], confidence=0.9),
        _opp("gaussian splatting rendering", ["gaussian"], confidence=0.6),
        _opp("gaussian splatting optimization", ["gaussian"], confidence=0.5),
    ]
    surfaced = surfacer.surface("gaussian splatting novel view synthesis", opps)
    assert len(surfaced) <= 2
    # 置信度高的排前面
    assert surfaced[0].confidence >= surfaced[-1].confidence


def test_surface_note_format():
    surfacer = OpportunitySurfacer()
    opp = _opp("knowledge gap about DINOv2", ["dinov2"], confidence=0.75)
    note = surfacer.surface_note([opp])
    assert "pending" in note
    assert "DINOv2" in note
    assert "0.75" in note
    assert surfacer.surface_note([]) == ""


def test_surface_empty_task_or_opps():
    surfacer = OpportunitySurfacer()
    assert surfacer.surface("", [_opp("x", ["x"])]) == []
    assert surfacer.surface("some task", []) == []
