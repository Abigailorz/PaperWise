"""P6 Phase D tests: ResearchNarrative."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paperwise.generators.narrative import NarrativeSection, ResearchNarrative
from paperwise.memory.research_state import ResearchState


def _state() -> ResearchState:
    state = ResearchState(state_id="s1", user_id="default", current_task="Analyze paper")
    state.current_paper = "/tmp/test_paper"
    state.add_finding_from_node("n1", "Method A achieves 95% accuracy", evidence="Table 1, L100", confidence=0.9)
    state.add_finding_from_node("n2", "Method B is 10x faster", evidence="Table 2, L200", confidence=0.85)
    return state


class TestResearchNarrative:
    def test_build_from_state(self):
        narrative = ResearchNarrative.build(_state())
        assert len(narrative.sections) == 2
        assert narrative.sections[0].claim == "Method A achieves 95% accuracy"
        assert narrative.sections[0].confidence == 0.9

    def test_build_with_evidence_and_facts(self):
        from paperwise.evidence.models import EvidencePack, EvidenceSnippet, StructureType
        pack = EvidencePack(query="test")
        pack.snippets.append(EvidenceSnippet(
            evidence_id="ev1",
            structure_type=StructureType.SECTION,
            paper_id="test_paper",
            section="Method",
            content="The proposed method uses X",
            start_line=100,
            end_line=120,
        ))
        facts = {"title": "Test Paper", "method": "A novel approach"}
        narrative = ResearchNarrative.build(_state(), pack, facts)
        assert narrative.paper_title == "Test Paper"
        assert len(narrative.evidence_snippets) == 1
        assert narrative.evidence_snippets[0]["section"] == "Method"

    def test_to_prompt_context(self):
        narrative = ResearchNarrative.build(_state())
        context = narrative.to_prompt_context()
        assert "Verified Findings" in context
        assert "Method A achieves 95% accuracy" in context

    def test_save_and_load(self, tmp_path):
        narrative = ResearchNarrative.build(_state())
        path = tmp_path / "narrative.json"
        narrative.save(path)
        loaded = ResearchNarrative.from_dict(json.loads(path.read_text()))
        assert len(loaded.sections) == 2
        assert loaded.sections[1].claim == "Method B is 10x faster"

    def test_empty_state(self):
        state = ResearchState(state_id="s", user_id="d")
        narrative = ResearchNarrative.build(state)
        assert len(narrative.sections) == 0
        context = narrative.to_prompt_context()
        assert "Research Narrative" in context
