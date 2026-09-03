from pathlib import Path

from paperwise.context import BudgetManager, ContextCompiler


def test_selective_activation_prefers_query_relevant_items():
    items = [
        "user studies computer vision and Gaussian Splatting",
        "user prefers coffee over tea",
        "LangSplat uses SAM and CLIP",
    ]
    selected = ContextCompiler(token_limit=10_000).compile(
        query="How does LangSplat use SAM?",
        system_prompt="system",
        workspace=Path("workspace"),
        memories=items,
        memory_limit=2,
    )
    memory_block = next(block for block in selected.ir.blocks if block.partition == "memory")
    assert "LangSplat" in memory_block.content
    assert "coffee" not in memory_block.content


def test_selective_activation_is_deterministic():
    items = [{"id": 1, "text": "feature field"}, {"id": 2, "text": "feature field"}]
    first = ContextCompiler(token_limit=10_000).compile(
        query="feature field", system_prompt="s", workspace=Path("w"), memories=items
    )
    second = ContextCompiler(token_limit=10_000).compile(
        query="feature field", system_prompt="s", workspace=Path("w"), memories=items
    )
    assert [block.content for block in first.ir.blocks] == [block.content for block in second.ir.blocks]


def test_dynamic_budget_profiles_change_by_task_type():
    manager = BudgetManager()
    question = manager.allocate(1_000, "question")
    report = manager.allocate(1_000, "report")
    research = manager.allocate(1_000, "research_loop")

    assert question.task_type == "question"
    assert report.allocations["knowledge"] > question.allocations["knowledge"]
    assert research.allocations["recent_turns"] < report.allocations["recent_turns"]
    for plan in (question, report, research):
        assert sum(plan.allocations.values()) + plan.reserve_tokens <= 1_000


def test_compiler_infers_task_type_into_trace_budget():
    compiled = ContextCompiler(token_limit=10_000).compile(
        query="Generate an academic report", system_prompt="s", workspace=Path("w")
    )
    assert compiled.ir.budget_plan.task_type == "report"
    assert compiled.ir.to_trace_dict()["budget"]["task_type"] == "report"
