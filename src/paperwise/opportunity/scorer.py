"""P4 Phase 1 — 机会打分与排序。

三维打分：
- confidence: 证据强度（证据数量 + 来源置信度）
- importance: 对当前研究的价值（由规则根据 urgency/severity 预设到 description 元数据）
- novelty:    是否已知/重复（与既有 pending 机会去重）

输出按综合分降序；低于 min_confidence 的被过滤（防垃圾机会）。
"""

from __future__ import annotations

from typing import Iterable

from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


# 各类型基础重要度
_TYPE_IMPORTANCE: dict[OpportunityType, float] = {
    OpportunityType.CONTRADICTION: 0.85,            # 冲突最需关注
    OpportunityType.MISSING_EVIDENCE: 0.75,          # 证据不足影响可信度
    OpportunityType.METHOD_COMPLEMENTARITY: 0.7,     # 方法互补有研究价值
    OpportunityType.KNOWLEDGE_GAP: 0.6,              # 知识缺口相对常规
}


class OpportunityScorer:
    """给候选机会打分并过滤。"""

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    def score(
        self,
        candidates: Iterable[ResearchOpportunity],
        existing: Iterable[ResearchOpportunity] = (),
    ) -> list[ResearchOpportunity]:
        """打分 -> 过滤 -> 排序。existing 用于 novelty 惩罚。"""
        existing_sigs = {o.signature() for o in existing}
        existing_titles = {o.title for o in existing}

        scored: list[ResearchOpportunity] = []
        for opp in candidates:
            opp.confidence = self._confidence(opp)
            opp.importance = self._importance(opp)
            opp.novelty = 0.3 if (opp.signature() in existing_sigs or opp.title in existing_titles) else 1.0

            if opp.confidence < self.min_confidence:
                continue
            scored.append(opp)

        scored.sort(
            key=lambda o: (o.confidence * 0.5 + o.importance * 0.3 + o.novelty * 0.2),
            reverse=True,
        )
        return scored

    def _confidence(self, opp: ResearchOpportunity) -> float:
        """证据强度：证据数量（饱和到 3 条）+ 平均长度合理性。"""
        if not opp.evidence:
            return 0.0
        n = min(len(opp.evidence), 3)
        quantity = n / 3.0
        # 证据摘录非空比例
        non_empty = sum(1 for e in opp.evidence if e.excerpt.strip())
        quality = non_empty / len(opp.evidence)
        return round(0.4 + 0.4 * quantity + 0.2 * quality, 3)

    def _importance(self, opp: ResearchOpportunity) -> float:
        return _TYPE_IMPORTANCE.get(opp.type, 0.5)
