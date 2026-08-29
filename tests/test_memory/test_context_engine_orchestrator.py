"""Tests for ContextEngine orchestrator-facing extensions."""

import pytest
from pathlib import Path

from paperwise.memory.context_engine import ContextEngine, ContextPackage
from paperwise.memory.research_state import ResearchState


def test_context_package_size_and_truncate(tmp_path: Path):
    pkg = ContextPackage(
        profile=[{"category": "domain", "data": "3D vision", "confidence": 0.9, "source": "user"}],
        paper_context=[{"doc_id": "p1", "text": "x" * 500} for _ in range(10)],
    )
    assert pkg.size() > 0
    truncated = pkg.truncate(300)
    assert truncated.size() <= 300
    # paper_context should be truncated first
    assert len(truncated.paper_context) < len(pkg.paper_context)


def test_context_package_for_node_filters_episodes(tmp_path: Path):
    pkg = ContextPackage(
        profile=[{"category": "domain", "data": "3D vision", "confidence": 0.9, "source": "user"}],
        episodes=[{"goal": "past task", "findings": "foo", "outcome": "success"}],
        paper_context=[{"doc_id": "p1", "text": "method section"}],
    )
    method_pkg = pkg.for_node("analyze_method")
    assert len(method_pkg.episodes) == 0
    assert len(method_pkg.paper_context) == 1


def test_context_package_for_node_filters_paper_context(tmp_path: Path):
    pkg = ContextPackage(
        profile=[{"category": "domain", "data": "3D vision", "confidence": 0.9, "source": "user"}],
        episodes=[{"goal": "past task", "findings": "foo", "outcome": "success"}],
        paper_context=[{"doc_id": "p1", "text": "method section"}],
    )
    report_pkg = pkg.for_node("generate_report")
    assert len(report_pkg.paper_context) == 0
    assert len(report_pkg.episodes) == 1


def test_assemble_for_subagent_truncates(tmp_path: Path):
    engine = ContextEngine(workspace=tmp_path, user_id="test")
    state = ResearchState(state_id="rs_1", user_id="test", current_task="analyze method")
    pkg = engine.assemble_for_subagent("analyze_method", state, max_chars=2000)
    assert pkg.size() <= 2000
