"""Tests for P4 Phase 2 — Action Planner（机会 -> Dynamic DAG 行动）。"""

from paperwise.core.plan import Plan
from paperwise.opportunity import (
    ActionPlanner,
    ActionResult,
    OpportunityStatus,
    OpportunityType,
    ResearchOpportunity,
    EvidenceRef,
)
from paperwise.opportunity.action_planner import ACTION_TO_NODE, DEFAULT_ACTION_NODE
from paperwise.orchestration.dynamic_planner import (
    EXECUTABLE_NODE_IDS,
    DynamicDAGPlanner,
)


def _opp(actions, confidence=0.8) -> ResearchOpportunity:
    return ResearchOpportunity(
        type=OpportunityType.MISSING_EVIDENCE,
        title="证据不足：某论断",
        description="d",
        confidence=confidence,
        evidence=[EvidenceRef(source_type="reviewer_claim", source_id="0", excerpt="q")],
        related_entities=["claim x"],
        suggested_actions=actions,
    )


def test_build_action_plan_starts_with_read_paper():
    planner = ActionPlanner()
    plan = planner.build_action_plan(_opp(["verify_claim"]))
    assert plan.tasks[0].id == "read_paper"
    verify = plan.get("verify_data")
    assert verify is not None
    assert verify.depends_on == ["read_paper"]


def test_action_plan_dedups_and_maps_unknown():
    planner = ActionPlanner()
    # verify_claim 与 expand_evidence 重复触发 verify/expand；未知 action 归 dynamic_research
    plan = planner.build_action_plan(_opp(["verify_claim", "verify_claim", "totally_unknown"]))
    ids = [t.id for t in plan.tasks]
    assert ids.count("verify_data") == 1
    assert DEFAULT_ACTION_NODE in ids  # totally_unknown -> dynamic_research


def test_all_action_nodes_are_executable():
    """行动 DAG 经适配层后只含受控可执行节点。"""
    planner = ActionPlanner()
    all_actions = list(ACTION_TO_NODE.keys())
    plan = planner.to_executable(planner.build_action_plan(_opp(all_actions)))
    assert plan.tasks
    assert all(t.id in EXECUTABLE_NODE_IDS for t in plan.tasks)
    assert DynamicDAGPlanner.is_topologically_valid(plan)


def test_write_back_success_advances_status():
    planner = ActionPlanner()
    opp = _opp(["verify_claim"], confidence=0.8)
    planner.mark_acting(opp)
    assert opp.status == OpportunityStatus.ACTING

    planner.write_back(opp, ActionResult(opportunity_id=opp.opportunity_id, success=True))
    assert opp.status == OpportunityStatus.ACTED
    assert opp.confidence > 0.8  # 成功提升置信度


def test_write_back_failure_returns_to_pending_and_demotes():
    planner = ActionPlanner()
    opp = _opp(["verify_claim"], confidence=0.8)
    planner.mark_acting(opp)
    planner.write_back(opp, ActionResult(
        opportunity_id=opp.opportunity_id, success=False, error_message="boom"))
    assert opp.status == OpportunityStatus.PENDING
    assert opp.confidence < 0.8  # 失败降置信度，避免反复触发


def test_orchestrator_actions_disabled_by_default(tmp_path):
    """默认不自动执行机会行动（节省 token / 安全）。"""
    from paperwise.orchestration.orchestrator import SmartOrchestrator
    orch = SmartOrchestrator(llm_client=None, workspace=tmp_path)
    assert orch.enable_opportunity_actions is False
    assert orch.opportunity_act_threshold == 0.7


def test_act_on_opportunities_respects_threshold(tmp_path):
    """只有置信度 >= 阈值的 pending 机会才会被行动。"""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from paperwise.orchestration.orchestrator import SmartOrchestrator

    orch = SmartOrchestrator(
        llm_client=None, workspace=tmp_path, enable_opportunity_actions=True,
    )
    state = orch.research_state_manager.new(current_task="t")
    high = _opp(["verify_claim"], confidence=0.9)
    low = _opp(["verify_claim"], confidence=0.4)
    state.add_opportunity(high)
    state.add_opportunity(low)

    async def run():
        # 不真正跑 DAG，直接 stub executor.run
        with patch.object(orch, "_make_executor") as mk:
            executor = mk.return_value
            executor.run = AsyncMock(return_value={
                "success": True, "completed_nodes": ["verify_data"], "failed_nodes": [],
                "error_message": "",
            })
            return await orch._act_on_opportunities(state, tmp_path)

    results = asyncio.run(run())
    # 只有高置信机会被行动
    assert len(results) == 1
    assert results[0].opportunity_id == high.opportunity_id
    assert high.status == OpportunityStatus.ACTED
    assert low.status == OpportunityStatus.PENDING
