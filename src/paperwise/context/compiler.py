from __future__ import annotations

from pathlib import Path
from typing import Any

from paperwise.core.types import AgentState, Message, Role

from .budget import BudgetManager
from .models import BudgetPlan, CompiledContext, ContextBlock, ContextIR
from . import selectors


class ContextCompiler:
    """Assemble state sources into a deterministic, budgeted message list."""

    def __init__(
        self,
        token_limit: int,
        recent_turn_limit: int = 20,
        budget_manager: BudgetManager | None = None,
    ):
        self.token_limit = int(token_limit)
        self.recent_turn_limit = int(recent_turn_limit)
        self.budget_manager = budget_manager or BudgetManager()

    def compile(
        self,
        query: str,
        system_prompt: str,
        workspace: Path,
        *,
        plan_text: str = "",
        runtime_state: AgentState | dict[str, Any] | None = None,
        memories: list[Any] | None = None,
        knowledge: list[Any] | None = None,
        session_summary: str = "",
        transcript: list[Message] | None = None,
        tools_catalog: str = "",
    ) -> CompiledContext:
        transcript = list(transcript or [])
        blocks = [
            selectors.select_system(system_prompt, tools_catalog),
            selectors.select_task(query, workspace, plan_text),
            selectors.select_execution_state(runtime_state),
            selectors.select_memory(memories),
            selectors.select_knowledge(knowledge),
            selectors.select_session_summary(session_summary),
        ]
        blocks.extend(selectors.select_recent_turns(transcript, self.recent_turn_limit))
        blocks.append(selectors.select_user_input(query))

        plan = self.budget_manager.allocate(self.token_limit)
        fitted = self.budget_manager.fit(blocks, plan)
        fitted = [block for block in fitted if block.content or block.partition == "user_input"]

        ir = ContextIR(blocks=fitted, budget_plan=plan)
        return CompiledContext(messages=self._render(fitted), ir=ir)

    def _render(self, blocks: list[ContextBlock]) -> list[Message]:
        system_blocks = [b for b in blocks if b.partition == "system"]
        state_blocks = [
            b for b in blocks
            if b.partition in ("task", "execution_state", "memory", "knowledge", "session_summary")
        ]
        rendered: list[Message] = []
        if system_blocks:
            rendered.append(Message(role=Role.SYSTEM, content="\n\n".join(b.content for b in system_blocks)))

        # Task and all dynamic recall form one suffix, preserving a byte-stable
        # system prefix while retaining the old system+task API shape.
        suffix_sections = [b.content for b in state_blocks if b.content]
        current_input = next((b for b in blocks if b.partition == "user_input"), None)
        if current_input and current_input.content and current_input not in suffix_sections:
            suffix_sections.append(f"<user_input>\n{current_input.content}\n</user_input>")
        if suffix_sections:
            rendered.append(Message(role=Role.USER, content="\n\n".join(suffix_sections)))

        for block in blocks:
            if block.partition not in ("recent_turns", "tool_results"):
                continue
            role_name = (block.metadata or {}).get("role", "user")
            try:
                role = Role(role_name)
            except (ValueError, TypeError):
                role = Role.USER
            message = Message(
                role=role,
                content=block.content,
                tool_call_id=(block.metadata or {}).get("tool_call_id"),
                message_id=(block.metadata or {}).get("message_id"),
            )
            rendered.append(message)
        return rendered
