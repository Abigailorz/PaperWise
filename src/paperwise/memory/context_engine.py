"""Context Engine: task-aware memory retrieval and assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from paperwise.memory.user_memory import UserMemory
from paperwise.memory.episodic_memory import EpisodicMemory
from paperwise.memory.procedural_memory import ProceduralMemory
from paperwise.memory.research_state import ResearchState
from paperwise.memory.knowledge_base import KnowledgeBase
from paperwise.core.hierarchical_memory import HierarchicalMemory


@dataclass
class ContextRetrievalPolicy:
    """Explicit, bounded retrieval policy for context assembly."""

    top_k: int = 5
    max_chars: int = 4000
    include_profile: bool = True
    include_episodes: bool = True
    include_procedures: bool = True
    include_paper_context: bool = True

    def for_node(self, node_id: str) -> "ContextRetrievalPolicy":
        """Create a node-aware policy; never enables full-paper context."""
        policy = ContextRetrievalPolicy(
            top_k=self.top_k,
            max_chars=self.max_chars,
            include_profile=True,
            include_episodes=False,
            include_procedures=True,
            include_paper_context=True,
        )
        if node_id in ("generate_report", "generate_pptx", "revision", "report_assemble", "ppt_assemble"):
            policy.include_paper_context = False
            policy.include_episodes = True
        return policy


@dataclass
class ContextPackage:
    """Assembled context for a task."""
    profile: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    procedures: list[dict] = field(default_factory=list)
    paper_context: list[dict] = field(default_factory=list)
    working_memory: str = ""

    def to_xml(self) -> str:
        lines = ["<context>"]
        if self.profile:
            lines.append("  <profile>")
            for item in self.profile:
                lines.append(f"    {item}")
            lines.append("  </profile>")
        if self.episodes:
            lines.append("  <episodes>")
            for item in self.episodes:
                lines.append(f"    {item}")
            lines.append("  </episodes>")
        if self.procedures:
            lines.append("  <procedures>")
            for item in self.procedures:
                lines.append(f"    {item}")
            lines.append("  </procedures>")
        if self.paper_context:
            lines.append("  <paper_context>")
            for item in self.paper_context:
                lines.append(f"    {item}")
            lines.append("  </paper_context>")
        if self.working_memory:
            lines.append(f"  <working_memory>\n{self.working_memory}\n  </working_memory>")
        lines.append("</context>")
        return "\n".join(lines)

    def size(self) -> int:
        """返回 XML 字符串的字符数，用于控制 prompt 长度。"""
        return len(self.to_xml())

    def truncate(self, max_chars: int) -> "ContextPackage":
        """按优先级截断上下文包：先 paper_context，再 episodes，再 procedures，最后 profile。"""
        if self.size() <= max_chars:
            return self
        # 创建一个可变的副本
        import copy
        pkg = copy.deepcopy(self)
        # 优先截断 paper_context（通常最长且最可替换）
        while pkg.paper_context and pkg.size() > max_chars:
            pkg.paper_context.pop()
        # 然后截断 episodes
        while pkg.episodes and pkg.size() > max_chars:
            pkg.episodes.pop()
        # 然后截断 procedures
        while pkg.procedures and pkg.size() > max_chars:
            pkg.procedures.pop()
        # 最后截断 profile
        while pkg.profile and pkg.size() > max_chars:
            pkg.profile.pop()
        return pkg

    def for_node(self, node_id: str) -> "ContextPackage":
        """返回针对特定节点的过滤版本。

        规则：
        - 所有节点都保留 profile 和 working_memory
        - method / experiment / verify 节点保留 procedures 和 paper_context
        - report / pptx 生成节点保留 episodes（历史偏好）
        """
        import copy
        pkg = copy.deepcopy(self)
        if node_id in ("analyze_method", "verify_data", "re_verify_with_code", "experiment_analysis"):
            pkg.episodes = []
        elif node_id in ("generate_report", "generate_pptx", "revision", "report_assemble", "ppt_assemble"):
            pkg.paper_context = []
        else:
            # 通用节点：保留全部但可后续细化
            pass
        return pkg


class ContextEngine:
    """Retrieve and assemble relevant memories for the current task."""

    def __init__(
        self,
        workspace: Path,
        user_id: str = "default",
        user_memory: Optional[UserMemory] = None,
        episodic_memory: Optional[EpisodicMemory] = None,
        procedural_memory: Optional[ProceduralMemory] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
    ):
        self.workspace = workspace
        self.user_id = user_id
        self.user_memory = user_memory or UserMemory(workspace / ".paperwise" / user_id / "memory", user_id=user_id)
        self.episodic_memory = episodic_memory or EpisodicMemory(workspace / ".paperwise" / user_id / "episodes", user_id=user_id)
        self.procedural_memory = procedural_memory or ProceduralMemory(workspace / ".paperwise" / user_id / "procedures", user_id=user_id)
        self.knowledge_base = knowledge_base

    def assemble(
        self,
        research_state: ResearchState,
        hierarchical_memory: Optional[HierarchicalMemory] = None,
        top_k: int = 5,
        policy: Optional[ContextRetrievalPolicy] = None,
    ) -> ContextPackage:
        pkg = ContextPackage()
        policy = policy or ContextRetrievalPolicy(top_k=top_k, max_chars=4000)
        effective_top_k = policy.top_k or top_k

        # Profile: research domains, preferences, facts above threshold
        if policy.include_profile:
            profile_cards = self.user_memory.query(min_confidence=0.5, limit=effective_top_k)
            for card in profile_cards:
                if card.status != "active":
                    continue
                data_str = ", ".join(f"{k}={v}" for k, v in card.data.items())
                pkg.profile.append({
                    "category": card.category,
                    "data": data_str,
                    "confidence": card.confidence,
                    "source": card.source,
                })

        # Episodes: similar task_type or entity
        task_type = research_state.intent or "analysis"
        if policy.include_episodes:
            episodes = self.episodic_memory.query(task_type=task_type, entity=research_state.current_paper or "", limit=effective_top_k)
            for ep in episodes:
                pkg.episodes.append({
                    "goal": ep.goal,
                    "findings": ep.findings,
                    "outcome": ep.outcome,
                })

        # Procedures: match task type
        if policy.include_procedures:
            procedures = self.procedural_memory.match(task_type, {"paper": research_state.current_paper or ""})
            for pat in procedures[:effective_top_k]:
                pkg.procedures.append({
                    "steps": pat.preferred_steps,
                    "preferences": pat.preferences,
                    "success_rate": pat.success_rate,
                })

        # Knowledge base: search current paper context
        if policy.include_paper_context and self.knowledge_base and research_state.current_paper:
            query = " ".join([research_state.current_task] + [g.description for g in research_state.gaps[:3]])
            try:
                results = self.knowledge_base.search(query, top_k=effective_top_k, search_chunks=True)
                for r in results:
                    pkg.paper_context.append({
                        "doc_id": r.get("doc_id", ""),
                        "text": r.get("text", "")[:300],
                    })
            except Exception:
                pass

        # Working memory
        if hierarchical_memory:
            pkg.working_memory = hierarchical_memory.working_summary

        return pkg

    def assemble_for_subagent(
        self,
        node_id: str,
        research_state: ResearchState,
        hierarchical_memory: Optional[HierarchicalMemory] = None,
        top_k: int = 5,
        max_chars: int = 4000,
        policy: Optional[ContextRetrievalPolicy] = None,
    ) -> ContextPackage:
        """为特定子 Agent 节点组装并截断上下文。"""
        retrieval_policy = policy or ContextRetrievalPolicy(top_k=top_k, max_chars=max_chars)
        retrieval_policy = retrieval_policy.for_node(node_id)
        pkg = self.assemble(research_state, hierarchical_memory=hierarchical_memory, top_k=top_k, policy=retrieval_policy)
        pkg = pkg.for_node(node_id)
        return pkg.truncate(retrieval_policy.max_chars)
