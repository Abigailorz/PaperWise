from __future__ import annotations

from pathlib import Path
from typing import Any

from paperwise.core.types import AgentState, Message, Role

from .models import ContextBlock


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "; ".join(f"{key}={item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(item) for item in value)
    return str(value)


def select_system(system_prompt: str, tools_catalog: str = "") -> ContextBlock:
    content = system_prompt
    if tools_catalog:
        content += f"\n\n{tools_catalog}"
    return ContextBlock(
        partition="system", content=content, source="ContextManager.system"
    )


def select_task(task: str, workspace: Path, plan_text: str = "") -> ContextBlock:
    sections = [f"<task>\n{task}\n</task>"]
    if plan_text:
        sections.append(f"<current_plan>\n{plan_text}\n</current_plan>")
    sections.append(
        f"<workspace>\n  working_dir: {workspace}\n</workspace>"
    )
    return ContextBlock(
        partition="task", content="\n\n".join(sections), source="ResearchState.task"
    )


def select_execution_state(state: AgentState | dict[str, Any] | None) -> ContextBlock:
    if isinstance(state, AgentState):
        data = {
            "step": state.current_step,
            "max_steps": state.max_steps,
            "tokens_used": state.tokens_used,
            "token_limit": state.token_limit,
            "todo": state.todo_items,
        }
    else:
        data = dict(state or {})
    content = f"<execution_state>\n{_text(data)}\n</execution_state>" if data else ""
    return ContextBlock(
        partition="execution_state", content=content, source="AgentState"
    )


def select_memory(memories: list[Any] | None) -> ContextBlock:
    lines = [_text(item) for item in (memories or []) if _text(item)]
    content = "<relevant_memory>\n" + "\n".join(f"- {line}" for line in lines) + "\n</relevant_memory>" if lines else ""
    return ContextBlock(
        partition="memory", content=content, source="UserMemory"
    )


def select_knowledge(knowledge: list[Any] | None) -> ContextBlock:
    lines = [_text(item) for item in (knowledge or []) if _text(item)]
    content = "<relevant_knowledge>\n" + "\n".join(f"- {line}" for line in lines) + "\n</relevant_knowledge>" if lines else ""
    return ContextBlock(
        partition="knowledge", content=content, source="KnowledgeBase"
    )


def select_session_summary(summary: str) -> ContextBlock:
    content = f"<session_memory>\n{summary}\n</session_memory>" if summary else ""
    return ContextBlock(
        partition="session_summary", content=content, source="SessionMemory"
    )


def select_user_input(query: str) -> ContextBlock:
    return ContextBlock(partition="user_input", content=query, source="transcript")


def select_recent_turns(transcript: list[Message], limit: int = 20) -> list[ContextBlock]:
    """Select prior messages only; the current input is a separate partition."""
    selected = [m for m in transcript if m.content is not None][-max(limit, 0):]
    return [
        ContextBlock(
            partition="recent_turns" if m.role.value != "tool" else "tool_results",
            content=m.content or "",
            source="transcript",
            metadata={"role": m.role.value, "message_id": getattr(m, "message_id", None)},
        )
        for m in selected
    ]
