"""Tests for V2 memory system: Episodic, Procedural, ResearchState, ContextEngine, ProactiveEngine."""

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.memory.user_memory import UserMemory
from paperwise.memory.episodic_memory import EpisodicMemory, Episode
from paperwise.memory.procedural_memory import ProceduralMemory, ProceduralPattern
from paperwise.memory.research_state import ResearchState, ResearchStateManager, KnowledgeGap
from paperwise.memory.context_engine import ContextEngine
from paperwise.memory.proactive_engine import ProactiveEngine, ProactivePolicy


@pytest.fixture
def tmp_user(tmp_path):
    return tmp_path / "user"


def test_profile_memory_lifecycle(tmp_user):
    mem = UserMemory(tmp_user / "mem", user_id="u1")
    card = mem.remember("preference", {"research_fields": "3DGS"}, confidence=0.9, source="explicit")
    assert card.source == "explicit"
    assert card.status == "active"
    assert card.user_id == "u1"

    mem.update_status(card.card_id, "archived")
    assert mem.recall(card.card_id).status == "archived"

    mem.apply_feedback(card.card_id, 0.05)
    assert mem.recall(card.card_id).confidence > 0.9


def test_episodic_memory(tmp_user):
    em = EpisodicMemory(tmp_user / "episodes", user_id="u1")
    ep = em.record(
        task_type="paper_analysis",
        goal="analyze feature 3dgs",
        entities=["feature3dgs"],
        findings=["uses feature splatting"],
        outcome="completed",
    )
    assert ep.episode_id.startswith("ep_")
    assert em.get(ep.episode_id).outcome == "completed"

    results = em.query(task_type="paper_analysis", entity="feature3dgs")
    assert len(results) == 1


def test_procedural_memory(tmp_user):
    pm = ProceduralMemory(tmp_user / "procedures", user_id="u1")
    pat = pm.learn(
        task_type="report",
        preferred_steps=["method", "experiment", "limitation"],
        preferences={"style": "concise"},
        success=True,
    )
    assert pat.task_type == "report"

    # Update same pattern
    pat2 = pm.learn(
        task_type="report",
        preferred_steps=["method", "experiment", "limitation"],
        preferences={"style": "concise"},
        success=True,
    )
    assert pat2.pattern_id == pat.pattern_id
    assert pat2.use_count == 2


def test_research_state_manager(tmp_user):
    rsm = ResearchStateManager(tmp_user / "rs", user_id="u1")
    state = rsm.new("analyze paper X")
    state.intent = "analysis"
    rsm.update(state)

    rsm.add_gap("missing ablation", urgency="high", suggested_action="search arxiv")
    loaded = rsm.get()
    assert loaded is not None
    assert len(loaded.gaps) == 1
    assert loaded.gaps[0].urgency == "high"

    rsm.close_gap(loaded.gaps[0].gap_id)
    loaded = rsm.get()
    assert len(loaded.gaps) == 0


def test_context_engine_assembles(tmp_user):
    um = UserMemory(tmp_user / "mem", user_id="u1")
    um.remember("preference", {"research_fields": "3DGS"}, confidence=0.9)
    em = EpisodicMemory(tmp_user / "episodes", user_id="u1")
    em.record(task_type="analysis", goal="analyze 3dgs", entities=["p1"], outcome="completed")
    pm = ProceduralMemory(tmp_user / "procedures", user_id="u1")
    pm.learn(task_type="analysis", preferred_steps=["read", "analyze"])

    ctx = ContextEngine(tmp_user, user_id="u1", user_memory=um, episodic_memory=em, procedural_memory=pm)
    state = ResearchState(state_id="s1", user_id="u1", current_task="analyze p1", intent="analysis")
    pkg = ctx.assemble(state, hierarchical_memory=None, top_k=3)

    assert len(pkg.profile) == 1
    assert len(pkg.episodes) == 1
    assert len(pkg.procedures) == 1
    xml = pkg.to_xml()
    assert "<profile>" in xml


def test_proactive_engine_policy(tmp_user):
    """ProactiveEngine should return empty when score is below threshold."""
    pe = ProactiveEngine(tmp_user, user_id="u1")
    state = ResearchState(state_id="s1", user_id="u1", current_task="foo")
    recs = asyncio.run(pe.decide(state, focus_mode=False))
    assert recs == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
