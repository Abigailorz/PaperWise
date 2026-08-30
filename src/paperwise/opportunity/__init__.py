"""P4 Phase 1 — Research Opportunity Engine。

从 DAG 执行结果中发现用户未明确提出、但可能有研究价值的机会。

- ``models.py``:    ResearchOpportunity / OpportunityType / OpportunityStatus / EvidenceRef
- ``rules.py``:     4 类确定性检测规则（KnowledgeGap / MissingEvidence / Contradiction / MethodComplementarity）
- ``evidence.py``:  EvidenceVerifier — 无证据的机会直接丢弃
- ``scorer.py``:    OpportunityScorer — confidence/importance/novelty 三维打分
- ``detector.py``:  OpportunityDetector — 编排 + 防递归五约束

Phase 1 边界：只检测并落盘 pending；不主动推送、不改 UI、不自动执行 DAG。
"""

from paperwise.opportunity.models import (
    EvidenceRef,
    OpportunityStatus,
    OpportunityType,
    ResearchOpportunity,
)
from paperwise.opportunity.detector import OpportunityDetector, OpportunityPolicy
from paperwise.opportunity.evidence import EvidenceVerifier
from paperwise.opportunity.scorer import OpportunityScorer
from paperwise.opportunity.action_planner import (
    ACTION_TO_NODE,
    ActionPlanner,
    ActionResult,
)

__all__ = [
    "EvidenceRef",
    "OpportunityStatus",
    "OpportunityType",
    "ResearchOpportunity",
    "OpportunityDetector",
    "OpportunityPolicy",
    "EvidenceVerifier",
    "OpportunityScorer",
    "ActionPlanner",
    "ActionResult",
    "ACTION_TO_NODE",
]
