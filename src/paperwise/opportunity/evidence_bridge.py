"""P4 evidence bridge: turn grounded evidence into research opportunities."""

from __future__ import annotations

import re
from typing import Any, Iterable

from paperwise.opportunity.models import EvidenceRef, OpportunityType, ResearchOpportunity


_STOPWORDS = {
    "this", "that", "with", "from", "paper", "using", "based", "method",
    "results", "abstract", "introduction", "conclusion", "references",
}


class EvidenceOpportunityBridge:
    """Derive candidates from EvidencePack and enrich rule candidates."""

    def derive(self, evidence_packs: Iterable[Any]) -> list[ResearchOpportunity]:
        packs = list(evidence_packs)
        candidates: list[ResearchOpportunity] = []
        for pack in packs:
            snippets = list(getattr(pack, "snippets", []) or [])
            if not snippets:
                continue
            query = str(getattr(pack, "query", "")).strip()

            if getattr(pack, "low_recall", False):
                candidates.append(self._make_missing_evidence(pack, snippets, query))

            papers = {s.paper_id for s in snippets if s.paper_id}
            if len(papers) >= 2:
                current, related = sorted(papers)[:2]
                shared = self._shared_tokens(
                    " ".join(s.content for s in snippets if s.paper_id == current),
                    " ".join(s.content for s in snippets if s.paper_id == related),
                )
                if len(shared) >= 2:
                    candidates.append(self._make_complementarity(
                        pack, current, related, sorted(shared), query,
                    ))
        return candidates

    def attach(
        self,
        opportunities: Iterable[ResearchOpportunity],
        evidence_packs: Iterable[Any],
    ) -> list[ResearchOpportunity]:
        """Attach relevant grounded snippets as additional evidence refs."""
        packs = list(evidence_packs)
        for opportunity in opportunities:
            opportunity_entities = {
                token for entity in opportunity.related_entities
                for token in self._tokens(entity)
            }
            if not opportunity_entities:
                continue
            matches: list[tuple[float, Any, Any]] = []
            for pack in packs:
                for snippet in getattr(pack, "snippets", []) or []:
                    snippet_tokens = self._tokens(snippet.content)
                    overlap = opportunity_entities & snippet_tokens
                    if overlap:
                        matches.append((len(overlap), pack, snippet))
            for _, pack, snippet in sorted(matches, key=lambda item: item[0], reverse=True)[:3]:
                opportunity.evidence.append(self._snippet_ref(snippet, pack))
        return opportunities

    def _make_missing_evidence(self, pack: Any, snippets: list[Any], query: str) -> ResearchOpportunity:
        refs = [self._snippet_ref(s, pack) for s in snippets[:2]]
        return ResearchOpportunity(
            type=OpportunityType.MISSING_EVIDENCE,
            title=f"证据覆盖不足：{query[:70]}",
            description=(
                f"针对查询“{query}”，当前证据召回不足；已有 {len(snippets)} 条低置信证据，"
                "需要重新检索、扩展查询或补充对比证据。"
            ),
            evidence=refs,
            related_entities=[query[:80], *list(dict.fromkeys(s.paper_id for s in snippets))[:2]],
            suggested_actions=["expand_evidence", "compare_evidence"],
        )

    def _make_complementarity(
        self,
        pack: Any,
        current_paper: str,
        related_paper: str,
        shared_topics: list[str],
        query: str,
    ) -> ResearchOpportunity:
        current_snippets = [s for s in pack.snippets if s.paper_id == current_paper][:2]
        related_snippets = [s for s in pack.snippets if s.paper_id == related_paper][:2]
        refs = [self._snippet_ref(s, pack) for s in current_snippets + related_snippets]
        return ResearchOpportunity(
            type=OpportunityType.METHOD_COMPLEMENTARITY,
            title=f"方法互补：{current_paper} × {related_paper}",
            description=(
                f"当前论文 {current_paper} 与 {related_paper} 在 {', '.join(shared_topics[:4])} "
                f"上存在共同证据；建议围绕“{query[:60]}”设计 A/B 或组合实验。"
            ),
            evidence=refs,
            related_entities=[current_paper, related_paper, *shared_topics[:3]],
            suggested_actions=["compare_methods", "suggest_experiment"],
        )

    @staticmethod
    def _snippet_ref(snippet: Any, pack: Any) -> EvidenceRef:
        return EvidenceRef(
            source_type=f"evidence_{getattr(snippet.structure_type, 'value', 'section')}",
            source_id=snippet.evidence_id,
            excerpt=snippet.content[:500],
            location=getattr(snippet, "location", "") or f"{getattr(pack, 'scope', 'current_paper')}",
        )

    @staticmethod
    def _shared_tokens(first: str, second: str) -> set[str]:
        first_tokens = EvidenceOpportunityBridge._tokens(first)
        second_tokens = EvidenceOpportunityBridge._tokens(second)
        return first_tokens & second_tokens

    @staticmethod
    def _tokens(text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-z]{4,}", (text or "").lower())
        return {token for token in tokens if token not in _STOPWORDS}
