"""Incremental session memory with a persisted cursor.

SessionMemory is separate from HierarchicalMemory: the latter compresses the
message view; this module extracts the semantic delta that can survive
compression and resume after a crash.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from paperwise.core.types import Message, Role
from paperwise.memory.storage import create_storage


@dataclass
class SessionDelta:
    delta_id: str
    from_message_id: Optional[str]
    to_message_id: Optional[str]
    message_ids: list[str] = field(default_factory=list)
    token_delta: int = 0
    summary: str = ""
    triggers: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionDelta":
        return cls(
            delta_id=data["delta_id"],
            from_message_id=data.get("from_message_id"),
            to_message_id=data.get("to_message_id"),
            message_ids=list(data.get("message_ids", [])),
            token_delta=int(data.get("token_delta", 0)),
            summary=data.get("summary", ""),
            triggers=list(data.get("triggers", [])),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


@dataclass
class SessionState:
    session_id: str
    last_processed_message_id: Optional[str] = None
    summary: str = ""
    processed_tokens: int = 0
    committed_delta_ids: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        return cls(
            session_id=data.get("session_id", "default"),
            last_processed_message_id=data.get("last_processed_message_id"),
            summary=data.get("summary", ""),
            processed_tokens=int(data.get("processed_tokens", 0)),
            committed_delta_ids=list(data.get("committed_delta_ids", []))[-128:],
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


class SessionMemory:
    """Observe transcript, extract unprocessed deltas, and commit idempotently."""

    def __init__(
        self,
        storage_dir: Path,
        session_id: str = "default",
        user_id: str = "default",
        backend: str = "sqlite",
        token_threshold: int = 2_000,
        max_summary_chars: int = 6_000,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.token_threshold = max(int(token_threshold), 1)
        self.max_summary_chars = max(int(max_summary_chars), 1)
        self.store = create_storage(backend, Path(storage_dir))
        self._messages: list[Message] = []
        self.state = self._load_state()

    def observe(self, message: Message) -> str:
        if not message.message_id:
            message.message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self._messages.append(message)
        self._save_message(message)
        return message.message_id

    def observe_all(self, messages: Iterable[Message]) -> list[str]:
        return [self.observe(message) for message in messages]

    def _pending_messages(self, from_message_id: Optional[str] = None) -> list[Message]:
        messages = self._messages or self._load_messages()
        self._messages = messages
        cursor = self.state.last_processed_message_id if from_message_id is None else from_message_id
        if cursor is None:
            return list(messages)
        try:
            index = next(i for i, message in enumerate(messages) if message.message_id == cursor)
        except StopIteration:
            processed = set(self.state.committed_delta_ids)
            return [message for message in messages if message.message_id not in processed]
        return messages[index + 1:]

    def extract_delta(self, from_message_id: Optional[str] = None) -> SessionDelta:
        pending = self._pending_messages(from_message_id)
        token_delta = sum(len(message.content or "") // 3 for message in pending)
        cursor = (
            from_message_id
            if from_message_id is not None
            else self.state.last_processed_message_id
        )
        return SessionDelta(
            delta_id=f"delta_{pending[0].message_id}_{pending[-1].message_id}" if pending
            else f"delta_empty_{cursor or 'start'}",
            from_message_id=cursor,
            to_message_id=pending[-1].message_id if pending else cursor,
            message_ids=[message.message_id for message in pending if message.message_id],
            token_delta=token_delta,
            summary=self._summarize(pending),
        )

    def should_extract(
        self,
        messages: Iterable[Message] | None = None,
        *,
        semantic_event: bool = False,
        before_compaction: bool = False,
    ) -> tuple[bool, list[str]]:
        if messages is not None:
            self.observe_all(messages)
        triggers: list[str] = []
        pending = self._pending_messages()
        if before_compaction:
            triggers.append("before_compaction")
        if semantic_event:
            triggers.append("semantic_event")
        if sum(len(message.content or "") // 3 for message in pending) >= self.token_threshold:
            triggers.append("token_delta")
        return bool(triggers), triggers

    def maybe_extract(
        self,
        messages: Iterable[Message] | None = None,
        *,
        semantic_event: bool = False,
        before_compaction: bool = False,
    ) -> tuple[SessionDelta | None, list[str]]:
        triggered, triggers = self.should_extract(
            messages,
            semantic_event=semantic_event,
            before_compaction=before_compaction,
        )
        return (self.extract_delta(), triggers) if triggered else (None, triggers)

    def commit(self, delta: SessionDelta) -> None:
        """Idempotently advance the cursor; duplicate or stale deltas are no-ops."""
        if not delta.message_ids or delta.delta_id in self.state.committed_delta_ids:
            return
        processed = set(self.state.committed_delta_ids)
        if delta.to_message_id in processed:
            return

        summary = "\n".join(part for part in (self.state.summary, delta.summary) if part)
        if len(summary) > self.max_summary_chars:
            head = int(self.max_summary_chars * 0.8)
            tail = self.max_summary_chars - head - 30
            omitted = len(summary) - head - tail
            summary = summary[:head] + f"\n... ({omitted} chars omitted) ...\n" + summary[-tail:]

        self.state.summary = summary
        self.state.last_processed_message_id = delta.to_message_id
        self.state.processed_tokens += delta.token_delta
        self.state.committed_delta_ids = (self.state.committed_delta_ids + [delta.delta_id])[-128:]
        self.state.updated_at = datetime.now().isoformat()
        self._save_state()

    def _summarize(self, pending: list[Message]) -> str:
        lines = []
        for message in pending:
            content = (message.content or "").replace("\n", " ")
            if len(content) > 500:
                content = content[:500] + "..."
            if message.tool_calls:
                names = ", ".join(call.name for call in message.tool_calls)
                content += f" [tools: {names}]"
            lines.append(f"[{message.role.value}] {content}")
        return "\n".join(lines)

    def _load_state(self) -> SessionState:
        data = self.store.get("session_memory", self.session_id)
        if not data:
            return SessionState(session_id=self.session_id)
        try:
            return SessionState.from_dict(data.get("state", {}))
        except Exception:
            return SessionState(session_id=self.session_id)

    def _save_state(self) -> None:
        self.store.put("session_memory", self.session_id, {"state": self.state.to_dict()})

    def _load_messages(self) -> list[Message]:
        records: list[tuple[int, Message]] = []
        for key in self.store.list_keys("session_transcript", f"{self.session_id}:"):
            data = self.store.get("session_transcript", key) or {}
            try:
                message = Message(
                    role=Role(data.get("role", "user")),
                    content=data.get("content"),
                    message_id=data.get("message_id"),
                    tool_call_id=data.get("tool_call_id"),
                )
                records.append((int(data.get("sequence", 0)), message))
            except Exception:
                continue
        records.sort(key=lambda item: item[0])
        return [message for _, message in records]

    def _save_message(self, message: Message) -> None:
        sequence = len(self._messages)
        self.store.put(
            "session_transcript",
            f"{self.session_id}:{message.message_id}",
            {
                "sequence": sequence,
                "session_id": self.session_id,
                "user_id": self.user_id,
                "role": message.role.value,
                "content": message.content,
                "message_id": message.message_id,
                "tool_call_id": message.tool_call_id,
            },
        )
