from __future__ import annotations

from .models import BudgetPlan, ContextBlock


class BudgetManager:
    """Deterministic static token budget for C1."""

    # system is reserved rather than aggressively truncated to preserve the
    # KV-cache-friendly prefix.  task+execution matches the spec's 15% share.
    RATIOS = {
        "system": 0.10,
        "task": 0.08,
        "execution_state": 0.07,
        "memory": 0.10,
        "knowledge": 0.30,
        "session_summary": 0.10,
        "recent_turns": 0.20,
        "tool_results": 0.0,
        "user_input": 0.0,
    }
    RESERVE_RATIO = 0.05

    TASK_PROFILES = {
        "general": RATIOS,
        "question": {
            **RATIOS,
            "knowledge": 0.25,
            "recent_turns": 0.25,
        },
        "report": {
            **RATIOS,
            "memory": 0.05,
            "knowledge": 0.35,
            "session_summary": 0.05,
            "recent_turns": 0.25,
        },
        "research_loop": {
            **RATIOS,
            "knowledge": 0.35,
            "recent_turns": 0.15,
        },
    }

    def allocate(self, total_tokens: int, task_type: str = "general") -> BudgetPlan:
        total_tokens = max(int(total_tokens), 0)
        reserve = int(total_tokens * self.RESERVE_RATIO)
        budgeted = max(total_tokens - reserve, 0)
        ratios = self.TASK_PROFILES.get(task_type, self.RATIOS)
        allocations = {
            partition: int(budgeted * ratio)
            for partition, ratio in ratios.items()
            if ratio > 0
        }
        return BudgetPlan(
            total_tokens=total_tokens,
            task_type=task_type,
            allocations=allocations,
            reserve_tokens=reserve,
        )

    def fit(self, blocks: list[ContextBlock], plan: BudgetPlan) -> list[ContextBlock]:
        """Apply local character budgets; empty and user/system blocks pass through."""
        fitted: list[ContextBlock] = []
        for block in blocks:
            limit = plan.allocations.get(block.partition, 0)
            if not limit or block.partition in ("system", "user_input", "tool_results"):
                fitted.append(block)
                continue
            max_chars = limit * 3
            if len(block.content) <= max_chars:
                fitted.append(block)
                continue
            head = max(1, int(max_chars * 0.7))
            tail = max(0, max_chars - head - 30)
            omitted = len(block.content) - head - tail
            content = block.content[:head]
            if tail:
                content += f"\n... ({omitted} chars omitted) ...\n" + block.content[-tail:]
            fitted.append(
                ContextBlock(
                    partition=block.partition,
                    content=content,
                    source=block.source,
                    compressed=True,
                    metadata=block.metadata,
                )
            )
        return fitted
