"""P4 Phase 2 — Action Planner：把研究机会转化为 Dynamic DAG 行动。

机会不是终点。Action Planner 把 ``Opportunity.suggested_actions`` 映射为
一个小型 DAG，复用 P2 收尾的受控可执行节点（``to_executable_plan``），
执行结果回写机会置信度（与 P3.5 的 outcome 回写同构）。

防递归：Action DAG 在 ``depth=1`` 运行，且不再级联触发机会检测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from paperwise.core.plan import Plan
from paperwise.opportunity.models import (
    OpportunityStatus,
    ResearchOpportunity,
)


#: suggested_action -> 可执行节点（受控映射，见 OPPORTUNITY_ENGINE_DESIGN.md）
ACTION_TO_NODE: dict[str, str] = {
    "verify_claim": "verify_data",
    "expand_evidence": "expand_evidence",
    "search_papers": "dynamic_research",
    "build_background": "dynamic_research",
    "compare_evidence": "dynamic_research",
    "compare_methods": "dynamic_research",
    "suggest_experiment": "dynamic_research",
}

#: 未知 action 的保守归宿
DEFAULT_ACTION_NODE = "dynamic_research"


@dataclass
class ActionResult:
    """一次机会行动的结果。"""

    opportunity_id: str
    success: bool
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    error_message: str = ""


class ActionPlanner:
    """把机会转化为可执行的 Dynamic DAG。"""

    def build_action_plan(self, opportunity: ResearchOpportunity) -> Plan:
        """为机会构建行动 DAG：read_paper -> 各 action 节点。

        所有 action 节点都依赖 read_paper（先建立论文事实基础）。
        未识别的 action 保守归入 dynamic_research。
        """
        plan = Plan()
        plan.add("Read paper and extract facts", task_id="read_paper")

        seen_nodes: set[str] = set()
        for action in opportunity.suggested_actions:
            node_id = ACTION_TO_NODE.get(action, DEFAULT_ACTION_NODE)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            plan.add(
                f"[{opportunity.type.value}] {action}: {opportunity.title[:50]}",
                task_id=node_id,
                depends_on=["read_paper"],
                max_retries=1,
            )
        return plan

    def to_executable(self, plan: Plan) -> Plan:
        """复用 P2 收尾的受控适配层，保证行动 DAG 只含已注册 handler 的节点。"""
        # 延迟导入避免与 memory.research_state 的循环依赖
        from paperwise.orchestration.dynamic_planner import to_executable_plan
        return to_executable_plan(plan)

    def mark_acting(self, opportunity: ResearchOpportunity) -> None:
        opportunity.status = OpportunityStatus.ACTING

    def write_back(self, opportunity: ResearchOpportunity, result: ActionResult) -> None:
        """把行动结果回写到机会：状态推进 + 置信度调整。"""
        if result.success:
            opportunity.status = OpportunityStatus.ACTED
            opportunity.confidence = min(1.0, round(opportunity.confidence + 0.1, 3))
        else:
            # 行动失败：回到 pending 并降置信度，避免反复触发
            opportunity.status = OpportunityStatus.PENDING
            opportunity.confidence = max(0.0, round(opportunity.confidence - 0.15, 3))
