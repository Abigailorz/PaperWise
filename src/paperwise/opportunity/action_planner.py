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
from paperwise.opportunity.action import (
    ACTION_RISK_LEVELS,
    ActionStatus,
    ActionRisk,
    ActionStatus,
    ActionType,
    ResearchAction,
)
from paperwise.opportunity.models import (
    OpportunityStatus,
    OpportunityType,
    ResearchOpportunity,
)


#: suggested_action -> 可执行节点（受控映射，见 OPPORTUNITY_ENGINE_DESIGN.md）
ACTION_TO_NODE: dict[str, str] = {
    "retrieve_evidence": "expand_evidence",
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


#: OpportunityType -> (primary Action, secondary Action or None)
#: Deterministic mapping; LLM cannot change this policy.
OPPORTUNITY_TO_ACTIONS: dict[OpportunityType, tuple[ActionType, ...]] = {
    OpportunityType.KNOWLEDGE_GAP: (
        ActionType.RETRIEVE_EVIDENCE,
        ActionType.ANALYZE_GAP,
    ),
    OpportunityType.MISSING_EVIDENCE: (
        ActionType.RETRIEVE_EVIDENCE,
        ActionType.VERIFY_CLAIM,
    ),
    OpportunityType.CONTRADICTION: (
        ActionType.RETRIEVE_EVIDENCE,
        ActionType.VERIFY_CLAIM,
    ),
    OpportunityType.METHOD_COMPLEMENTARITY: (
        ActionType.SEARCH_RELATED_WORK,
        ActionType.COMPARE_METHODS,
    ),
}


@dataclass
class ActionResult:
    """一次机会行动的结果。"""

    opportunity_id: str
    success: bool
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    error_message: str = ""


class ActionPlanner:
    """Map opportunities to formal ResearchActions, then to executable DAG."""

    def plan_actions(
        self,
        opportunities: list[ResearchOpportunity],
        research_state: Any,
        max_actions: int = 3,
    ) -> list[ResearchAction]:
        """Convert pending opportunities into bounded ResearchActions.

        Deterministic: same input -> same output. Action Budget is enforced
        (max_actions per round). LLM parameterizes objective text but never
        changes the action type or bypasses constraints.
        """
        actions: list[ResearchAction] = []
        for opp in opportunities:
            if opp.status != OpportunityStatus.PENDING:
                continue
            existing_pending = {
                existing.opportunity_id for existing in research_state.pending_actions
                if existing.status in (ActionStatus.PENDING, ActionStatus.APPROVED, ActionStatus.RUNNING)
            }
            if opp.opportunity_id in existing_pending:
                continue
            action_types = OPPORTUNITY_TO_ACTIONS.get(opp.type, (ActionType.RETRIEVE_EVIDENCE,))
            for action_type in action_types:
                if len(actions) >= max_actions:
                    return actions
                action = ResearchAction(
                    opportunity_id=opp.opportunity_id,
                    action_type=action_type,
                    objective=f"[{opp.type.value}] {opp.title}: {opp.description[:120]}",
                    required_capabilities=self._capabilities_for(action_type),
                    input_refs=[f"opportunity:{opp.opportunity_id}"],
                    expected_outputs=self._outputs_for(action_type),
                    priority=opp.confidence * opp.importance,
                    confidence=opp.confidence,
                    risk_level=ACTION_RISK_LEVELS.get(action_type, ActionRisk.LOW),
                    status=ActionStatus.PENDING,
                    requires_user_approval=(
                        ACTION_RISK_LEVELS.get(action_type, ActionRisk.LOW) != ActionRisk.LOW
                    ),
                )
                actions.append(action)
        return actions

    def actions_to_dag(self, actions: list[ResearchAction]) -> Plan:
        """Convert approved/auto-executable actions into a controlled Plan."""
        plan = Plan()
        plan.add("Read paper and extract facts", task_id="read_paper")
        seen_nodes: set[str] = set()
        for action in actions:
            if not action.is_auto_executable:
                continue
            node_id = ACTION_TO_NODE.get(action.action_type.value, DEFAULT_ACTION_NODE)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            plan.add(
                f"[{action.action_type.value}] {action.objective[:60]}",
                task_id=node_id,
                depends_on=["read_paper"],
                max_retries=1,
            )
        return plan

    @staticmethod
    def _capabilities_for(action_type: ActionType) -> list[str]:
        caps: dict[ActionType, list[str]] = {
            ActionType.RETRIEVE_EVIDENCE: ["evidence_retriever", "knowledge_base"],
            ActionType.VERIFY_CLAIM: ["code_interpreter", "grep"],
            ActionType.COMPARE_METHODS: ["dynamic_research", "evidence_retriever"],
            ActionType.ANALYZE_GAP: ["analyze_method", "evidence_retriever"],
            ActionType.SEARCH_RELATED_WORK: ["dynamic_research", "recommender"],
            ActionType.GENERATE_HYPOTHESIS: ["llm"],
            ActionType.DESIGN_EXPERIMENT: ["llm"],
            ActionType.ASK_USER: ["ask_user"],
        }
        return caps.get(action_type, ["dynamic_research"])

    @staticmethod
    def _outputs_for(action_type: ActionType) -> list[str]:
        outs: dict[ActionType, list[str]] = {
            ActionType.RETRIEVE_EVIDENCE: ["evidence/evidence_pack.json"],
            ActionType.VERIFY_CLAIM: ["verified.json"],
            ActionType.COMPARE_METHODS: ["findings/comparison.json"],
            ActionType.ANALYZE_GAP: ["findings/gap_analysis.json"],
            ActionType.SEARCH_RELATED_WORK: ["related_papers.json"],
            ActionType.GENERATE_HYPOTHESIS: ["hypotheses.json"],
            ActionType.DESIGN_EXPERIMENT: ["experiment_design.json"],
            ActionType.ASK_USER: ["user_response"],
        }
        return outs.get(action_type, [])

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
