"""P4 Phase 1 — 机会证据验证。

反幻觉硬约束：任何没有有效证据引用的机会直接丢弃，
不允许"LLM 凭空声称发现了研究机会"。
"""

from __future__ import annotations

from typing import Any, Optional

from paperwise.opportunity.models import ResearchOpportunity


class EvidenceVerifier:
    """验证机会的证据是否成立。

    两级校验：
    1. 结构校验（必选）：至少有 1 条非空 excerpt 的 EvidenceRef
    2. 检索校验（可选）：若提供 KnowledgeBase，MissingEvidence 类机会
       必须在 KB 中检索不到强支撑（否则"证据不足"不成立）
    """

    def __init__(self, knowledge_base: Optional[Any] = None, kb_support_threshold: float = 0.75):
        self.knowledge_base = knowledge_base
        self.kb_support_threshold = kb_support_threshold

    def verify(self, opportunity: ResearchOpportunity) -> bool:
        """返回 True 表示机会成立。"""
        if not self._has_structural_evidence(opportunity):
            return False
        if not self._passes_retrieval_check(opportunity):
            return False
        return True

    def _has_structural_evidence(self, opportunity: ResearchOpportunity) -> bool:
        return any(e.excerpt and e.excerpt.strip() for e in opportunity.evidence)

    def _passes_retrieval_check(self, opportunity: ResearchOpportunity) -> bool:
        """MissingEvidence 机会：KB 中不应存在强支撑（否则机会不成立）。"""
        if self.knowledge_base is None:
            return True
        from paperwise.opportunity.models import OpportunityType
        if opportunity.type != OpportunityType.MISSING_EVIDENCE:
            return True

        claim = opportunity.related_entities[0] if opportunity.related_entities else opportunity.title
        try:
            results = self.knowledge_base.search(claim, top_k=3)
        except Exception:
            return True  # 检索失败不阻塞，保守放行
        for r in results:
            score = r.get("score", 0.0) if isinstance(r, dict) else getattr(r, "score", 0.0)
            if score >= self.kb_support_threshold:
                # KB 里找到了强支撑 -> "证据不足"不成立
                return False
        return True
