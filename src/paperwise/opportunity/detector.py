"""P4 Phase 1 — Opportunity Detector 编排器。

数据流：
    rules (候选) -> evidence (验证) -> scorer (打分过滤) -> policy (防递归) -> pending

防递归五约束在此强制：
1. depth limit     — 机会触发的 Action DAG 不再级联检测（depth >= max_depth 直接返回空）
2. budget          — 单次检测最多产出 max_per_run 个机会
3. confidence      — scorer 的 min_confidence 过滤低置信机会
4. cooldown+dedup  — 同签名机会在冷却期内/仍 pending 时不重复
5. interrupt       — Phase 1 只落 pending，绝不主动打断用户
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

from paperwise.opportunity.evidence import EvidenceVerifier
from paperwise.opportunity.evidence_bridge import EvidenceOpportunityBridge
from paperwise.opportunity.models import (
    OpportunityStatus,
    ResearchOpportunity,
)
from paperwise.opportunity.rules import DEFAULT_RULES, DetectionRule
from paperwise.opportunity.scorer import OpportunityScorer

if TYPE_CHECKING:
    from paperwise.memory.research_state import ResearchState


@dataclass
class OpportunityPolicy:
    """机会检测与触发的硬约束。"""

    max_per_run: int = 3             # 单次检测预算
    max_depth: int = 1               # 机会触发的 DAG 深度上限（depth>=1 不再检测）
    min_confidence: float = 0.5      # 置信度阈值
    # Phase 1：不主动打断用户，所有机会只落 pending
    allow_proactive_interrupt: bool = False


class OpportunityDetector:
    """机会检测器：规则 -> 证据 -> 打分 -> 防递归。"""

    def __init__(
        self,
        policy: Optional[OpportunityPolicy] = None,
        rules: Optional[list[DetectionRule]] = None,
        verifier: Optional[EvidenceVerifier] = None,
        scorer: Optional[OpportunityScorer] = None,
        evidence_bridge: Optional[EvidenceOpportunityBridge] = None,
    ):
        self.policy = policy or OpportunityPolicy()
        self.rules = rules if rules is not None else list(DEFAULT_RULES)
        self.verifier = verifier or EvidenceVerifier()
        self.scorer = scorer or OpportunityScorer(min_confidence=self.policy.min_confidence)
        self.evidence_bridge = evidence_bridge or EvidenceOpportunityBridge()

    def detect(
        self,
        research_state: ResearchState,
        reviewer_findings: Optional[dict[str, Any]] = None,
        existing: Optional[Iterable[ResearchOpportunity]] = None,
        depth: int = 0,
        evidence_packs: Optional[Iterable[Any]] = None,
    ) -> list[ResearchOpportunity]:
        """运行检测，返回落盘为 pending 的机会列表。

        参数：
            depth: 当前 DAG 深度。>= max_depth 时直接返回空（防递归）。
            existing: 已存在的机会（用于去重/novelty），默认取 research_state.opportunities。
        """
        # 约束 2（depth limit）
        if depth >= self.policy.max_depth:
            return []

        if existing is None:
            existing = getattr(research_state, "opportunities", []) or []
        existing_list = list(existing)

        # 只对仍活跃的机会做去重（pending/acting 的同类机会不重复触发）
        active = [
            o for o in existing_list
            if o.status in (OpportunityStatus.PENDING, OpportunityStatus.ACTING)
        ]

        # 1. 规则检测
        candidates: list[ResearchOpportunity] = []
        for rule in self.rules:
            try:
                candidates.extend(rule.apply(research_state, reviewer_findings))
            except Exception:
                continue  # 单条规则失败不阻塞其他规则

        # P4: Evidence Pack 不是检索结果缓存，而是机会推理的直接输入。
        try:
            candidates.extend(self.evidence_bridge.derive(evidence_packs or []))
            candidates = self.evidence_bridge.attach(candidates, evidence_packs or [])
        except Exception:
            pass

        # 2. 证据验证（反幻觉）
        verified = [c for c in candidates if self.verifier.verify(c)]

        # 3. 打分 + 置信度过滤（约束 3）+ novelty 去重（约束 4 一部分）
        scored = self.scorer.score(verified, existing=active)

        # 4. 签名单级去重（同一次检测内部）
        seen_sigs: set[str] = set()
        deduped: list[ResearchOpportunity] = []
        for opp in scored:
            sig = opp.signature()
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            deduped.append(opp)

        # 约束 1（budget）
        result = deduped[: self.policy.max_per_run]

        # 约束 5（interrupt）：Phase 1 强制 pending
        for opp in result:
            opp.status = OpportunityStatus.PENDING
            opp.user_id = research_state.user_id
            opp.session_id = research_state.session_id

        return result
