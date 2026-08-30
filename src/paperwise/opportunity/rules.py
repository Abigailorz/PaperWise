"""P4 Phase 1 — 机会检测规则（确定性，不调用 LLM）。

每条规则输出**带证据**的候选 ResearchOpportunity。
设计取向：精确率优先（宁可少报，不产垃圾）。

输入来源（全部已存在于系统）：
- research_state.gaps            -> KnowledgeGap
- research_state.findings        -> Contradiction / MethodComplementarity
- reviewer findings.json         -> MissingEvidence / Contradiction / KnowledgeGap
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Protocol

from paperwise.opportunity.models import (
    EvidenceRef,
    OpportunityType,
    ResearchOpportunity,
)

if TYPE_CHECKING:
    from paperwise.memory.research_state import ResearchState


# reviewer evidence 中的矛盾标记词（中英文）
CONTRADICTION_MARKERS = (
    "contradict", "inconsistent", "does not match", "conflict",
    "相反", "矛盾", "不一致", "冲突", "相悖",
)


class DetectionRule(Protocol):
    """一条机会检测规则。"""

    opportunity_type: OpportunityType

    def apply(
        self,
        research_state: ResearchState,
        reviewer_findings: Optional[dict[str, Any]],
    ) -> list[ResearchOpportunity]: ...


def _urgency_score(urgency: str) -> float:
    return {"high": 0.85, "medium": 0.6, "low": 0.35}.get(urgency, 0.5)


class KnowledgeGapRule:
    """ResearchState 中的未解决 gap / reviewer 发现的 missing aspects -> 知识缺口机会。"""

    opportunity_type = OpportunityType.KNOWLEDGE_GAP

    def apply(self, research_state, reviewer_findings):
        opportunities: list[ResearchOpportunity] = []

        for gap in research_state.gaps:
            if not gap.description:
                continue
            opportunities.append(ResearchOpportunity(
                type=self.opportunity_type,
                title=f"知识缺口：{gap.description[:60]}",
                description=gap.description,
                evidence=[EvidenceRef(
                    source_type="knowledge_gap",
                    source_id=gap.gap_id,
                    excerpt=gap.description,
                    location=gap.node_id,
                )],
                related_entities=[gap.description[:80]],
                suggested_actions=["search_papers", "build_background"],
            ))

        # reviewer 发现的遗漏内容也是知识缺口
        for aspect in (reviewer_findings or {}).get("missing_aspects", []) or []:
            if not isinstance(aspect, str) or not aspect.strip():
                continue
            opportunities.append(ResearchOpportunity(
                type=self.opportunity_type,
                title=f"内容缺口：{aspect[:60]}",
                description=f"审查发现分析遗漏了：{aspect}",
                evidence=[EvidenceRef(
                    source_type="reviewer_claim",
                    source_id="missing_aspects",
                    excerpt=aspect,
                    location="review/findings.json",
                )],
                related_entities=[aspect[:80]],
                suggested_actions=["expand_evidence", "search_papers"],
            ))
        return opportunities


class MissingEvidenceRule:
    """reviewer flagged 的 critical/major claim -> 证据不足机会。

    只有带非空 quote 的 flagged claim 才算数（保证有证据锚点）。
    """

    opportunity_type = OpportunityType.MISSING_EVIDENCE

    def apply(self, research_state, reviewer_findings):
        opportunities: list[ResearchOpportunity] = []
        flagged = (reviewer_findings or {}).get("flagged_claims", []) or []

        for i, claim in enumerate(flagged):
            if not isinstance(claim, dict):
                continue
            severity = str(claim.get("severity", "")).lower()
            quote = str(claim.get("quote", "")).strip()
            if severity not in ("critical", "major") or not quote:
                continue
            opportunities.append(ResearchOpportunity(
                type=self.opportunity_type,
                title=f"证据不足：{quote[:60]}",
                description=(
                    f"报告中的论断缺乏论文证据支撑（severity={severity}）：{quote}"
                ),
                evidence=[EvidenceRef(
                    source_type="reviewer_claim",
                    source_id=f"flagged_claims[{i}]",
                    excerpt=quote,
                    location="review/findings.json",
                )],
                related_entities=[quote[:80]],
                suggested_actions=["verify_claim", "search_papers"],
            ))
        return opportunities


class ContradictionRule:
    """检测冲突：reviewer evidence 含矛盾标记，或 findings 中出现成对冲突。

    确定性实现，precision 优先：
    - 主路径：flagged claim 的 evidence 字段含矛盾标记词
    - 次路径：research_state.findings 中同 subject 但 confidence 两极分化的 claim
    """

    opportunity_type = OpportunityType.CONTRADICTION

    def apply(self, research_state, reviewer_findings):
        opportunities: list[ResearchOpportunity] = []
        flagged = (reviewer_findings or {}).get("flagged_claims", []) or []

        for i, claim in enumerate(flagged):
            if not isinstance(claim, dict):
                continue
            evidence_text = str(claim.get("evidence", ""))
            quote = str(claim.get("quote", "")).strip()
            if not quote:
                continue
            if any(m in evidence_text.lower() for m in CONTRADICTION_MARKERS) or \
               any(m in evidence_text for m in CONTRADICTION_MARKERS if any('一' <= c <= '鿿' for c in m)):
                opportunities.append(ResearchOpportunity(
                    type=self.opportunity_type,
                    title=f"潜在冲突：{quote[:60]}",
                    description=f"报告论断与论文证据可能冲突：{quote}；证据：{evidence_text[:120]}",
                    evidence=[EvidenceRef(
                        source_type="reviewer_claim",
                        source_id=f"flagged_claims[{i}]",
                        excerpt=evidence_text[:200],
                        location="review/findings.json",
                    )],
                    related_entities=[quote[:80]],
                    suggested_actions=["compare_evidence", "verify_claim"],
                ))
        return opportunities


class MethodComplementarityRule:
    """检测方法互补：当前论文方法 vs 相关论文中的不同方法，共享问题域。

    确定性 token 匹配，precision 优先：
    要求当前论文与某篇相关论文共享 >= 2 个问题域 token 且方法名不同。
    """

    opportunity_type = OpportunityType.METHOD_COMPLEMENTARITY

    # 方法名抽取：从 finding claim 中寻找 "method:" 前缀或使用 method 节点的 finding
    def apply(self, research_state, reviewer_findings):
        opportunities: list[ResearchOpportunity] = []
        if not research_state.related_papers:
            return opportunities

        current_methods = self._extract_methods(research_state)
        if not current_methods:
            return opportunities

        topic_tokens = self._topic_tokens(research_state.current_task)
        if len(topic_tokens) < 2:
            return opportunities

        for paper in research_state.related_papers:
            if not paper or paper == research_state.current_paper:
                continue
            paper_tokens = self._topic_tokens(paper)
            shared = topic_tokens & paper_tokens
            if len(shared) < 2:
                continue
            for method in current_methods:
                opportunities.append(ResearchOpportunity(
                    type=self.opportunity_type,
                    title=f"方法互补：{method[:40]} × {paper[:40]}",
                    description=(
                        f"当前论文方法 {method} 与相关论文 {paper} 共享问题域"
                        f"（{', '.join(sorted(shared))}），可能存在方法互补"
                    ),
                    evidence=[
                        EvidenceRef(
                            source_type="finding",
                            source_id="method_finding",
                            excerpt=method,
                            location=research_state.current_paper or "",
                        ),
                        EvidenceRef(
                            source_type="paper_section",
                            source_id=paper,
                            excerpt=f"shared topics: {', '.join(sorted(shared))}",
                            location=paper,
                        ),
                    ],
                    related_entities=[method, paper],
                    suggested_actions=["compare_methods", "suggest_experiment"],
                ))
        return opportunities

    @staticmethod
    def _extract_methods(research_state: ResearchState) -> list[str]:
        methods = []
        for finding in research_state.findings:
            if "method" in finding.node_id.lower() and finding.claim:
                methods.append(finding.claim[:80])
        return methods

    @staticmethod
    def _topic_tokens(text: str) -> set[str]:
        import re
        tokens = re.findall(r"[a-zA-Z]{4,}", (text or "").lower())
        stopwords = {"this", "that", "with", "from", "paper", "using", "based"}
        return {t for t in tokens if t not in stopwords}


#: Phase 1 启用的规则集
DEFAULT_RULES: list[DetectionRule] = [
    KnowledgeGapRule(),
    MissingEvidenceRule(),
    ContradictionRule(),
    MethodComplementarityRule(),
]
