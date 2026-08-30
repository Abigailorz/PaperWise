"""Tests for P4 Phase 1 — Research Opportunity detection.

验收标准映射：
① 4 类机会都能被检测     -> test_detects_*_opportunity
② 机会必须有 Evidence    -> test_opportunity_without_evidence_dropped
③ 不产生垃圾机会         -> test_empty_state_yields_nothing / low_confidence filtered
④ 防递归约束生效         -> test_depth_limit / budget / dedup / pending_only
"""

from paperwise.memory.research_state import ResearchState
from paperwise.opportunity import (
    OpportunityDetector,
    OpportunityPolicy,
    OpportunityStatus,
    OpportunityType,
    ResearchOpportunity,
    EvidenceRef,
)


def _state(**kwargs) -> ResearchState:
    base = dict(state_id="s1", user_id="u1", current_task="analyze paper")
    base.update(kwargs)
    return ResearchState(**base)


def _detector(**policy_kwargs) -> OpportunityDetector:
    return OpportunityDetector(policy=OpportunityPolicy(**policy_kwargs))


# ---------------------------------------------------------------- ① 4 类可检测

def test_detects_knowledge_gap():
    state = _state()
    state.add_gap(description="缺少 DINOv2 特征提取的基础知识", node_id="analyze_method", urgency="high")
    detector = _detector()
    opps = detector.detect(state)
    assert any(o.type == OpportunityType.KNOWLEDGE_GAP for o in opps)


def test_detects_missing_evidence():
    state = _state()
    findings = {
        "flagged_claims": [
            {"quote": "Our method improves accuracy by 12%", "evidence": "", "severity": "critical"},
        ]
    }
    detector = _detector()
    opps = detector.detect(state, reviewer_findings=findings)
    assert any(o.type == OpportunityType.MISSING_EVIDENCE for o in opps)


def test_detects_contradiction():
    state = _state()
    findings = {
        "flagged_claims": [
            {"quote": "X outperforms Y", "evidence": "This contradicts Table 3", "severity": "major"},
        ]
    }
    detector = _detector()
    opps = detector.detect(state, reviewer_findings=findings)
    assert any(o.type == OpportunityType.CONTRADICTION for o in opps)


def test_detects_method_complementarity():
    state = _state(
        current_paper="paper_a",
        related_papers=["semantic gaussian pruning for novel view synthesis"],
        current_task="uncertainty estimation for novel view synthesis rendering",
    )
    state.add_finding_from_node("method_analysis", "uncertainty-aware gaussian splatting", confidence=0.9)
    detector = _detector()
    opps = detector.detect(state)
    assert any(o.type == OpportunityType.METHOD_COMPLEMENTARITY for o in opps)


# ---------------------------------------------------------------- ② 必须有证据

def test_opportunity_without_evidence_dropped():
    detector = _detector()
    opp = ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title="无证据机会",
        description="没有任何证据",
        evidence=[],  # 空证据
    )
    assert not detector.verifier.verify(opp)


def test_opportunity_with_empty_excerpt_dropped():
    detector = _detector()
    opp = ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title="空摘录",
        description="证据摘录为空",
        evidence=[EvidenceRef(source_type="finding", source_id="x", excerpt="  ")],
    )
    assert not detector.verifier.verify(opp)


# ---------------------------------------------------------------- ③ 不产垃圾

def test_empty_state_yields_nothing():
    detector = _detector()
    assert detector.detect(_state()) == []


def test_missing_evidence_requires_non_empty_quote():
    """没有 quote 的 flagged claim 不产生机会（防止无锚点垃圾）。"""
    state = _state()
    findings = {"flagged_claims": [{"quote": "  ", "evidence": "", "severity": "critical"}]}
    detector = _detector()
    opps = detector.detect(state, reviewer_findings=findings)
    assert not any(o.type == OpportunityType.MISSING_EVIDENCE for o in opps)


def test_low_confidence_opportunity_filtered():
    """低于置信度阈值的机会被过滤。"""
    detector = _detector(min_confidence=0.99)  # 极高阈值
    state = _state()
    state.add_gap(description="某缺口", urgency="low")
    assert detector.detect(state) == []


# ---------------------------------------------------------------- ④ 防递归约束

def test_depth_limit_blocks_detection():
    """depth >= max_depth 时直接返回空（防止机会触发的 DAG 级联检测）。"""
    state = _state()
    state.add_gap(description="某缺口", urgency="high")
    detector = _detector(max_depth=1)
    assert detector.detect(state, depth=1) == []
    # depth=0 正常检测
    assert detector.detect(state, depth=0) != []


def test_budget_limits_opportunities():
    state = _state()
    for i in range(10):
        state.add_gap(description=f"缺口 {i}", urgency="high")
    detector = _detector(max_per_run=3)
    opps = detector.detect(state)
    assert len(opps) <= 3


def test_dedup_against_existing_pending():
    """已存在 pending 的同签名机会不重复产生。"""
    state = _state()
    state.add_gap(description="缺少 X 知识", urgency="high")
    detector = _detector()

    first = detector.detect(state)
    assert len(first) >= 1
    # 把第一轮机会落盘为 pending
    for o in first:
        state.add_opportunity(o)
    # 第二轮：同签名机会 novelty 被降权，且不应重复新增
    second = detector.detect(state)
    first_sigs = {o.signature() for o in first}
    assert not any(o.signature() in first_sigs and o.novelty == 1.0 for o in second)


def test_opportunities_are_pending_only():
    """Phase 1：所有机会只落 pending，绝不主动打断。"""
    state = _state()
    state.add_gap(description="缺口", urgency="high")
    detector = _detector()
    opps = detector.detect(state)
    assert opps
    assert all(o.status == OpportunityStatus.PENDING for o in opps)


# ---------------------------------------------------------------- 序列化与状态扩展

def test_opportunity_serialization_roundtrip():
    opp = ResearchOpportunity(
        type=OpportunityType.MISSING_EVIDENCE,
        title="t", description="d",
        evidence=[EvidenceRef(source_type="reviewer_claim", source_id="0", excerpt="e")],
        related_entities=["a", "b"],
    )
    restored = ResearchOpportunity.from_dict(opp.to_dict())
    assert restored.type == OpportunityType.MISSING_EVIDENCE
    assert restored.evidence[0].excerpt == "e"
    assert restored.signature() == opp.signature()


def test_research_state_opportunity_persistence(tmp_path):
    from paperwise.memory.research_state import ResearchStateManager
    mgr = ResearchStateManager(tmp_path, user_id="u1")
    state = mgr.new(current_task="t")
    state.add_opportunity(ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP, title="k", description="d",
        evidence=[EvidenceRef(source_type="knowledge_gap", source_id="g", excerpt="x")],
    ))
    mgr.save(state)

    mgr2 = ResearchStateManager(tmp_path, user_id="u1")
    loaded = mgr2.get()
    assert len(loaded.opportunities) == 1
    assert loaded.opportunities[0].type == OpportunityType.KNOWLEDGE_GAP
    assert loaded.get_active_opportunities()[0].status == OpportunityStatus.PENDING
