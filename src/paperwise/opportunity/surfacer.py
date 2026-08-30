"""P4 Phase 3 — Opportunity Surfacer：新任务来临时 surfaced 相关 pending 机会。

定位（见 OPPORTUNITY_ENGINE_DESIGN.md 第十二节）：
不"每天定时推送"，而是在用户下次进行**相关研究**时，
把之前落盘的 pending 机会按相关性 surfaced——主动但不打扰。
"""

from __future__ import annotations

import re
from typing import Iterable

from paperwise.opportunity.models import OpportunityStatus, ResearchOpportunity


_STOPWORDS = {
    "this", "that", "with", "from", "paper", "using", "based", "the", "and",
    "into", "their", "which", "have", "been",
}


class OpportunitySurfacer:
    """按与新任务的相关性 surfaced pending 机会。"""

    def __init__(self, min_relevance: float = 0.15, limit: int = 3):
        self.min_relevance = min_relevance
        self.limit = limit

    def surface(
        self,
        task: str,
        opportunities: Iterable[ResearchOpportunity],
    ) -> list[ResearchOpportunity]:
        """返回与 task 相关的 pending 机会，按 (相关性 × 置信度) 降序。

        只 surfaced pending 机会；acted/dismissed/expired 不再出现。
        """
        task_tokens = self._tokens(task)
        if not task_tokens:
            return []

        ranked: list[tuple[float, ResearchOpportunity]] = []
        for opp in opportunities:
            if opp.status != OpportunityStatus.PENDING:
                continue
            relevance = self._relevance(task_tokens, opp)
            if relevance < self.min_relevance:
                continue
            ranked.append((relevance * opp.confidence, opp))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [o for _, o in ranked[: self.limit]]

    def surface_note(self, surfaced: list[ResearchOpportunity]) -> str:
        """生成注入到输出/上下文的可读提示（非侵入）。"""
        if not surfaced:
            return ""
        lines = ["\n\n---\n**相关研究机会（此前发现，pending）**"]
        for opp in surfaced:
            lines.append(f"- [{opp.type.value}] {opp.title}（置信度 {opp.confidence:.2f}）")
        return "\n".join(lines)

    def _relevance(self, task_tokens: set[str], opp: ResearchOpportunity) -> float:
        """task 与机会的 token 重叠率（Jaccard 变体，偏向 task 覆盖）。"""
        opp_text = f"{opp.title} {opp.description} {' '.join(opp.related_entities)}"
        opp_tokens = self._tokens(opp_text)
        if not opp_tokens:
            return 0.0
        overlap = task_tokens & opp_tokens
        return len(overlap) / len(task_tokens)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z]{4,}", (text or "").lower())
        return {t for t in tokens if t not in _STOPWORDS}
