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
    ) -> ContextPackage:
        pkg = ContextPackage()

        # Profile: research domains, preferences, facts above threshold
        profile_cards = self.user_memory.query(min_confidence=0.5, limit=top_k)
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
        episodes = self.episodic_memory.query(task_type=task_type, entity=research_state.current_paper or "", limit=top_k)
        for ep in episodes:
            pkg.episodes.append({
                "goal": ep.goal,
                "findings": ep.findings,
                "outcome": ep.outcome,
            })

        # Procedures: match task type
        procedures = self.procedural_memory.match(task_type, {"paper": research_state.current_paper or ""})
        for pat in procedures[:top_k]:
            pkg.procedures.append({
                "steps": pat.preferred_steps,
                "preferences": pat.preferences,
                "success_rate": pat.success_rate,
            })

        # Knowledge base: search current paper context
        if self.knowledge_base and research_state.current_paper:
            query = " ".join([research_state.current_task] + [g.description for g in research_state.gaps[:3]])
            try:
                results = self.knowledge_base.search(query, top_k=top_k, search_chunks=True)
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
