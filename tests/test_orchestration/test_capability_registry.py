"""Tests for orchestration registries extensions."""

import pytest

from paperwise.orchestration.registries import (
    CapabilityRegistry,
    NodeRegistry,
    WorkflowRegistry,
)
from paperwise.orchestration.types import TaskRoute, TaskType, TaskComplexity


def test_capability_registry_find_for_report():
    reg = CapabilityRegistry()
    caps = reg.find_for_task("generate a report", required_output_artifacts=["ReportArtifact"])
    assert any(c.id == "paper_to_report" for c in caps)


def test_capability_registry_find_for_ppt():
    reg = CapabilityRegistry()
    caps = reg.find_for_task("make a presentation", required_output_artifacts=["SlideArtifact"])
    assert any(c.id == "paper_to_ppt" for c in caps)


def test_capability_registry_find_for_summary():
    reg = CapabilityRegistry()
    caps = reg.find_for_task("summarize this paper")
    assert any(c.id == "paper_summarize" for c in caps)


def test_capability_registry_resolve_nodes():
    reg = CapabilityRegistry()
    cap = reg.get("paper_to_report")
    node_registry = NodeRegistry()
    nodes = reg.resolve_nodes(cap, node_registry)
    assert "report_outline" in nodes
    assert "report_assemble" in nodes


def test_node_registry_filter_by_capabilities():
    reg = NodeRegistry()
    nodes = reg.filter_by_capabilities(["long_context"])
    ids = {n.id for n in nodes}
    assert "method_analysis" in ids
    assert "experiment_analysis" in ids


def test_node_registry_select_by_category():
    reg = NodeRegistry()
    nodes = reg.select_by_category("generation")
    assert any(n.id == "report_assemble" for n in nodes)


def test_workflow_registry_select_by_task_route():
    reg = WorkflowRegistry()
    route = TaskRoute(
        task_type=TaskType.RESEARCH,
        complexity=TaskComplexity.COMPLEX,
        workflow="paper_to_report",
        confidence="high",
        reason="report task",
    )
    wf = reg.select(route)
    assert wf is not None
    assert wf.id == "paper_to_report"


def test_workflow_registry_select_by_text():
    reg = WorkflowRegistry()
    route = TaskRoute(
        task_type=TaskType.RESEARCH,
        complexity=TaskComplexity.COMPLEX,
        workflow="unknown",
        confidence="low",
        reason="fallback",
    )
    wf = reg.select(route)
    # 没有 task_text 字段，select 应该返回 None
    assert wf is None
