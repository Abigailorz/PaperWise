"""Tests for dynamic-plan -> executable-plan adaptation (P2 收尾).

原则：Node Capability 受控（只含已注册 handler 的节点），Graph Composition 动态。
"""

from unittest.mock import patch

from paperwise.core.plan import Plan
from paperwise.orchestration.dynamic_planner import (
    DynamicDAGPlanner,
    executable_id_for,
    to_executable_plan,
)


def test_executable_id_for_known_registry_nodes():
    assert executable_id_for("parse_pdf") == "read_paper"
    assert executable_id_for("method_analysis") == "analyze_method"
    assert executable_id_for("report_assemble") == "generate_report"
    assert executable_id_for("ppt_assemble") == "generate_pptx"
    assert executable_id_for("critic") == "review_report"


def test_executable_id_for_passthrough_and_fallback():
    # 已可执行的节点原样透传（含 ReplanAgent 纠正节点）
    assert executable_id_for("read_paper") == "read_paper"
    assert executable_id_for("re_verify_with_code") == "re_verify_with_code"
    # 完全未知的节点保守归入 analyze_method
    assert executable_id_for("totally_unknown") == "analyze_method"


def test_dynamic_report_plan_collapses_to_executable_pipeline():
    planner = DynamicDAGPlanner()
    plan = planner.build_plan("generate a report", None, _empty_state())
    exec_plan = to_executable_plan(plan)

    ids = [t.id for t in exec_plan.tasks]
    assert ids == [
        "read_paper", "analyze_method", "generate_report",
        "review_report", "revise_report",
    ]
    # 依赖链在可执行空间正确重建
    by_id = {t.id: t for t in exec_plan.tasks}
    assert by_id["read_paper"].depends_on == []
    assert by_id["analyze_method"].depends_on == ["read_paper"]
    assert by_id["generate_report"].depends_on == ["analyze_method"]
    assert by_id["review_report"].depends_on == ["generate_report"]
    assert DynamicDAGPlanner.is_topologically_valid(exec_plan)


def test_dynamic_ppt_plan_gates_generate_pptx():
    planner = DynamicDAGPlanner()
    plan = planner.build_plan("make a presentation", None, _empty_state())
    exec_plan = to_executable_plan(plan)

    pptx = exec_plan.get("generate_pptx")
    assert pptx is not None
    assert pptx.condition == "requires_pptx"


def test_executable_plan_has_no_duplicate_nodes():
    plan = Plan()
    plan.add("parse", task_id="parse_pdf")
    plan.add("extract", task_id="extract_text", depends_on=["parse_pdf"])
    plan.add("problem", task_id="problem_analysis", depends_on=["extract_text"])
    plan.add("method", task_id="method_analysis", depends_on=["extract_text"])
    plan.add("synthesis", task_id="synthesis",
             depends_on=["problem_analysis", "method_analysis"])

    exec_plan = to_executable_plan(plan)
    ids = [t.id for t in exec_plan.tasks]
    assert len(ids) == len(set(ids))
    assert ids == ["read_paper", "analyze_method"]
    assert DynamicDAGPlanner.is_topologically_valid(exec_plan)


def test_executable_plan_preserves_passthrough_fields():
    plan = Plan()
    plan.add("Read", task_id="read_paper", max_retries=2)
    plan.add("Custom correction", task_id="re_verify_with_code",
             depends_on=["read_paper"], max_retries=1)

    exec_plan = to_executable_plan(plan)
    correction = exec_plan.get("re_verify_with_code")
    assert correction.description == "Custom correction"
    assert correction.max_retries == 1
    assert correction.depends_on == ["read_paper"]


def test_orchestrator_defaults_to_dynamic_main_path(tmp_path):
    from paperwise.core.agent import AgentConfig
    from paperwise.orchestration.orchestrator import SmartOrchestrator

    orchestrator = SmartOrchestrator(llm_client=None, workspace=tmp_path)
    assert orchestrator.plan_policy.use_dynamic_plan is True


def test_orchestrator_select_plan_returns_executable_nodes(tmp_path):
    from paperwise.orchestration.orchestrator import SmartOrchestrator

    orchestrator = SmartOrchestrator(llm_client=None, workspace=tmp_path)
    research_state = orchestrator.research_state_manager.new(current_task="generate a report")
    plan = orchestrator._select_plan("generate a report", None, research_state)

    handlers = orchestrator._handler_map()
    assert plan.tasks
    assert all(t.id in handlers for t in plan.tasks)
    assert DynamicDAGPlanner.is_topologically_valid(plan)
    # 动态主路径折叠出的 report 流水线
    assert "generate_report" in [t.id for t in plan.tasks]


def test_orchestrator_falls_back_to_static_when_dynamic_empty(tmp_path):
    from paperwise.orchestration.orchestrator import SmartOrchestrator

    orchestrator = SmartOrchestrator(llm_client=None, workspace=tmp_path)
    research_state = orchestrator.research_state_manager.new(current_task="generate a report")

    with patch.object(
        orchestrator.dynamic_planner, "build_plan", return_value=Plan()
    ):
        plan = orchestrator._select_plan("generate a report", None, research_state)

    # 静态 safety net 的固定节点
    static_ids = {t.id for t in orchestrator._build_complex_plan("generate a report").tasks}
    assert {t.id for t in plan.tasks} == static_ids


def _empty_state():
    from paperwise.memory.research_state import ResearchState
    return ResearchState(state_id="test", user_id="test", current_task="test")
