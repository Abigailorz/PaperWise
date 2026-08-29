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


# ---------------------------------------------------------------------------
# 动态 Plan -> 可执行 Plan 适配层
#
# 原则（见实施 Spec 第 7 节 P2 收尾）：**Node Capability 受控，Graph Composition 动态**。
# DynamicDAGPlanner 可以自由组合 Registry 中的节点，但最终执行前必须映射到
# SmartOrchestrator 已注册 handler 的可执行节点集合——LLM / planner 不能发明
# 没有 handler 的节点并让它真正运行。
# ---------------------------------------------------------------------------

#: SmartOrchestrator 已注册 handler 的可执行节点
EXECUTABLE_NODE_IDS: frozenset[str] = frozenset({
    "read_paper",
    "analyze_method",
    "verify_data",
    "generate_report",
    "generate_pptx",
    "review_report",
    "revise_report",
    # ReplanAgent 产出的纠正节点
    "re_read_section",
    "re_verify_with_code",
    "revision",
    "expand_evidence",
    "dynamic_research",
})

#: Registry 节点 -> 可执行节点的映射。
#: 多个细粒度 registry 节点折叠为同一个可执行节点（其 handler 内部完成完整子流程，
#: 例如 report_outline/section/assemble 都由 report writer 一个子 Agent 完成）。
NODE_TO_EXECUTABLE: dict[str, str] = {
    "parse_pdf": "read_paper",
    "extract_text": "read_paper",
    "problem_analysis": "analyze_method",
    "method_analysis": "analyze_method",
    "experiment_analysis": "analyze_method",
    "related_work_analysis": "analyze_method",
    "synthesis": "analyze_method",
    "summarize": "analyze_method",
    "evidence_verification": "verify_data",
    "report_outline": "generate_report",
    "report_section": "generate_report",
    "report_assemble": "generate_report",
    "ppt_outline": "generate_pptx",
    "ppt_slide": "generate_pptx",
    "ppt_assemble": "generate_pptx",
    "critic": "review_report",
    # "revision" 本身是可执行的纠正节点（有注册 handler），但动态 Plan 中出现时
    # 统一收敛到 revise_report，避免与审查循环里的 revise 节点重复
    "revision": "revise_report",
}

#: 未注册且未映射的节点的保守归宿
DEFAULT_EXECUTABLE_NODE = "analyze_method"

#: 可执行节点的规范描述（与静态 Plan 保持一致）
EXECUTABLE_NODE_DESCRIPTIONS: dict[str, str] = {
    "read_paper": "Read paper and extract facts",
    "analyze_method": "Analyze methodology and main claims",
    "verify_data": "Verify numerical claims with code",
    "generate_report": "Generate structured analysis report",
    "generate_pptx": "Generate academic presentation slides",
    "review_report": "Adversarially review the output",
    "revise_report": "Revise the output based on review findings",
    "re_read_section": "Re-read specific section of the paper",
    "re_verify_with_code": "Re-verify numerical claims with code",
    "revision": "Revise the output based on review findings",
    "expand_evidence": "Expand evidence and citations",
    "dynamic_research": "Dynamic research on open questions",
}

#: 映射后需要附加条件门的可执行节点（与静态 Plan 行为一致，节省 token）
_EXECUTABLE_CONDITIONS: dict[str, str] = {
    "generate_pptx": "requires_pptx",
    "verify_data": "requires_verification",
    # 审查干净时跳过修改，避免白跑一轮 revision writer
    "revise_report": "critic_has_issues",
}


def executable_id_for(node_id: str) -> str:
    """把任意节点 id 映射为可执行节点 id。显式映射优先于透传。"""
    if node_id in NODE_TO_EXECUTABLE:
        return NODE_TO_EXECUTABLE[node_id]
    if node_id in EXECUTABLE_NODE_IDS:
        return node_id
    return DEFAULT_EXECUTABLE_NODE


def to_executable_plan(plan: Plan) -> Plan:
    """把动态组合 Plan 折叠为只含可执行节点的 Plan。

    - 多个 registry 节点折叠为同一可执行节点时，依赖关系在可执行空间重建，
      去掉自依赖、去重，max_retries 取最大值
    - 已是可执行节点的任务原样保留（描述 / 条件 / 重试等字段不变）
    - 折叠出的 generate_pptx / verify_data 附加条件门，与静态 Plan 一致
    """
    id_map = {t.id: executable_id_for(t.id) for t in plan.tasks}
    exec_plan = Plan()
    added: set[str] = set()

    for task in plan.tasks:
        exec_id = id_map[task.id]
        if exec_id in added:
            existing = exec_plan.get(exec_id)
            if existing is not None:
                existing.max_retries = max(existing.max_retries, task.max_retries)
            continue

        deps: list[str] = []
        for dep in task.depends_on:
            mapped = id_map.get(dep, executable_id_for(dep))
            if mapped != exec_id and mapped not in deps:
                deps.append(mapped)

        if task.id == exec_id:
            # 已是可执行节点：原样透传
            exec_plan.add(
                task.description,
                depends_on=deps,
                task_id=exec_id,
                parallel_group=task.parallel_group,
                condition=task.condition,
                max_retries=task.max_retries,
                output_artifact=task.output_artifact,
                confidence_threshold=task.confidence_threshold,
            )
        else:
            exec_plan.add(
                EXECUTABLE_NODE_DESCRIPTIONS.get(exec_id, f"Run {exec_id}"),
                depends_on=deps,
                task_id=exec_id,
                condition=_EXECUTABLE_CONDITIONS.get(exec_id),
                max_retries=task.max_retries,
            )
        added.add(exec_id)

    return exec_plan


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

        # 固定优先级：input -> extraction -> research -> reasoning -> generation -> verification -> revision
        priority = {
            "input": 0,
            "extraction": 1,
            "research": 2,
            "reasoning": 3,
            "generation": 4,
            "verification": 5,
            "revision": 6,
            "general": 7,
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
