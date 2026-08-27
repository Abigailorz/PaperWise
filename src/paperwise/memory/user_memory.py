"""用户记忆系统 — LLM 驱动提取 + Advanced JSON Cards

对应书中:
- 3.1 节：用户记忆系统
- 3.1.3 节：四种存储格式 (Advanced JSON Cards)
- 3.1.6 节：记忆压缩与整理
"""

import json, time
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field, asdict


@dataclass
class MemoryCard:
    """Advanced JSON Card — 记忆最小单元"""
    card_id: str
    category: str       # preference | fact | relationship | experience | knowledge
    data: dict
    backstory: str = ""
    person: str = "user"
    relationship: str = "self"
    confidence: float = 0.8
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    last_verified: str = ""
    tags: list[str] = field(default_factory=list)
    version: int = 1     # 更新版本号
    source: str = "conversation"   # conversation | explicit | inference | feedback
    status: str = "active"         # active | stale | conflicting | archived
    user_id: str = "default"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryCard":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class UserMemory:
    """用户记忆管理器 — LLM 驱动的记忆提取 + 去重合并。

    五种记忆类别：
    - preference/fact/relationship/experience/knowledge

    存储后端：默认 SQLite（可切换为 JSON/Redis 等）。
    """

    CATEGORIES = ["preference", "fact", "relationship", "experience", "knowledge"]

    def __init__(self, storage_dir: Path = None, backend: str = "sqlite", user_id: str = "default"):
        self.user_id = user_id
        self.storage_dir = Path(storage_dir or Path.home() / ".paperwise" / "memory")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        from paperwise.memory.storage import create_storage
        self.store = create_storage(backend, self.storage_dir)
        self.cards: dict[str, MemoryCard] = {}
        self._load()
        # Tag loaded cards with user_id if missing
        for card in self.cards.values():
            if not card.user_id:
                card.user_id = user_id

    # ══════════ CRUD ══════════

    def remember(self, category: str, data: dict, backstory: str = "",
                 confidence: float = 0.8, tags: list[str] = None,
                 person: str = "user", relationship: str = "self",
                 source: str = "conversation", user_id: Optional[str] = None) -> MemoryCard:
        """记住一条信息。自动去重：同类别同 key 的数据会更新而非新增。"""
        import uuid

        # 去重检查：同类别下相同 data key
        existing = self._find_similar(category, data)
        if existing:
            # 合并更新
            existing.data.update(data)
            existing.backstory = backstory or existing.backstory
            existing.confidence = max(existing.confidence, confidence)
            existing.last_verified = datetime.now().isoformat()
            existing.last_confirmed_at = datetime.now().isoformat()
            existing.version += 1
            existing.tags = list(set((existing.tags or []) + (tags or [])))
            if source != "conversation":
                existing.source = source
            existing.status = "active"
            existing.tags = list(set((existing.tags or []) + (tags or [])))
            self._save()
            return existing

        # 新增
        cid = f"mem_{category}_{uuid.uuid4().hex[:8]}"
        card = MemoryCard(
            card_id=cid, category=category, data=data,
            backstory=backstory, confidence=confidence,
            tags=tags or [], person=person, relationship=relationship,
            source=source, user_id=(user_id if user_id is not None else self.user_id), status="active",
        )
        self.cards[cid] = card
        self._save()
        return card

    def recall(self, card_id: str) -> Optional[MemoryCard]:
        return self.cards.get(card_id)

    def forget(self, card_id: str) -> bool:
        if card_id in self.cards:
            del self.cards[card_id]; self._save(); return True
        return False

    # ══════════ LLM 驱动的记忆提取 ══════════

    async def extract_from_conversation(self, llm_client, user_msg: str,
                                        agent_response: str) -> list[MemoryCard]:
        """使用 LLM 从对话中提取结构化记忆。

        替代旧的关键词匹配方式。LLM 能理解语义，准确率远高于规则。
        """
        prompt = f"""分析以下对话，提取值得长期记住的用户信息。

用户消息：{user_msg[:500]}
助手回复：{agent_response[:500]}

提取规则：
1. 只提取对未来对话有用的持久信息
2. 不要提取临时的、单次性的请求
3. 每一条记忆都需要有明确的证据支持
4. 如果用户在对话中明确提到了自己的研究方向、研究领域或研究兴趣，务必把它们填入 research_fields

返回 JSON 格式（research_fields 可为空数组）：
{{
  "research_fields": ["方向1", "方向2"],
  "memories": [
    {{
      "category": "preference|fact|relationship|experience|knowledge",
      "key": "记忆的唯一标识键",
      "value": "记忆的具体内容",
      "backstory": "为什么记住这个（对话中的证据）",
      "confidence": 0.0-1.0
    }}
  ]
}}

可提取的信息类型：
- preference: 用户偏好（"我喜欢详细的分析"、"以后用中文回复"）
- fact: 用户事实（"我的研究方向是CV"、"我在读博士"）
- experience: 交互经验（"上次帮我分析过Transformer论文"）
- knowledge: 分析的论文信息

如果没有值得长期记住的信息，memories 返回空列表。"""
        try:
            resp = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=1000,
            )
            result = json.loads(resp.content)
            saved = []

            # 研究方向单独沉淀为结构化的 preference 卡，供推荐器读取
            import re as _re
            fields = result.get("research_fields") or []
            fields = [str(f).strip() for f in fields if str(f).strip()]
            if not fields:
                m = _re.search(
                    r"(?:研究方向|研究领域|研究兴趣)\s*(?:是|为|:：)\s*([^\n。，,;]+)",
                    user_msg,
                )
                if m:
                    fields = [
                        p.strip() for p in _re.split(r"[、，,;；/]", m.group(1))
                        if p.strip()
                    ]
            if fields:
                self.remember(
                    category="preference",
                    data={"research_fields": json.dumps(fields, ensure_ascii=False)},
                    backstory="从对话中提取的研究方向",
                    confidence=0.85,
                    tags=["research", "llm_extracted"],
                )

            for m in result.get("memories", []):
                card = self.remember(
                    category=m.get("category", "fact"),
                    data={m.get("key", "info"): m.get("value", "")},
                    backstory=m.get("backstory", ""),
                    confidence=m.get("confidence", 0.7),
                    tags=["llm_extracted"],
                )
                saved.append(card)
            return saved
        except Exception as e:
            import logging
            logging.getLogger("paperwise").debug(f"Memory extraction failed: {e}")
            return []

    # ══════════ 查询 ══════════

    def query(self, category: str = None, tags: list[str] = None,
              person: str = None, min_confidence: float = 0.0,
              limit: int = 20) -> list[MemoryCard]:
        results = list(self.cards.values())
        if category: results = [c for c in results if c.category == category]
        if tags: results = [c for c in results if any(t in c.tags for t in tags)]
        if person: results = [c for c in results if c.person == person]
        if min_confidence > 0: results = [c for c in results if c.confidence >= min_confidence]
        results.sort(key=lambda c: c.timestamp, reverse=True)
        return results[:limit]

    def get_preferences(self) -> list[MemoryCard]:
        return self.query(category="preference")

    def get_facts(self) -> list[MemoryCard]:
        return self.query(category="fact")

    def get_paper_history(self) -> list[MemoryCard]:
        """获取分析过的论文历史"""
        return self.query(category="knowledge", tags=["paper"])

    # ══════════ 维护 ══════════

    def detect_conflicts(self) -> list[tuple[MemoryCard, MemoryCard]]:
        """检测冲突记忆（同 key 不同 value）"""
        conflicts = []
        cards = list(self.cards.values())
        for i, c1 in enumerate(cards):
            for c2 in cards[i + 1:]:
                if c1.category == c2.category:
                    common = set(c1.data.keys()) & set(c2.data.keys())
                    for k in common:
                        if c1.data[k] != c2.data[k]:
                            conflicts.append((c1, c2))
        return conflicts

    def resolve_conflicts(self, keep: str, drop: str):
        """解决冲突：保留 keep，删除 drop"""
        if drop in self.cards:
            del self.cards[drop]
            self.cards[keep].version += 1
            self._save()

    def cleanup(self, max_age_days: int = 90, min_confidence: float = 0.3):
        """清理过期或低质量记忆"""
        cutoff = time.time() - max_age_days * 86400
        to_remove = []
        for cid, card in self.cards.items():
            ts = datetime.fromisoformat(card.timestamp).timestamp()
            if ts < cutoff or card.confidence < min_confidence:
                to_remove.append(cid)
        for cid in to_remove:
            del self.cards[cid]
        if to_remove:
            self._save()

    @staticmethod
    def _card_ts(card: "MemoryCard") -> float:
        """安全解析卡片时间戳。"""
        try:
            return datetime.fromisoformat(card.timestamp).timestamp()
        except (ValueError, TypeError):
            return 0.0

    def consolidate(self, max_age_days: int = 90, min_confidence: float = 0.3,
                    max_per_category: int = 30) -> dict:
        """周期性记忆整合：清理过期 → 合并重复 → 控制类别规模。

        对应书中 3.1.6 节记忆压缩与整理：
        - 低置信度 / 超期记忆 → 删除
        - 同类别同 key 的记忆 → 合并（保留置信度最高版本）
        - 单类别超限 → 降级删除最旧、最低置信度的卡

        Returns:
            {"removed": int, "merged": int, "demoted": int,
             "kept": int, "total_before": int}
        """
        before = len(self.cards)
        removed = 0
        merged = 0
        demoted = 0

        # 1. 清理低置信度 / 超期记忆
        cutoff = time.time() - max_age_days * 86400
        for cid in list(self.cards):
            card = self.cards[cid]
            if self._card_ts(card) < cutoff or card.confidence < min_confidence:
                del self.cards[cid]
                removed += 1

        # 2. 合并同类别同 key 集合的重复卡片
        seen: dict[tuple, str] = {}
        for cid in list(self.cards):
            card = self.cards[cid]
            sig = (card.category, tuple(sorted(card.data.keys())))
            if sig in seen:
                keep = self.cards[seen[sig]]
                keep.data.update(card.data)
                keep.backstory = keep.backstory or card.backstory
                keep.confidence = max(keep.confidence, card.confidence)
                keep.tags = list(set((keep.tags or []) + (card.tags or [])))
                keep.last_verified = card.last_verified or keep.last_verified
                keep.version += 1
                del self.cards[cid]
                merged += 1
            else:
                seen[sig] = cid

        # 3. 类别规模控制
        from collections import Counter
        cat_counts = Counter(c.category for c in self.cards.values())
        for cat, count in cat_counts.items():
            if count <= max_per_category:
                continue
            excess = count - max_per_category
            cards = sorted(
                (c for c in self.cards.values() if c.category == cat),
                key=lambda c: (c.confidence, self._card_ts(c)),
            )
            for card in cards[:excess]:
                del self.cards[card.card_id]
                demoted += 1

        if removed or merged or demoted:
            self._save()
        return {
            "removed": removed, "merged": merged, "demoted": demoted,
            "kept": len(self.cards), "total_before": before,
        }

    def maybe_consolidate(self, interval_days: int = 7) -> dict:
        """按时间间隔触发整合（避免每次会话都全量执行）。"""
        try:
            last = self.store.get("consolidation", "last_run")
        except Exception:
            last = None
        if last:
            try:
                last_ts = datetime.fromisoformat(last.get("at", "")).timestamp()
            except (ValueError, TypeError):
                last_ts = 0
            if time.time() - last_ts < interval_days * 86400:
                return {"skipped": True, "reason": "within_interval"}
        report = self.consolidate()
        report["skipped"] = False
        try:
            self.store.put("consolidation", "last_run",
                           {"at": datetime.now().isoformat()})
        except Exception:
            pass
        return report

    # ══════════ 上下文注入 ══════════

    def to_context_string(self, limit: int = 8) -> str:
        """生成注入 Agent system prompt 的记忆上下文。

        格式化为结构化 XML，Agent 可以"瞥一眼"快速读取（对应书中 2.6 节状态栏设计）。
        """
        cards = self.query(min_confidence=0.5, limit=limit)
        if not cards:
            return ""

        by_category = {}
        for c in cards:
            by_category.setdefault(c.category, []).append(c)

        lines = ["<user_memory>"]
        for cat in self.CATEGORIES:
            if cat in by_category:
                for c in by_category[cat][:3]:
                    data_str = ", ".join(f"{k}={v}" for k, v in c.data.items())
                    lines.append(f"  <{cat} confidence='{c.confidence:.0%}'>")
                    lines.append(f"    {data_str}")
                    lines.append(f"  </{cat}>")
        lines.append("</user_memory>")
        return "\n".join(lines)

    def stats(self) -> dict:
        cats = {}
        for c in self.cards.values():
            cats[c.category] = cats.get(c.category, 0) + 1
        return {"total": len(self.cards), "by_category": cats,
                "conflicts": len(self.detect_conflicts())}

    # ══════════ 内部 ══════════

    def _find_similar(self, category: str, data: dict) -> Optional[MemoryCard]:
        """查找同类别下「同名且同值」key 的记忆（用于去重合并）。

        只在同名 key 的值也相同时才合并，避免把「不同论文的 topics/title」
        这类共享 key 名但值不同的卡片误合并。
        """
        for card in self.cards.values():
            if card.category != category or card.status == "archived":
                continue
            for key, value in data.items():
                if key in card.data and card.data.get(key) == value:
                    return card
        return None

    def update_status(self, card_id: str, status: str) -> bool:
        """Update memory status (active | stale | conflicting | archived)."""
        card = self.cards.get(card_id)
        if not card:
            return False
        card.status = status
        card.last_confirmed_at = datetime.now().isoformat()
        self._save()
        return True

    def apply_feedback(self, card_id: str, delta: float) -> bool:
        """Adjust memory confidence by user feedback."""
        card = self.cards.get(card_id)
        if not card:
            return False
        card.confidence = max(0.0, min(1.0, card.confidence + delta))
        card.last_confirmed_at = datetime.now().isoformat()
        self._save()
        return True

    def _save(self):
        """保存到存储后端（SQLite/JSON）。"""
        try:
            data = {"cards": [c.to_dict() for c in self.cards.values()]}
            self.store.put("cards", "all", data)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"Memory save failed: {e}")

    def _load(self):
        """从存储后端加载（自动迁移旧 JSON）。"""
        data = self.store.get("cards", "all")
        if data and "cards" in data:
            loaded = {}
            for c in data["cards"]:
                try:
                    card = MemoryCard.from_dict(c)
                    loaded[card.card_id] = card
                except Exception:
                    pass
            self.cards = loaded
        else:
            self.cards = {}
