from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def estimate_tokens(text: str | None) -> int:
    """Conservative local estimate: roughly three chars per token."""
    return (len(text or "") + 2) // 3


@dataclass
class BudgetPlan:
    """Static C1 allocation; E2 can replace this policy without changing IR."""

    total_tokens: int
    allocations: dict[str, int]
    reserve_tokens: int
    task_type: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "task_type": self.task_type,
            "allocations": dict(self.allocations),
            "reserve_tokens": self.reserve_tokens,
        }


@dataclass
class ContextBlock:
    """One partition in the context intermediate representation."""

    partition: str
    content: str
    source: str
    tokens: int = 0
    compressed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tokens <= 0:
            self.tokens = estimate_tokens(self.content)


@dataclass
class ContextIR:
    """Traceable assembly result before conversion to API messages."""

    blocks: list[ContextBlock] = field(default_factory=list)
    budget_plan: BudgetPlan | None = None

    @property
    def partition_tokens(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for block in self.blocks:
            result[block.partition] = result.get(block.partition, 0) + block.tokens
        return result

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "partitions": self.partition_tokens,
            "budget": self.budget_plan.to_dict() if self.budget_plan else None,
            "blocks": [
                {
                    "partition": block.partition,
                    "source": block.source,
                    "tokens": block.tokens,
                    "compressed": block.compressed,
                    "metadata": block.metadata,
                }
                for block in self.blocks
            ],
        }


@dataclass
class CompiledContext:
    """Final message list plus the IR used to build it."""

    messages: list[Any] = field(default_factory=list)
    ir: ContextIR = field(default_factory=ContextIR)
