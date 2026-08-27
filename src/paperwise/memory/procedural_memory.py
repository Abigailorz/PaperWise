"""Procedural memory: user-agent collaboration patterns."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from paperwise.memory.storage import create_storage


@dataclass
class ProceduralPattern:
    """A preferred way of working with the agent for a task type."""
    pattern_id: str
    user_id: str
    task_type: str
    context_signature: dict[str, Any] = field(default_factory=dict)
    preferred_steps: list[str] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)
    success_rate: float = 0.0
    last_used: str = field(default_factory=lambda: datetime.now().isoformat())
    use_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProceduralPattern":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ProceduralMemory:
    """Store and retrieve user-agent collaboration patterns."""

    def __init__(self, storage_dir: Path, user_id: str = "default", backend: str = "sqlite"):
        self.user_id = user_id
        self.store = create_storage(backend, storage_dir)
        self.patterns: dict[str, ProceduralPattern] = {}
        self._load()

    def learn(
        self,
        task_type: str,
        preferred_steps: list[str],
        preferences: Optional[dict[str, Any]] = None,
        context_signature: Optional[dict[str, Any]] = None,
        success: bool = True,
    ) -> ProceduralPattern:
        # Update existing if same task_type and similar signature, else create.
        for pat in self.patterns.values():
            if pat.task_type == task_type and self._signature_match(pat.context_signature, context_signature or {}):
                pat.preferred_steps = preferred_steps
                if preferences:
                    pat.preferences.update(preferences)
                pat.use_count += 1
                pat.success_rate = (pat.success_rate * (pat.use_count - 1) + (1.0 if success else 0.0)) / pat.use_count
                pat.last_used = datetime.now().isoformat()
                self._save()
                return pat

        pid = f"proc_{uuid.uuid4().hex[:8]}"
        pattern = ProceduralPattern(
            pattern_id=pid,
            user_id=self.user_id,
            task_type=task_type,
            context_signature=context_signature or {},
            preferred_steps=preferred_steps,
            preferences=preferences or {},
            success_rate=1.0 if success else 0.0,
            use_count=1,
        )
        self.patterns[pid] = pattern
        self._save()
        return pattern

    def get(self, pattern_id: str) -> Optional[ProceduralPattern]:
        return self.patterns.get(pattern_id)

    def match(self, task_type: str, context: Optional[dict[str, Any]] = None) -> list[ProceduralPattern]:
        context = context or {}
        scored = []
        for pat in self.patterns.values():
            if pat.task_type != task_type:
                continue
            score = self._signature_match_score(pat.context_signature, context)
            scored.append((score, pat))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    def _signature_match(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        if not a or not b:
            return True
        overlap = sum(1 for k, v in a.items() if k in b and b[k] == v)
        return overlap >= min(len(a), len(b)) * 0.5

    def _signature_match_score(self, pattern_sig: dict[str, Any], context: dict[str, Any]) -> float:
        if not pattern_sig:
            return 0.0
        hits = sum(1 for k, v in pattern_sig.items() if k in context and context[k] == v)
        return hits / len(pattern_sig)

    def _save(self) -> None:
        data = {"patterns": [p.to_dict() for p in self.patterns.values()]}
        try:
            self.store.put("procedures", "all", data)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"ProceduralMemory save failed: {e}")

    def _load(self) -> None:
        data = self.store.get("procedures", "all")
        if data and "patterns" in data:
            loaded = {}
            for raw in data["patterns"]:
                try:
                    pat = ProceduralPattern.from_dict(raw)
                    loaded[pat.pattern_id] = pat
                except Exception:
                    pass
            self.patterns = loaded
        else:
            self.patterns = {}
