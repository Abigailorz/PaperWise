"""Dynamic DAG Planner.

根据 task、classifier route 和 research state，从 CapabilityRegistry / NodeRegistry
动态选择 capability 并组合成节点 DAG。保留静态 DAG 作为 fallback。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from paperwise.core.plan import Plan, Task
from paperwise.memory.research_state import ResearchState, KnowledgeGap
from paperwise.orchestration.registries import CapabilityRegistry, NodeRegistry
from paperwise.orchestration.types import TaskRoute


@dataclass
class PlanCompositionPolicy:
    """决定何时使用动态 Plan。"""

    use_dynamic_plan: bool = False
    # 当 task 明确包含这些关键词时，即使 use_dynamic_plan=False 也尝试动态规划
    force_dynamic_keywords: tuple[str, ...] = ("dynamic", "custom", "adaptive")


class DynamicDAGPlanner:
    """基于 capability 和 registry 动态构建 Plan。"""

    def __init__(
        self,
        capability_registry: Optional[CapabilityRegistry] = None,
        node_registry: Optional[NodeRegistry] = None,
    ):
        self.capability_registry = capability_registry or CapabilityRegistry()
        self.node_registry = node_registry or NodeRegistry()

    def build_plan(
        self,
        task: str,
        task_route: TaskRoute,
        research_state: ResearchState,
        policy: Optional[PlanCompositionPolicy] = None,
    ) -> Plan:
        """构建动态 Plan。

        流程：
        1. 快速路径：根据 task 关键词识别期望输出 artifacts
        2. 选择 capability
        3. 将 capability 展开为节点
        4. 拓扑排序并生成 Plan
        5. 若 research_state 有 gaps，追加恢复节点
        """
        policy = policy or PlanCompositionPolicy()

        # 1. 识别期望输出
        output_artifacts = self._infer_output_artifacts(task, task_route)

        # 2. 选择 capability
        capabilities = self.capability_registry.find_for_task(
            task, required_output_artifacts=output_artifacts
        )

        # 3. 展开为节点 id 列表
        node_ids: list[str] = []
        for cap in capabilities:
            nodes = self.capability_registry.resolve_nodes(cap, self.node_registry)
            for nid in nodes:
                if nid not in node_ids:
                    node_ids.append(nid)

        # 4. 构建拓扑合法的 Plan
        plan = self._compose_plan(node_ids)

        # 5. 根据 gaps 追加恢复节点
        plan = self._add_gap_recovery_nodes(plan, research_state)

        return plan

    def _infer_output_artifacts(self, task: str, task_route: TaskRoute) -> list[str]:
        """根据 task 文本和 route 推断期望输出 artifacts。"""
        task_lower = task.lower()
        artifacts = []
        if any(k in task_lower for k in ("ppt", "pptx", "presentation", "slides")):
            artifacts.append("SlideArtifact")
        if any(k in task_lower for k in ("report", "write", "generate", "markdown")):
            artifacts.append("ReportArtifact")
        if not artifacts and getattr(task_route, "workflow", None) == "paper_to_ppt":
            artifacts.append("SlideArtifact")
        if not artifacts and getattr(task_route, "workflow", None) == "paper_to_report":
            artifacts.append("ReportArtifact")
        if not artifacts:
            artifacts.append("ReportArtifact")
        return artifacts

    def _compose_plan(self, node_ids: list[str]) -> Plan:
        """根据节点依赖关系构建 Plan。

        简化策略：
        - parse_pdf 类 input 节点无依赖
        - extraction 节点依赖 parse_pdf
        - research 节点并行依赖 extraction
        - reasoning 节点依赖 research
        - generation 节点依赖 reasoning
        - verification 节点依赖 generation
        """
        plan = Plan()

        # 按 category 分组
        categories = {}
        for nid in node_ids:
            node = self.node_registry.get(nid)
            cat = node.category if node else "general"
            categories.setdefault(cat, []).append(nid)

        # 固定优先级：input -> extraction -> research -> reasoning -> generation -> verification
        priority = {
            "input": 0,
            "extraction": 1,
            "research": 2,
            "reasoning": 3,
            "generation": 4,
            "verification": 5,
            "general": 6,
        }
        sorted_cats = sorted(categories.keys(), key=lambda c: priority.get(c, 99))

        last_by_cat: dict[str, str] = {}
        previous_cat: Optional[str] = None

        for cat in sorted_cats:
            ids = categories[cat]
            deps: list[str] = []
            if previous_cat and previous_cat in last_by_cat:
                deps = [last_by_cat[previous_cat]]

            # 同一 category 内若多个节点，设为并行组
            parallel_group = f"group_{cat}" if len(ids) > 1 else None
            for nid in ids:
                node = self.node_registry.get(nid)
                description = node.description if node else f"Run {nid}"
                plan.add(
                    description=description,
                    task_id=nid,
                    depends_on=deps,
                    parallel_group=parallel_group,
                )
            if ids:
                last_by_cat[cat] = ids[-1]
            previous_cat = cat

        return plan

    def _add_gap_recovery_nodes(self, plan: Plan, research_state: ResearchState) -> Plan:
        """根据 high priority gaps 在 Plan 末尾追加恢复节点。"""
        gaps = research_state.get_high_priority_gaps(limit=2)
        if not gaps:
            return plan

        existing_ids = {t.id for t in plan.tasks}
        last_id = plan.tasks[-1].id if plan.tasks else None

        for gap in gaps:
            corrective_id = self._gap_to_node_id(gap)
            if corrective_id in existing_ids:
                continue
            deps = [last_id] if last_id else []
            plan.add(
                description=f"Address gap: {gap.description}",
                task_id=corrective_id,
                depends_on=deps,
                max_retries=1,
            )
            existing_ids.add(corrective_id)
            last_id = corrective_id

        return plan

    @staticmethod
    def _gap_to_node_id(gap: KnowledgeGap) -> str:
        desc = gap.description.lower()
        if any(k in desc for k in ("numerical", "number", "value", "metric")):
            return "re_verify_with_code"
        if any(k in desc for k in ("evidence", "citation", "source")):
            return "expand_evidence"
        return "dynamic_research"

    @staticmethod
    def is_topologically_valid(plan: Plan) -> bool:
        """简单拓扑校验：所有依赖必须存在于 Plan 中且不存在自依赖。"""
        ids = {t.id for t in plan.tasks}
        for t in plan.tasks:
            if t.id in t.depends_on:
                return False
            for dep in t.depends_on:
                if dep not in ids:
                    return False
        return True
