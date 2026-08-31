"""P4 Phase 1 — Research Opportunity 领域对象。

Opportunity 是一级领域对象（非 Recommendation 子类），
是 P4 → P5 Research Graph 的衔接点。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class OpportunityType(str, Enum):
    """第一版锁死 4 种类型。"""

    KNOWLEDGE_GAP = "knowledge_gap"
    MISSING_EVIDENCE = "missing_evidence"
    CONTRADICTION = "contradiction"
    METHOD_COMPLEMENTARITY = "method_complementarity"


class OpportunityStatus(str, Enum):
    PENDING = "pending"      # 落盘待处理（Phase 1 的唯一终态）
    ACTING = "acting"        # Action DAG 执行中（Phase 2）
    ACTED = "acted"          # 已执行
    DISMISSED = "dismissed"  # 用户/策略拒绝
    EXPIRED = "expired"      # 超期未处理


@dataclass
class EvidenceRef:
    """支撑机会的一条证据引用。无证据的机会会被直接丢弃。"""

    source_type: str          # finding | knowledge_gap | reviewer_claim | kb_chunk | paper_section
    source_id: str            # gap_id / finding node_id / claim 索引 / chunk id
    excerpt: str = ""         # 证据摘录
    location: str = ""        # 位置（node_id / 文件 / 行号）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRef":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ResearchOpportunity:
    """一次被探测到的、可能有研究价值的机会。"""

    type: OpportunityType
    title: str
    description: str
    opportunity_id: str = field(default_factory=lambda: f"opp_{uuid.uuid4().hex[:8]}")
    user_id: str = "default"
    session_id: Optional[str] = None

    evidence: list[EvidenceRef] = field(default_factory=list)
    confidence: float = 0.0    # 证据强度
    importance: float = 0.0    # 对当前研究的价值
    novelty: float = 1.0       # 是否已知/重复

    related_entities: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    question: str = ""

    status: OpportunityStatus = OpportunityStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def signature(self) -> str:
        """去重签名：同类型 + 相同相关实体 -> 同一机会。"""
        entities = "|".join(sorted({e.lower().strip() for e in self.related_entities}))
        raw = f"{self.type.value}::{entities}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchOpportunity":
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        kwargs["type"] = OpportunityType(kwargs["type"])
        kwargs["status"] = OpportunityStatus(kwargs.get("status", "pending"))
        kwargs["evidence"] = [EvidenceRef.from_dict(e) for e in kwargs.get("evidence", [])]
        return cls(**kwargs)
