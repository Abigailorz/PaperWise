"""Episodic memory: records of meaningful user-agent tasks."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from paperwise.memory.storage import create_storage, StorageBackend


@dataclass
class Episode:
    """A single meaningful task / interaction record."""
    episode_id: str
    user_id: str
    session_id: Optional[str] = None
    task_type: str = ""
    goal: str = ""
    entities: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    outcome: str = ""
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Episode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class EpisodicMemory:
    """Store and query task-level episodes."""

    def __init__(self, storage_dir: Path, user_id: str = "default", backend: str = "sqlite"):
        self.user_id = user_id
        self.store = create_storage(backend, storage_dir)
        self.episodes: dict[str, Episode] = {}
        self._load()

    def record(
        self,
        task_type: str,
        goal: str,
        session_id: Optional[str] = None,
        entities: Optional[list[str]] = None,
        actions: Optional[list[str]] = None,
        findings: Optional[list[str]] = None,
        decisions: Optional[list[str]] = None,
        unresolved_questions: Optional[list[str]] = None,
        outcome: str = "",
        artifacts: Optional[list[str]] = None,
    ) -> Episode:
        eid = f"ep_{uuid.uuid4().hex[:8]}"
        episode = Episode(
            episode_id=eid,
            user_id=self.user_id,
            session_id=session_id,
            task_type=task_type,
            goal=goal,
            entities=entities or [],
            actions=actions or [],
            findings=findings or [],
            decisions=decisions or [],
            unresolved_questions=unresolved_questions or [],
            outcome=outcome,
            artifacts=artifacts or [],
        )
        self.episodes[eid] = episode
        self._save()
        return episode

    def get(self, episode_id: str) -> Optional[Episode]:
        return self.episodes.get(episode_id)

    def query(
        self,
        task_type: Optional[str] = None,
        entity: Optional[str] = None,
        limit: int = 10,
    ) -> list[Episode]:
        results = []
        for ep in reversed(list(self.episodes.values())):
            if task_type and ep.task_type != task_type:
                continue
            if entity and entity not in ep.entities:
                continue
            results.append(ep)
            if len(results) >= limit:
                break
        return results

    def update_outcome(self, episode_id: str, outcome: str) -> bool:
        ep = self.episodes.get(episode_id)
        if not ep:
            return False
        ep.outcome = outcome
        self._save()
        return True

    def _save(self) -> None:
        data = {"episodes": [e.to_dict() for e in self.episodes.values()]}
        try:
            self.store.put("episodes", "all", data)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"EpisodicMemory save failed: {e}")

    def _load(self) -> None:
        data = self.store.get("episodes", "all")
        if data and "episodes" in data:
            loaded = {}
            for raw in data["episodes"]:
                try:
                    ep = Episode.from_dict(raw)
                    loaded[ep.episode_id] = ep
                except Exception:
                    pass
            self.episodes = loaded
        else:
            self.episodes = {}
