"""Tests for OrchestratorMemoryAdapter and memory-driven orchestration."""

import tempfile
from pathlib import Path

import pytest

from paperwise.core.plan import Plan
from paperwise.core.types import AgentResult
from paperwise.memory.research_state import ResearchState, KnowledgeGap
from paperwise.orchestration.memory_adapter import OrchestratorMemoryAdapter


@pytest.fixture
def adapter(tmp_path: Path):
    return OrchestratorMemoryAdapter(workspace=tmp_path, user_id="test")


def test_assemble_context_returns_package(adapter: OrchestratorMemoryAdapter):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="analyze method")
    pkg = adapter.assemble_context(state)
    assert pkg is not None
    assert "<context>" in pkg.to_xml()


def test_apply_gaps_to_plan_inserts_verify_data(adapter: OrchestratorMemoryAdapter):
    plan = Plan()
    plan.add("Read paper", task_id="read_paper")
    plan.add("Analyze method", task_id="analyze_method", depends_on=["read_paper"])
    plan.add("Generate report", task_id="generate_report", depends_on=["analyze_method"])

    state = ResearchState(state_id="rs_1", user_id="test", current_task="verify numbers")
    state.add_gap(
        description="numerical accuracy values need verification",
        node_id="analyze_method",
        urgency="high",
    )

    new_plan = adapter.apply_gaps_to_plan(plan, state)
    assert any(t.id == "verify_data" for t in new_plan.tasks)


def test_apply_gaps_to_plan_inserts_expand_evidence(adapter: OrchestratorMemoryAdapter):
    plan = Plan()
    plan.add("Read paper", task_id="read_paper")
    plan.add("Analyze method", task_id="analyze_method", depends_on=["read_paper"])
    plan.add("Generate report", task_id="generate_report", depends_on=["analyze_method"])

    state = ResearchState(state_id="rs_1", user_id="test", current_task="write report")
    state.add_gap(
        description="missing evidence and citations",
        node_id="generate_report",
        urgency="high",
    )

    new_plan = adapter.apply_gaps_to_plan(plan, state)
    assert any(t.id == "expand_evidence" for t in new_plan.tasks)


def test_apply_gaps_respects_urgency(adapter: OrchestratorMemoryAdapter):
    plan = Plan()
    plan.add("Read paper", task_id="read_paper")

    state = ResearchState(state_id="rs_1", user_id="test", current_task="task")
    state.add_gap(description="low priority gap", urgency="low")

    new_plan = adapter.apply_gaps_to_plan(plan, state)
    assert len(new_plan.tasks) == len(plan.tasks)


def test_update_state_from_execution(adapter: OrchestratorMemoryAdapter):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="task")
    state = adapter.update_state_from_execution(
        state,
        completed_nodes=["read_paper", "analyze_method"],
        failed_nodes=["verify_data"],
        gaps=[KnowledgeGap(gap_id="g1", node_id="verify_data", description="numbers mismatch")],
    )
    assert "read_paper" in state.completed_nodes
    assert "verify_data" in state.failed_nodes
    assert any(g.node_id == "verify_data" for g in state.gaps)
    assert state.dag_status == "completed_with_gaps"


def test_record_episode_does_not_raise(adapter: OrchestratorMemoryAdapter):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="task")
    result = AgentResult(final_output="done", success=True)
    adapter.record_episode(state, None, result)


def test_learn_procedure_does_not_raise(adapter: OrchestratorMemoryAdapter):
    plan = Plan()
    plan.add("Read paper", task_id="read_paper")
    adapter.learn_procedure("analysis", plan, success=True)
