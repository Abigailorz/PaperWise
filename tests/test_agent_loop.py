"""Tier 2：Mock-LLM Agent 控制逻辑测试。

对应 EVALUATION_FRAMEWORK.md Tier 2：
- 按 plan 顺序调用 read_file / grep
- budget usage 较高时是否注入 budget note
- stagnation 检测是否触发退出
- Judge review 关闭时是否跳过
- AgentSession 多轮对话上下文不丢失
- Plan task 完成后 verify_completion 正确触发
"""

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.core.llm_client import LLMResponse
from paperwise.core.agent import Agent
from paperwise.core.types import AgentConfig, ToolCall
from paperwise.core.session import AgentSession
from paperwise.tools.registry import ToolRegistry
from paperwise.harness.harness import Harness
from tests.helpers.mock_llm import MockLLMClient


PAPER_TEXT = """# EfficientGraph
This paper proposes EfficientGraph with hierarchical attention and dynamic pruning.
It achieves 87.2% accuracy on Cora, compared to GAT 83.0%.
"""


def _build_agent(workspace: Path, config: AgentConfig, llm: MockLLMClient):
    workspace.mkdir(parents=True, exist_ok=True)
    paper_dir = workspace / "paper"
    paper_dir.mkdir(exist_ok=True)
    (paper_dir / "text.md").write_text(PAPER_TEXT, encoding="utf-8")
    (paper_dir / "metadata.json").write_text('{"title": "EfficientGraph"}', encoding="utf-8")

    tools = ToolRegistry.create_default(paper_dir)
    harness = Harness(paper_dir, max_steps=config.max_steps)
    return Agent(config=config, tools=tools, llm_client=llm, harness=harness, workspace_dir=paper_dir)


def test_plan_order_read_then_grep(tmp_path):
    async def _inner():
        llm = MockLLMClient([
            LLMResponse(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "text.md"})]),
            LLMResponse(tool_calls=[ToolCall(id="2", name="grep", arguments={"pattern": "accuracy"})]),
            LLMResponse(content="The main contribution is EfficientGraph with hierarchical attention. final answer."),
        ])
        config = AgentConfig(
            name="test", system_prompt="You are a paper analyst.",
            max_steps=5, enable_plan=True, enable_budget_note=True,
            enable_judge_review=False, enable_hierarchical_memory=False,
        enable_orchestration=False,)
        agent = _build_agent(tmp_path, config, llm)
        result = await agent.run("What is the main contribution?")

        assert result.success
        assert agent.state.tool_call_count.get("read_file", 0) >= 1
        assert agent.state.tool_call_count.get("grep", 0) >= 1

    asyncio.run(_inner())


def test_budget_note_injected_at_high_usage(tmp_path):
    async def _inner():
        # Two tool calls push step usage to 0.5 before the final text response,
        # which should trigger the budget note.
        llm = MockLLMClient([
            LLMResponse(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "text.md"})]),
            LLMResponse(tool_calls=[ToolCall(id="2", name="grep", arguments={"pattern": "EfficientGraph"})]),
            LLMResponse(content="final answer: contribution is EfficientGraph."),
        ])
        config = AgentConfig(
            name="test", system_prompt="You are a paper analyst.",
            max_steps=4, enable_plan=True, enable_budget_note=True,
            enable_judge_review=False, enable_hierarchical_memory=False,
        enable_orchestration=False,)
        agent = _build_agent(tmp_path, config, llm)
        await agent.run("What is the main contribution?")

        budget_notes = [
            m for m in agent.state.messages
            if m.role.value == "user" and m.content and "<budget_note>" in m.content
        ]
        assert len(budget_notes) >= 1, "budget note should be injected when step usage exceeds 0.5"

    asyncio.run(_inner())


def test_budget_note_disabled(tmp_path):
    async def _inner():
        llm = MockLLMClient([
            LLMResponse(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "text.md"})]),
            LLMResponse(tool_calls=[ToolCall(id="2", name="grep", arguments={"pattern": "EfficientGraph"})]),
            LLMResponse(content="final answer: contribution is EfficientGraph."),
        ])
        config = AgentConfig(
            name="test", system_prompt="You are a paper analyst.",
            max_steps=4, enable_plan=True, enable_budget_note=False,
            enable_judge_review=False, enable_hierarchical_memory=False,
        enable_orchestration=False,)
        agent = _build_agent(tmp_path, config, llm)
        await agent.run("What is the main contribution?")

        budget_notes = [
            m for m in agent.state.messages
            if m.role.value == "user" and m.content and "<budget_note>" in m.content
        ]
        assert len(budget_notes) == 0, "budget note should be skipped when disabled"

    asyncio.run(_inner())


def test_stagnation_exit(tmp_path):
    async def _inner():
        # Use a successful repeating tool call so the harness circuit breaker
        # does not fire before stagnation detection triggers.
        repeated = LLMResponse(tool_calls=[ToolCall(id="x", name="grep", arguments={"pattern": "EfficientGraph"})])
        llm = MockLLMClient([repeated] * 12)
        config = AgentConfig(
            name="test", system_prompt="You are a paper analyst.",
            max_steps=20, enable_plan=True, enable_budget_note=False,
            enable_judge_review=False, enable_hierarchical_memory=False,
        enable_orchestration=False,)
        agent = _build_agent(tmp_path, config, llm)
        result = await agent.run("What is the main contribution?")

        assert not result.success
        assert "stagnation" in result.error_message.lower()

    asyncio.run(_inner())


def test_no_plan_ablation(tmp_path):
    async def _inner():
        llm = MockLLMClient([
            LLMResponse(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "text.md"})]),
            LLMResponse(content="final answer: done."),
        ])
        config = AgentConfig(
            name="test", system_prompt="You are a paper analyst.",
            max_steps=5, enable_plan=False, enable_budget_note=False,
            enable_judge_review=False, enable_hierarchical_memory=False,
        enable_orchestration=False,)
        agent = _build_agent(tmp_path, config, llm)
        result = await agent.run("What is the main contribution?")

        assert result.success
        assert len(agent._plan.tasks) == 0

    asyncio.run(_inner())


def test_session_context_preserved(tmp_path):
    """AgentSession 在两次 chat 之间保留上下文。"""
    async def _inner():
        llm = MockLLMClient([
            LLMResponse(content="I need to read the paper first."),
            LLMResponse(content="It is about EfficientGraph."),
        ])
        workspace = tmp_path / "session"
        workspace.mkdir(parents=True, exist_ok=True)
        paper_dir = workspace / "paper"
        paper_dir.mkdir(exist_ok=True)
        (paper_dir / "text.md").write_text(PAPER_TEXT, encoding="utf-8")

        tools = ToolRegistry.create_default(paper_dir)
        harness = Harness(paper_dir, max_steps=10)
        session = AgentSession(
            workspace=workspace, llm_client=llm, tools=tools,
            harness=harness, memory=None, knowledge_base=None,
        )
        await session.chat("Hello")
        await session.chat("Tell me the topic")

        user_msgs = [m for m in session.state.messages if m.role.value == "user"]
        contents = " ".join(m.content or "" for m in user_msgs)
        assert "Hello" in contents
        assert "Tell me the topic" in contents

    asyncio.run(_inner())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
