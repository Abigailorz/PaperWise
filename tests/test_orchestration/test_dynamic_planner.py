"""Tests for DynamicDAGPlanner."""

import pytest

from paperwise.core.plan import Plan
from paperwise.memory.research_state import ResearchState, KnowledgeGap
from paperwise.orchestration.dynamic_planner import DynamicDAGPlanner, PlanCompositionPolicy
from paperwise.orchestration.registries import CapabilityRegistry, NodeRegistry
from paperwise.orchestration.types import TaskRoute


@pytest.fixture
def planner():
    return DynamicDAGPlanner(
        capability_registry=CapabilityRegistry(),
        node_registry=NodeRegistry(),
    )


def test_build_plan_for_report(planner: DynamicDAGPlanner):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="generate report")
    route = TaskRoute(
        task_type="analysis",
        complexity="complex",
        workflow="paper_to_report",
        confidence=0.9,
        reason="report",
    )
    plan = planner.build_plan("generate a detailed report", route, state)
    assert planner.is_topologically_valid(plan)
    assert plan.tasks
    assert any(t.id == "report_assemble" for t in plan.tasks)


def test_build_plan_for_ppt(planner: DynamicDAGPlanner):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="generate ppt")
    route = TaskRoute(
        task_type="analysis",
        complexity="complex",
        workflow="paper_to_ppt",
        confidence=0.9,
        reason="ppt",
    )
    plan = planner.build_plan("make a presentation", route, state)
    assert planner.is_topologically_valid(plan)
    assert any(t.id == "ppt_assemble" for t in plan.tasks)


def test_build_plan_adds_gap_recovery_nodes(planner: DynamicDAGPlanner):
    state = ResearchState(state_id="rs_1", user_id="test", current_task="generate report")
    state.add_gap(
        description="numerical accuracy values need verification",
        node_id="experiment_analysis",
        urgency="high",
    )
    route = TaskRoute(
        task_type="analysis",
        complexity="complex",
        workflow="paper_to_report",
        confidence=0.9,
        reason="report",
    )
    plan = planner.build_plan("generate a report", route, state)
    assert any(t.id == "re_verify_with_code" for t in plan.tasks)


def test_topological_validity_detects_missing_dependency():
    plan = Plan()
    plan.add("Task A", task_id="a", depends_on=["missing"])
    assert not DynamicDAGPlanner.is_topologically_valid(plan)


def test_topological_validity_detects_self_dependency():
    plan = Plan()
    plan.add("Task A", task_id="a", depends_on=["a"])
    assert not DynamicDAGPlanner.is_topologically_valid(plan)


def test_plan_merge():
    plan1 = Plan()
    plan1.add("Read", task_id="read")
    plan2 = Plan()
    plan2.add("Verify", task_id="verify", depends_on=["read"])
    plan1.merge(plan2)
    assert any(t.id == "verify" for t in plan1.tasks)
    assert len(plan1.tasks) == 2


def test_plan_to_dependency_graph():
    plan = Plan()
    plan.add("Read", task_id="read")
    plan.add("Analyze", task_id="analyze", depends_on=["read"])
    graph = plan.to_dependency_graph()
    assert graph == {"read": [], "analyze": ["read"]}
