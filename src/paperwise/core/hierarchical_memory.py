"""Hierarchical memory system -- dynamic soft compression instead of hard truncation.

Design:
- recent_turns: last N full conversation turns.
- working_summary: task-level summary (key conclusions, failures, next steps).
- long_term_summary: project-level facts and user preferences.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from paperwise.core.types import Message, Role


class HierarchicalMemory:
    """Three-tier context memory manager."""

    def __init__(self,
                 workspace: Path,
                 llm_client=None,
                 max_recent_chars: int = 24_000,
                 max_working_chars: int = 4_000,
                 max_long_term_chars: int = 2_000):
        self.workspace = Path(workspace)
        self.llm = llm_client
        self.max_recent_chars = max_recent_chars
        self.max_working_chars = max_working_chars
        self.max_long_term_chars = max_long_term_chars

        self.recent_turns: list[Message] = []
        self.working_summary: str = ""
        self.long_term_summary: str = ""
        self._plan_text: str = ""

    def build_initial_context(self,
                              system_prompt: str,
                              task: str,
                              workspace: Path,
                              plan_text: str = "") -> list[Message]:
        """Build the initial context and reset the three tiers."""
        self.recent_turns = []
        self.working_summary = ""
        self.long_term_summary = ""
        self._plan_text = plan_text

        parts = ["<agent_identity_and_rules>", system_prompt, "</agent_identity_and_rules>"]
        if plan_text:
            parts += ["\n<current_plan>", plan_text, "</current_plan>"]
        parts += [
            "\n<task>", task, "</task>",
            f"\n<workspace>\n  working_dir: {workspace}\n</workspace>",
        ]
        return [Message(role=Role.SYSTEM, content="\n".join(parts))]

    def add_turn(self, msg: Message) -> None:
        """Add a full turn to recent memory."""
        self.recent_turns.append(msg)

    @property
    def _recent_chars(self) -> int:
        return sum(len(m.content or "") for m in self.recent_turns)

    @property
    def _working_chars(self) -> int:
        return len(self.working_summary)

    @property
    def _long_term_chars(self) -> int:
        return len(self.long_term_summary)

    def estimate_token_usage(self, extra_chars: int = 0) -> int:
        """Track total characters; approximate tokens as chars / 3."""
        total = self._recent_chars + self._working_chars + self._long_term_chars + extra_chars
        return total // 3

    def maybe_compress(self, token_limit: int, token_used: int) -> bool:
        """Decide whether to compress based on remaining token budget."""
        remaining = max(token_limit - token_used, 0)
        soft_limit = int(token_limit * 0.75)
        hard_limit = int(token_limit * 0.9)

        compressed = False
        if token_used > hard_limit:
            compressed = self._compress_one_level() or compressed
            compressed = self._compress_one_level() or compressed
        elif token_used > soft_limit:
            compressed = self._compress_one_level() or compressed
        return compressed

    def _compress_one_level(self) -> bool:
        """Single-level compression: recent -> working -> long-term -> hard truncate."""
        if self._recent_chars > self.max_recent_chars:
            return self._summarize_recent_to_working()
        if self._working_chars > self.max_working_chars:
            return self._summarize_working_to_long_term()
        if self._long_term_chars > self.max_long_term_chars:
            self.long_term_summary = self._truncate(self.long_term_summary, self.max_long_term_chars)
            return True
        return False

    def _summarize_recent_to_working(self) -> bool:
        """Summarize old recent turns into working memory, keep newest K turns."""
        if not self.llm:
            keep = self.recent_turns[-4:] if len(self.recent_turns) > 4 else self.recent_turns
            drop = self.recent_turns[:-4] if len(self.recent_turns) > 4 else []
            self.recent_turns = keep
            self.working_summary = self._format_turns(drop)[:self.max_working_chars]
            return len(drop) > 0

        keep_count = max(3, len(self.recent_turns) // 3)
        drop = self.recent_turns[:-keep_count]
        keep = self.recent_turns[-keep_count:]

        prompt = (
            "Summarize the following conversation turns into a concise working memory. "
            "Keep: current task, key decisions, failures and corrections, next steps. "
            "Discard narration and redundant tool output.\n\n" +
            self._format_turns(drop) + "\n\nSummary:"
        )
        try:
            summary = self._call_llm(prompt, max_tokens=800)
            if summary:
                self.working_summary = summary[:self.max_working_chars]
                self.recent_turns = keep
                return True
        except Exception:
            pass
        return False

    def _summarize_working_to_long_term(self) -> bool:
        """Fold working memory into long-term memory."""
        if not self.llm:
            self.long_term_summary = self._truncate(self.working_summary, self.max_long_term_chars)
            self.working_summary = ""
            return True

        prompt = (
            "Compress the following working memory into a compact long-term memory. "
            "Keep only project-level facts, user preferences, and overarching conclusions.\n\n" +
            self.working_summary + "\n\nLong-term memory:"
        )
        try:
            summary = self._call_llm(prompt, max_tokens=400)
            if summary:
                self.long_term_summary = summary[:self.max_long_term_chars]
                self.working_summary = ""
                return True
        except Exception:
            pass
        return False

    def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Call LLM synchronously for compression."""
        import asyncio

        async def _ask():
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return resp.content.strip() if resp.content else ""
        try:
            return asyncio.get_event_loop().run_until_complete(_ask())
        except Exception:
            return ""


    async def a_call_llm(self, prompt: str, max_tokens: int) -> str:
        """Async-friendly LLM call for compression."""
        try:
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return resp.content.strip() if resp.content else ""
        except Exception:
            return ""

    async def amaybe_compress(self, token_limit: int, token_used: int) -> bool:
        """Async version of maybe_compress."""
        soft_limit = int(token_limit * 0.75)
        hard_limit = int(token_limit * 0.9)

        compressed = False
        if token_used > hard_limit:
            compressed = await self.acompress_one_level() or compressed
            compressed = await self.acompress_one_level() or compressed
        elif token_used > soft_limit:
            compressed = await self.acompress_one_level() or compressed
        return compressed

    async def acompress_one_level(self) -> bool:
        """Async single-level compression."""
        if self._recent_chars > self.max_recent_chars:
            return await self.asummarize_recent_to_working()
        if self._working_chars > self.max_working_chars:
            return await self.asummarize_working_to_long_term()
        if self._long_term_chars > self.max_long_term_chars:
            self.long_term_summary = self._truncate(self.long_term_summary, self.max_long_term_chars)
            return True
        return False

    async def asummarize_recent_to_working(self) -> bool:
        """Async: summarize old recent turns into working memory, keep newest K turns."""
        if not self.llm:
            keep = self.recent_turns[-4:] if len(self.recent_turns) > 4 else self.recent_turns
            drop = self.recent_turns[:-4] if len(self.recent_turns) > 4 else []
            self.recent_turns = keep
            self.working_summary = self._format_turns(drop)[:self.max_working_chars]
            return len(drop) > 0

        keep_count = max(3, len(self.recent_turns) // 3)
        drop = self.recent_turns[:-keep_count]
        keep = self.recent_turns[-keep_count:]

        prompt = (
            "Summarize the following conversation turns into a concise working memory. "
            "Keep: current task, key decisions, failures and corrections, next steps. "
            "Discard narration and redundant tool output.\n\n" +
            self._format_turns(drop) + "\n\nSummary:"
        )
        try:
            summary = await self.a_call_llm(prompt, max_tokens=800)
            if summary:
                self.working_summary = summary[:self.max_working_chars]
                self.recent_turns = keep
                return True
        except Exception:
            pass
        return False

    async def asummarize_working_to_long_term(self) -> bool:
        """Async: fold working memory into long-term memory."""
        if not self.llm:
            self.long_term_summary = self._truncate(self.working_summary, self.max_long_term_chars)
            self.working_summary = ""
            return True

        prompt = (
            "Compress the following working memory into a compact long-term memory. "
            "Keep only project-level facts, user preferences, and overarching conclusions.\n\n" +
            self.working_summary + "\n\nLong-term memory:"
        )
        try:
            summary = await self.a_call_llm(prompt, max_tokens=400)
            if summary:
                self.long_term_summary = summary[:self.max_long_term_chars]
                self.working_summary = ""
                return True
        except Exception:
            pass
        return False
    def _format_turns(self, turns: list[Message]) -> str:
        lines = []
        for m in turns:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            content = (m.content or "")[:800].replace("\n", " ")
            if m.tool_calls:
                names = ", ".join(tc.name for tc in m.tool_calls)
                content += f" [tools: {names}]"
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        head = int(limit * 0.7)
        tail = limit - head - 30
        return text[:head] + f"\n... ({len(text) - head - tail} chars omitted) ...\n" + text[-tail:]

    def to_messages(self, system_msg: Message | None = None) -> list[Message]:
        """Build complete context messages for the LLM."""
        messages = []
        if system_msg:
            parts = [system_msg.content]
            if self.long_term_summary:
                parts.append(f"\n<long_term_memory>\n{self.long_term_summary}\n</long_term_memory>")
            if self.working_summary:
                parts.append(f"\n<working_memory>\n{self.working_summary}\n</working_memory>")
            messages.append(Message(role=Role.SYSTEM, content="\n".join(parts)))
        else:
            if self.long_term_summary:
                messages.append(Message(role=Role.SYSTEM,
                    content=f"<long_term_memory>\n{self.long_term_summary}\n</long_term_memory>"))
            if self.working_summary:
                messages.append(Message(role=Role.SYSTEM,
                    content=f"<working_memory>\n{self.working_summary}\n</working_memory>"))
        messages.extend(self.recent_turns)
        return messages
