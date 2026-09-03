"""Agent-level regression tests for the context-native runtime (C1/C2)."""

import asyncio
from pathlib import Path

import pytest

from paperwise.core.agent import Agent
from paperwise.core.llm_client import LLMResponse
from paperwise.core.trace_collector import InMemoryTraceCollector
from paperwise.core.types import AgentConfig, TraceEventType
from paperwise.harness.harness import Harness
from paperwise.tools.registry import ToolRegistry
from tests.helpers.mock_llm import MockLLMClient


PAPER_TEXT = """# FeatureField
FeatureField distills 2D semantic features into a 3D Gaussian field.
It reports 84.3% segmentation accuracy and a 1.7x speedup.
"""


def _config(name: str, **overrides) -> AgentConfig:
    values = {
        "name": name,
        "system_prompt": "You are a paper analysis agent.",
        "max_steps": 2,
        "enable_plan": False,
        "enable_budget_note": False,
        "enable_judge_review": False,
        "enable_hierarchical_memory": False,
        "enable_orchestration": False,
    }
    values.update(overrides)
    return AgentConfig(**values)


def _build_agent(
    workspace: Path,
    llm: MockLLMClient,
    config: AgentConfig,
    trace_collector=None,
) -> Agent:
    paper_dir = workspace / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "text.md").write_text(PAPER_TEXT, encoding="utf-8")

    return Agent(
        config=config,
        tools=ToolRegistry.create_default(paper_dir),
        llm_client=llm,
        harness=Harness(paper_dir, max_steps=config.max_steps),
        workspace_dir=paper_dir,
        trace_collector=trace_collector,
    )


def _text_agent_responses() -> list[LLMResponse]:
    return [
        LLMResponse(content="partial answer"),
        LLMResponse(content="final answer: FeatureField distills semantic features."),
    ]


@pytest.mark.asyncio
async def test_agent_uses_context_compiler_and_records_budget(tmp_path):
    traces = []
    collector = InMemoryTraceCollector(save_callback=traces.append)
    llm = MockLLMClient(_text_agent_responses())
    agent = _build_agent(tmp_path, llm, _config("context-native"), collector)

    result = await agent.run("What is the main contribution?")

    assert result.success
    assert agent._context_ir is not None
    # The legacy-agent task template asks the model to verify output files,
    # which is intentionally classified as a research-loop task.
    assert agent._context_ir.budget_plan.task_type == "research_loop"
    assert agent.state.messages[0].role.value == "system"
    assert agent.state.messages[1].role.value == "user"

    context_events = [
        event for trace in traces for event in trace.events
        if event.type == TraceEventType.CONTEXT_ASSEMBLED
    ]
    assert context_events
    assert context_events[0].data["mode"] == "compiler"
    assert context_events[0].data["ir"]["budget"]["task_type"] == "research_loop"
    assert context_events[0].data["ir"]["partitions"]


@pytest.mark.asyncio
async def test_agent_can_fall_back_to_legacy_context(tmp_path):
    llm = MockLLMClient(_text_agent_responses())
    config = _config("legacy-context", enable_context_compiler=False)
    agent = _build_agent(tmp_path, llm, config)

    result = await agent.run("What is the main contribution?")

    assert result.success
    assert agent._context_ir is None
    assert agent.state.messages


def test_agent_static_system_prefix_is_stable(tmp_path: Path):
    query_a = "How does FeatureField handle segmentation?"
    query_b = "Generate an academic report for FeatureField."
    prefixes = []

    for name, query in (("stable-a", query_a), ("stable-b", query_b)):
        llm = MockLLMClient(_text_agent_responses())
        agent = _build_agent(tmp_path / name, llm, _config(name))
        agent._init_messages(query)
        assert agent.state.messages
        prefixes.append(agent.state.messages[0].content or "")

    assert prefixes[0] == prefixes[1]


@pytest.mark.asyncio
async def test_agent_session_memory_extracts_and_resumes_cursor(tmp_path):
    config = _config("session-memory", enable_hierarchical_memory=True)

    first_llm = MockLLMClient([LLMResponse(content="first answer")])
    first_agent = _build_agent(tmp_path, first_llm, config)
    await first_agent.run("first-session-evidence about Gaussian feature fields")
    assert first_agent._session_memory is not None
    assert first_agent._session_memory.state.summary
    assert first_agent._session_memory.state.last_processed_message_id
    first_cursor = first_agent._session_memory.state.last_processed_message_id

    # A new Agent process with the same workspace and session id resumes from
    # the persisted cursor instead of reprocessing the first turn.
    second_llm = MockLLMClient([LLMResponse(content="second answer")])
    second_agent = _build_agent(tmp_path, second_llm, config)
    await second_agent.run("second-session-evidence about semantic distillation")

    session = second_agent._session_memory
    assert session is not None
    assert session.state.last_processed_message_id != first_cursor
    assert "first-session-evidence" in session.state.summary
    assert "second-session-evidence" in session.state.summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
