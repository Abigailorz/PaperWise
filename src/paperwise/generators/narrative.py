"""P6 Phase D - Research Narrative: unified input for Report and PPT output.

Report and PPT are two projections of the same Research Narrative, not
independent generation pipelines. The narrative bundles Research State,
Evidence, Findings, and Hypotheses into a structured object that any
output generator can consume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from paperwise.evidence.models import EvidencePack
from paperwise.memory.research_state import ResearchState


@dataclass
class NarrativeSection:
    """A single structured unit of the research narrative."""

    section_id: str = ""
    title: str = ""
    claim: str = ""
    evidence_summary: str = ""
    citation: str = ""
    confidence: float = 0.0


@dataclass
class CrossPaperNarrativeSection:
    """P9.4 — A cross-paper section derived from multi-paper analysis."""

    section_type: str = ""   # method_comparison | contradictions | complementarity | research_gaps
    title: str = ""
    content: str = ""
    source_papers: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ResearchNarrative:
    """Aggregated research state for output generation."""

    title: str = ""
    task: str = ""
    paper_title: str = ""
    sections: list[NarrativeSection] = field(default_factory=list)
    findings_summary: list[dict[str, Any]] = field(default_factory=list)
    hypotheses_summary: list[dict[str, Any]] = field(default_factory=list)
    opportunities_summary: list[dict[str, Any]] = field(default_factory=list)
    questions_summary: list[dict[str, Any]] = field(default_factory=list)
    actions_summary: list[dict[str, Any]] = field(default_factory=list)
    evidence_snippets: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    cross_paper_sections: list[CrossPaperNarrativeSection] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        research_state: ResearchState,
        evidence_pack: Optional[EvidencePack] = None,
        facts: Optional[dict[str, Any]] = None,
    ) -> "ResearchNarrative":
        """Build narrative from research state, evidence, and facts."""
        facts = facts or {}
        narrative = cls(
            task=research_state.current_task,
            paper_title=facts.get("title") or (Path(research_state.current_paper).name if research_state.current_paper else ""),
        )
        narrative.findings_summary = [
            {"claim": f.claim, "evidence": f.evidence, "confidence": f.confidence}
            for f in research_state.findings
        ]
        narrative.hypotheses_summary = [
            {"statement": h.statement, "confidence": h.confidence, "status": h.status}
            for h in getattr(research_state, "hypotheses", [])
        ]
        narrative.opportunities_summary = [
            {"type": o.type.value, "title": o.title, "confidence": o.confidence, "status": o.status.value}
            for o in research_state.opportunities
        ]
        narrative.questions_summary = [
            {
                "question": q.question,
                "status": q.status,
                "outcome": q.outcome,
                "evaluation_count": q.evaluation_count,
                "importance": q.importance,
                "source_opportunities": q.source_opportunities,
            }
            for q in getattr(research_state, "questions", [])
        ]
        narrative.actions_summary = [
            {
                "action_type": a.action_type.value,
                "objective": a.objective,
                "status": a.status.value,
                "risk_level": a.risk_level.value,
            }
            for a in getattr(research_state, "pending_actions", []) + getattr(research_state, "completed_actions", [])
        ]
        if evidence_pack:
            narrative.evidence_snippets = [
                {
                    "section": s.section,
                    "content": s.content[:300],
                    "structure_type": s.structure_type.value,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                }
                for s in evidence_pack.snippets
            ]
        for finding in research_state.findings:
            narrative.sections.append(NarrativeSection(
                section_id=f"sec_{finding.node_id}",
                title=finding.claim[:80],
                claim=finding.claim,
                evidence_summary=finding.evidence[:200] if finding.evidence else "",
                citation=finding.evidence[:100] if finding.evidence else "",
                confidence=finding.confidence,
            ))

        # P9.4: Build cross-paper sections from cross-paper opportunities.
        cross_paper_opps = [
            o for o in research_state.opportunities
            if any("跨论文" in o.title or "cross" in o.title.lower() for _ in [1])
        ]
        for opp in cross_paper_opps:
            section_type = {
                "knowledge_gap": "method_comparison",
                "contradiction": "contradictions",
                "method_complementarity": "complementarity",
            }.get(opp.type.value, "research_gaps")
            source_papers = [
                e.location for e in opp.evidence if e.location
            ]
            narrative.cross_paper_sections.append(CrossPaperNarrativeSection(
                section_type=section_type,
                title=opp.title,
                content=opp.description,
                source_papers=list(dict.fromkeys(source_papers)),
                evidence_refs=[e.source_id for e in opp.evidence],
            ))
        return narrative

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchNarrative":
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if kwargs.get("sections"):
            kwargs["sections"] = [NarrativeSection(**s) for s in kwargs["sections"]]
        if kwargs.get("cross_paper_sections"):
            kwargs["cross_paper_sections"] = [
                CrossPaperNarrativeSection(**s) for s in kwargs["cross_paper_sections"]
            ]
        return cls(**kwargs)

    def to_prompt_context(self, max_chars: int = 4000) -> str:
        """Compact prompt context for report/PPT writers."""
        lines = [f"# Research Narrative: {self.paper_title or self.task}"]
        if self.findings_summary:
            lines.append("\n## Verified Findings")
            for f in self.findings_summary[:5]:
                lines.append(f"- {f['claim']} (confidence: {f['confidence']:.2f})")
        if self.hypotheses_summary:
            lines.append("\n## Hypotheses")
            for h in self.hypotheses_summary[:3]:
                lines.append(f"- {h['statement']} ({h['status']})")
        if self.questions_summary:
            lines.append("\n## Research Questions")
            for q in self.questions_summary[:3]:
                lines.append(f"- {q['question']} ({q['status']})")
        if self.actions_summary:
            lines.append("\n## Research Actions")
            for a in self.actions_summary[:3]:
                lines.append(f"- [{a['status']}] {a['action_type']}: {a['objective'][:100]}")
        if self.opportunities_summary:
            lines.append("\n## Open Opportunities")
            for o in self.opportunities_summary[:3]:
                lines.append(f"- [{o['type']}] {o['title']}")
        if self.cross_paper_sections:
            lines.append("\n## Cross-Paper Analysis")
            for cs in self.cross_paper_sections[:5]:
                lines.append(f"- [{cs.section_type}] {cs.title}")
                if cs.source_papers:
                    lines.append(f"  papers: {', '.join(cs.source_papers[:3])}")
        if self.evidence_snippets:
            lines.append("\n## Evidence")
            for e in self.evidence_snippets[:3]:
                lines.append(f"- [{e['structure_type']}] {e['content'][:100]}...")
        result = "\n".join(lines)
        return result[:max_chars]
