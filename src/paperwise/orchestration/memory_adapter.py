"""OrchestratorMemoryAdapter — 桥接 Memory 层与 Orchestrator 决策层。

职责：
- 为当前任务组装 ContextPackage
- 根据 ResearchState 的 findings/gaps 调整 Plan
- 在 DAG 执行结束后记录 Episode 和 ProceduralMemory
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from paperwise.core.types import AgentResult, AgentTrace
from paperwise.core.plan import Plan, Task
from paperwise.learning.signals import LearningSignal, LearningSignalGenerator
from paperwise.learning.strategy_library import StrategyLibrary
from paperwise.memory.context_engine import ContextEngine, ContextPackage
from paperwise.memory.research_state import ResearchState, ResearchStateManager, KnowledgeGap
from paperwise.memory.episodic_memory import EpisodicMemory
from paperwise.memory.procedural_memory import ProceduralMemory


class OrchestratorMemoryAdapter:
    """为 SmartOrchestrator 提供统一记忆访问接口。"""

    def __init__(
        self,
        workspace: Path,
        user_id: str = "default",
        research_state_manager: Optional[ResearchStateManager] = None,
        context_engine: Optional[ContextEngine] = None,
        episodic_memory: Optional[EpisodicMemory] = None,
        procedural_memory: Optional[ProceduralMemory] = None,
        strategy_library: Optional[StrategyLibrary] = None,
        signal_generator: Optional[LearningSignalGenerator] = None,
    ):
        self.workspace = Path(workspace)
        self.user_id = user_id
        self.research_state_manager = research_state_manager or ResearchStateManager(
            self.workspace, user_id=user_id
        )
        self.context_engine = context_engine or ContextEngine(
            self.workspace, user_id=user_id
        )
        self.episodic_memory = episodic_memory or EpisodicMemory(
            self.workspace / ".paperwise" / user_id / "episodes", user_id=user_id
        )
        self.procedural_memory = procedural_memory or ProceduralMemory(
            self.workspace / ".paperwise" / user_id / "procedures", user_id=user_id
        )
        self.strategy_library = strategy_library or StrategyLibrary(
            self.workspace / ".paperwise" / user_id / "strategies", user_id=user_id
        )
        self.signal_generator = signal_generator or LearningSignalGenerator()
        # 最近一次 apply_strategies_to_plan 实际应用的策略 id（用于 outcome 回写）
        self._applied_strategy_ids: list[str] = []

    def assemble_context(self, research_state: ResearchState) -> ContextPackage:
        """为当前任务组装完整上下文。"""
        return self.context_engine.assemble(research_state)

    def assemble_context_for_subagent(
        self,
        node_id: str,
        research_state: ResearchState,
        max_chars: int = 4000,
    ) -> ContextPackage:
        """为特定子 Agent 节点组装过滤后的上下文。"""
        return self.context_engine.assemble_for_subagent(
            node_id=node_id,
            research_state=research_state,
            max_chars=max_chars,
        )

    def apply_gaps_to_plan(self, plan: Plan, research_state: ResearchState) -> Plan:
        """根据未解决的 gaps 在 Plan 中插入恢复/验证节点。

        当前规则：
        - 若存在 urgency=high 的 numerical/verification gap，在 analyze_method 前插入 verify_data
        - 若存在 evidence gap，在 generate_report 前插入 expand_evidence
        """
        gaps = research_state.get_high_priority_gaps(limit=3)
        if not gaps:
            return plan

        # 记录已经插入的节点，避免重复
        existing_ids = {t.id for t in plan.tasks}

        for gap in gaps:
            if gap.urgency != "high":
                continue
            desc = gap.description.lower()
            if any(k in desc for k in ("numerical", "number", "digit", "value", "accuracy", "metric")):
                if "verify_data" not in existing_ids and "analyze_method" in existing_ids:
                    plan.add(
                        "Verify numerical claims with code",
                        task_id="verify_data",
                        depends_on=["read_paper"],
                    )
                    existing_ids.add("verify_data")
            elif any(k in desc for k in ("evidence", "citation", "source", "reference")):
                if "expand_evidence" not in existing_ids and "generate_report" in existing_ids:
                    plan.add(
                        "Expand evidence and citations",
                        task_id="expand_evidence",
                        depends_on=["analyze_method"],
                    )
                    existing_ids.add("expand_evidence")

        return plan

    def record_episode(
        self,
        research_state: ResearchState,
        trace: Optional[AgentTrace],
        result: AgentResult,
    ) -> None:
        """将一次任务执行记录为 Episode。"""
        try:
            self.episodic_memory.record(
                goal=research_state.current_task,
                task_type=research_state.intent or "analysis",
                entities=[research_state.current_paper] if research_state.current_paper else [],
                findings=[f.claim for f in research_state.findings[:5]],
                outcome="success" if result.success else f"failed: {result.error_message}",
            )
        except Exception:
            # 记忆记录不应阻塞主流程
            pass

    def learn_procedure(self, task_type: str, plan: Plan, success: bool) -> None:
        """从成功或失败的 Plan 中提取程序性模式。"""
        try:
            preferred_steps = [t.id for t in plan.tasks]
            self.procedural_memory.learn(
                task_type=task_type,
                preferred_steps=preferred_steps,
                context_signature={"plan_signature": "|".join(preferred_steps[:6])},
                success=success,
            )
        except Exception:
            pass

    def learn_from_review(
        self,
        task_type: str,
        findings: dict,
    ) -> list[LearningSignal]:
        """把 Reviewer findings 转换为学习信号并更新策略库。

        Reviewer 由此升级为 Learning Signal Generator：
        findings 不只用于当轮 revise，还沉淀为可复用的 Strategy。
        """
        try:
            signals = self.signal_generator.from_findings(findings, task_type=task_type)
            self.strategy_library.learn_from_signals(task_type, signals)
            return signals
        except Exception:
            # 学习信号不应阻塞主流程
            return []

    def apply_strategies_to_plan(self, plan: Plan, task_type: str) -> Plan:
        """根据策略库中的高置信策略补全 Plan（保守插入，不删除节点）。

        只处理白名单中的可插入节点，且依赖节点必须已存在，
        保证插入后 Plan 拓扑仍然合法。
        """
        insertable: dict[str, tuple[str, list[str]]] = {
            # node_id: (description, preferred dependencies)
            "verify_data": ("Verify numerical claims with code", ["read_paper"]),
            "expand_evidence": ("Expand evidence and citations", ["analyze_method", "read_paper"]),
        }
        try:
            strategies = self.strategy_library.select(task_type)
        except Exception:
            return plan

        existing_ids = {t.id for t in plan.tasks}
        self._applied_strategy_ids = []
        for strat in strategies:
            applied = False
            for hint in strat.plan_hints:
                spec = insertable.get(hint)
                if spec is None or hint in existing_ids:
                    continue
                description, preferred_deps = spec
                deps = [d for d in preferred_deps if d in existing_ids]
                plan.add(description, task_id=hint, depends_on=deps)
                existing_ids.add(hint)
                applied = True
            if applied:
                self._applied_strategy_ids.append(strat.strategy_id)
        return plan

    def record_strategy_outcomes(self, success: bool) -> None:
        """把本次执行结果回写到实际应用过的策略（P3.5 闭环）。

        只统计真正改变了 Plan 的策略，未被应用的策略不受本轮结果影响。
        """
        for strategy_id in self._applied_strategy_ids:
            try:
                self.strategy_library.record_outcome(strategy_id, success)
            except Exception:
                pass
        self._applied_strategy_ids = []

    def update_state_from_execution(
        self,
        research_state: ResearchState,
        completed_nodes: list[str],
        failed_nodes: list[str],
        gaps: list[KnowledgeGap],
    ) -> ResearchState:
        """根据 DAG 执行结果更新 ResearchState。"""
        research_state.completed_nodes.extend(completed_nodes)
        research_state.failed_nodes.extend(failed_nodes)
        for node_id in completed_nodes:
            research_state.add_finding_from_node(
                node_id=node_id,
                claim=f"Node {node_id} completed",
                evidence="",
                confidence=0.9,
            )
        for gap in gaps:
            # 避免重复添加相同描述的 gap
            if not any(g.description == gap.description for g in research_state.gaps):
                research_state.add_gap(
                    description=gap.description,
                    node_id=gap.node_id,
                    urgency=gap.urgency,
                    suggested_action=gap.suggested_action,
                )
        research_state.dag_status = "completed" if not failed_nodes else "completed_with_gaps"
        self.research_state_manager.save(research_state)
        return research_state
