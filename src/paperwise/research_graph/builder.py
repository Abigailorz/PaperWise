"""Build a ResearchGraph from ResearchState, EvidencePack, and facts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from paperwise.evidence.models import EvidencePack, EvidenceSnippet
from paperwise.memory.research_state import ResearchState
from paperwise.research_graph.models import (
    EntityType,
    RelationType,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)


def _hash_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:12]}"


class ResearchGraphBuilder:
    """Turn execution outputs into a stable, mergeable research graph."""

    def build(
        self,
        research_state: ResearchState,
        evidence_packs: Iterable[EvidencePack] = (),
        facts: dict[str, Any] | None = None,
    ) -> ResearchGraph:
        graph = ResearchGraph(
            graph_id=f"rg_{research_state.state_id}",
            user_id=research_state.user_id,
        )
        facts = facts or {}

        user = graph.add_node(ResearchNode(
            node_id=_hash_id("user", research_state.user_id),
            entity_type=EntityType.USER,
            label=research_state.user_id,
            description="User whose research state is being maintained.",
        ))
        paper_label = facts.get("title") or (
            Path(research_state.current_paper).name
            if research_state.current_paper else "Unknown paper"
        )
        project = graph.add_node(ResearchNode(
            node_id="project_default",
            entity_type=EntityType.PROJECT,
            label="Default research project",
            description=research_state.current_task[:300],
        ))
        question = graph.add_node(ResearchNode(
            node_id=_hash_id("question", research_state.current_task or "unspecified"),
            entity_type=EntityType.RESEARCH_QUESTION,
            label=research_state.current_task[:120] or "Unspecified question",
            description=research_state.current_task,
        ))
        graph.add_edge(ResearchEdge(user.node_id, project.node_id, RelationType.OWNS, confidence=1.0))
        graph.add_edge(ResearchEdge(project.node_id, question.node_id, RelationType.STUDIES, confidence=1.0))

        question_nodes = {}
        for research_question in research_state.questions:
            question_node = graph.add_node(ResearchNode(
                node_id=research_question.question_id,
                entity_type=EntityType.RESEARCH_QUESTION,
                label=research_question.question[:120],
                description=research_question.question,
                confidence=research_question.importance,
                metadata={
                    "status": research_question.status,
                    "source_opportunities": research_question.source_opportunities,
                },
            ))
            question_nodes[research_question.question_id] = question_node
            graph.add_edge(ResearchEdge(
                project.node_id, question_node.node_id,
                RelationType.STUDIES, confidence=1.0,
            ))

        paper = graph.add_node(ResearchNode(
            node_id=_hash_id("paper", paper_label),
            entity_type=EntityType.PAPER,
            label=paper_label,
            description=facts.get("abstract", ""),
            source=research_state.current_paper or "",
            metadata={"authors": facts.get("authors", [])},
        ))
        graph.add_edge(ResearchEdge(
            question.node_id, paper.node_id, RelationType.RELATED_TO, confidence=0.9,
        ))

        for snippet in self._snippets(evidence_packs):
            evidence_node = graph.add_node(ResearchNode(
                node_id=_hash_id("evidence", snippet.evidence_id),
                entity_type=EntityType.EVIDENCE,
                label=f"{snippet.structure_type.value}: {snippet.section or snippet.evidence_id}",
                description=snippet.content[:500],
                confidence=min(1.0, max(snippet.score, 0.4)),
                source=snippet.citation(),
                metadata={
                    "paper_id": snippet.paper_id,
                    "structure_type": snippet.structure_type.value,
                    "start_line": snippet.start_line,
                    "end_line": snippet.end_line,
                },
            ))
            evidence_paper = graph.add_node(ResearchNode(
                node_id=_hash_id("paper", snippet.paper_id),
                entity_type=EntityType.PAPER,
                label=snippet.paper_id,
            ))
            graph.add_edge(ResearchEdge(
                evidence_paper.node_id, evidence_node.node_id,
                RelationType.SUPPORTED_BY, confidence=0.85,
            ))

        for finding in research_state.findings:
            finding_node = graph.add_node(ResearchNode(
                node_id=_hash_id("finding", f"{finding.node_id}:{finding.claim}"),
                entity_type=EntityType.FINDING,
                label=finding.claim[:120],
                description=finding.claim,
                confidence=finding.confidence,
                source=finding.evidence,
            ))
            graph.add_edge(ResearchEdge(
                paper.node_id, finding_node.node_id,
                RelationType.DERIVED_FROM, confidence=finding.confidence,
            ))
            if finding.evidence:
                evidence_node = graph.add_node(ResearchNode(
                    node_id=_hash_id("evidence", finding.evidence),
                    entity_type=EntityType.EVIDENCE,
                    label=f"Evidence for {finding.node_id}",
                    description=finding.evidence[:500],
                    confidence=finding.confidence,
                ))
                graph.add_edge(ResearchEdge(
                    finding_node.node_id, evidence_node.node_id,
                    RelationType.SUPPORTED_BY, confidence=finding.confidence,
                ))

        method_text = str(facts.get("method") or facts.get("approach") or "").strip()
        if method_text:
            method_node = graph.add_node(ResearchNode(
                node_id=_hash_id("method", method_text),
                entity_type=EntityType.METHOD,
                label=method_text[:120],
                description=method_text,
                source=research_state.current_paper or "",
            ))
            graph.add_edge(ResearchEdge(
                paper.node_id, method_node.node_id, RelationType.PROPOSES, confidence=0.9,
            ))
            graph.add_edge(ResearchEdge(
                question.node_id, method_node.node_id, RelationType.USES, confidence=0.7,
            ))

        for raw_claim in facts.get("claims", []) or []:
            if not isinstance(raw_claim, dict) or not raw_claim.get("claim"):
                continue
            claim_text = str(raw_claim["claim"])
            claim_node = graph.add_node(ResearchNode(
                node_id=_hash_id("claim", claim_text),
                entity_type=EntityType.CLAIM,
                label=claim_text[:120],
                description=claim_text,
                confidence=float(raw_claim.get("confidence", 0.7)),
                source=" | ".join(map(str, raw_claim.get("evidence", []) or [])),
            ))
            graph.add_edge(ResearchEdge(
                paper.node_id, claim_node.node_id, RelationType.RELATED_TO, confidence=0.8,
            ))
            if raw_claim.get("evidence"):
                evidence_text = " | ".join(map(str, raw_claim["evidence"]))
                evidence_id = _hash_id("evidence", evidence_text)
                graph.add_node(ResearchNode(
                    node_id=evidence_id,
                    entity_type=EntityType.EVIDENCE,
                    label=evidence_text[:120],
                    description=evidence_text[:500],
                ))
                graph.add_edge(ResearchEdge(
                    claim_node.node_id, evidence_id, RelationType.SUPPORTED_BY, confidence=0.8,
                ))

        for dataset in facts.get("datasets", []) or []:
            label = self._label(dataset)
            dataset_node = graph.add_node(ResearchNode(
                node_id=_hash_id("dataset", label),
                entity_type=EntityType.DATASET,
                label=label[:120],
            ))
            graph.add_edge(ResearchEdge(
                paper.node_id, dataset_node.node_id, RelationType.USES, confidence=0.8,
            ))

        for experiment in facts.get("experiments", []) or []:
            label = self._label(experiment)
            experiment_node = graph.add_node(ResearchNode(
                node_id=_hash_id("experiment", label),
                entity_type=EntityType.EXPERIMENT,
                label=label[:120],
                description=self._description(experiment),
            ))
            graph.add_edge(ResearchEdge(
                paper.node_id, experiment_node.node_id, RelationType.EVALUATES, confidence=0.8,
            ))

        for hypothesis_text in research_state.next_steps[:5]:
            if not hypothesis_text:
                continue
            hypothesis_node = graph.add_node(ResearchNode(
                node_id=_hash_id("hypothesis", hypothesis_text),
                entity_type=EntityType.HYPOTHESIS,
                label=hypothesis_text[:120],
                description=hypothesis_text,
            ))
            graph.add_edge(ResearchEdge(
                question.node_id, hypothesis_node.node_id,
                RelationType.SUGGESTS_HYPOTHESIS, confidence=0.5,
            ))

        for opportunity in research_state.opportunities:
            opportunity_node = graph.add_node(ResearchNode(
                node_id=_hash_id("opportunity", opportunity.opportunity_id),
                entity_type=EntityType.OPPORTUNITY,
                label=opportunity.title[:120],
                description=opportunity.description,
                confidence=opportunity.confidence,
                source=opportunity.evidence[0].location if opportunity.evidence else "",
                metadata={"status": opportunity.status.value, "type": opportunity.type.value},
            ))
            relation = RelationType.HAS_GAP
            if opportunity.type.value == "method_complementarity":
                relation = RelationType.COMPLEMENTS
            elif opportunity.type.value == "contradiction":
                relation = RelationType.CONTRADICTS
            graph.add_edge(ResearchEdge(
                question.node_id, opportunity_node.node_id, relation,
                confidence=opportunity.confidence,
                evidence_ids=[
                    _hash_id("evidence", ref.source_id) for ref in opportunity.evidence[:3]
                ],
            ))
            for research_question in research_state.questions:
                if opportunity.opportunity_id not in research_question.source_opportunities:
                    continue
                question_node = question_nodes.get(research_question.question_id)
                if question_node is not None:
                    graph.add_edge(ResearchEdge(
                        question_node.node_id, opportunity_node.node_id,
                        relation, confidence=opportunity.confidence,
                        evidence_ids=[
                            _hash_id("evidence", ref.source_id) for ref in opportunity.evidence[:3]
                        ],
                    ))

        return graph

    @staticmethod
    def _snippets(evidence_packs: Iterable[EvidencePack]) -> list[EvidenceSnippet]:
        snippets: dict[str, EvidenceSnippet] = {}
        for pack in evidence_packs:
            for snippet in pack.snippets:
                snippets.setdefault(snippet.evidence_id, snippet)
        return list(snippets.values())

    @staticmethod
    def _label(value: Any) -> str:
        if isinstance(value, dict):
            for key in ("name", "dataset", "title", "method", "experiment"):
                if value.get(key):
                    return str(value[key])
        return str(value)

    @staticmethod
    def _description(value: Any) -> str:
        if isinstance(value, dict):
            return " | ".join(f"{key}: {item}" for key, item in value.items() if item)
        return str(value)
