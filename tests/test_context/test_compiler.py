from pathlib import Path

from paperwise.context import BudgetManager, ContextCompiler
from paperwise.core.types import AgentState, Message, Role


def test_compile_is_deterministic_and_partitioned(tmp_path: Path):
    compiler = ContextCompiler(token_limit=10_000)
    kwargs = dict(
        query="What is the contribution?",
        system_prompt="You are a paper analyst.",
        workspace=tmp_path,
        plan_text="read -> answer",
        runtime_state={"step": 2},
        memories=["user prefers concise answers"],
        knowledge=["The paper introduces EfficientGraph."],
        session_summary="Previously read section 1.",
        transcript=[Message(role=Role.ASSISTANT, content="Earlier answer")],
    )
    first = compiler.compile(**kwargs)
    second = compiler.compile(**kwargs)

    assert [m.to_dict() for m in first.messages] == [m.to_dict() for m in second.messages]
    partitions = first.ir.partition_tokens
    assert set(partitions) == {
        "system", "task", "execution_state", "memory", "knowledge",
        "session_summary", "recent_turns", "user_input",
    }
    assert first.ir.to_trace_dict()["budget"]["total_tokens"] == 10_000


def test_static_system_prefix_is_stable_when_state_changes(tmp_path: Path):
    compiler = ContextCompiler(token_limit=10_000)
    first = compiler.compile(
        query="q", system_prompt="stable", workspace=tmp_path,
        runtime_state={"step": 1},
    )
    second = compiler.compile(
        query="q", system_prompt="stable", workspace=tmp_path,
        runtime_state={"step": 2, "todo": ["new"]},
    )
    assert first.messages[0].content == second.messages[0].content


def test_budget_marks_oversized_non_system_block_compressed(tmp_path: Path):
    manager = BudgetManager()
    plan = manager.allocate(100)
    compiler = ContextCompiler(token_limit=100, budget_manager=manager)
    compiled = compiler.compile(
        query="q", system_prompt="system", workspace=tmp_path,
        knowledge=["x" * 1_000],
    )
    assert plan.allocations["knowledge"] == 28
    knowledge_block = next(block for block in compiled.ir.blocks if block.partition == "knowledge")
    assert knowledge_block.compressed
