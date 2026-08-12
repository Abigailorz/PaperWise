"""端到端集成测试 — 使用模拟 LLM 验证完整流程

测试场景:
1. 解析论文 → Agent 分析 → 生成报告
2. 使用 Mock LLM（不调用真实 API）
"""

import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from paperwise.core.types import (
    Message, Role, ToolCall, ToolResult,
    AgentState, AgentConfig, AgentResult,
)
from paperwise.core.llm_client import LLMClient, LLMResponse


class MockLLMClient:
    """模拟 LLM 客户端 — 返回预设响应序列（兼容 chat 和 chat_stream）"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = responses
        self.call_count = 0
        self.history: list[list[dict]] = []
        self._stream_index = 0

    async def chat(self, messages, tools=None, temperature=0.3, max_tokens=4096):
        """非流式调用（向后兼容）。"""
        self.history.append(messages)
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content="Analysis complete.", stop_reason="stop")

    async def chat_stream(self, messages, tools=None, temperature=0.3, max_tokens=4096):
        """流式调用 — Agent 现在默认使用此方法。"""
        self.history.append(messages)
        resp = None
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
        else:
            resp = LLMResponse(content="Analysis complete.", stop_reason="stop")

        # 模拟流式事件
        from paperwise.core.llm_client import StreamEvent

        # 1. 发射 reasoning
        if resp.reasoning:
            yield StreamEvent(type="text_delta", text=resp.reasoning)

        # 2. 发射 tool calls
        if resp.tool_calls:
            for i, tc in enumerate(resp.tool_calls):
                yield StreamEvent(type="tool_call_start", tool_id=tc.id, tool_name=tc.name)
                args_str = json.dumps(tc.arguments, ensure_ascii=False)
                yield StreamEvent(type="tool_call_delta", tool_id=tc.id, tool_arguments=args_str)
                yield StreamEvent(type="tool_call_end", tool_id=tc.id, tool_name=tc.name)

        # 3. 发射 text content
        if resp.content:
            yield StreamEvent(type="text_delta", text=resp.content)

        # 4. done
        yield StreamEvent(type="done")

    def count_tokens(self, text: str) -> int:
        return len(text) // 2


def make_response(content="", tool_calls=None, reasoning=""):
    """Helper: create LLMResponse."""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        reasoning=reasoning,
        stop_reason="tool_calls" if tool_calls else "stop",
    )


def make_tool_call(id, name, args):
    """Helper: create ToolCall."""
    return ToolCall(id=id, name=name, arguments=args)


class TestE2EPipeline:
    """端到端测试：解析 → 分析 → 报告"""

    async def test_full_pipeline_with_mock_llm(self, workspace: Path, sample_text: str):
        """使用模拟 LLM 验证完整 Agent 循环。"""
        from paperwise.core.agent import Agent
        from paperwise.tools.registry import ToolRegistry
        from paperwise.harness.harness import Harness

        # 准备论文目录
        paper_dir = workspace / "test_paper"
        paper_dir.mkdir()
        (paper_dir / "text.md").write_text(sample_text)
        (paper_dir / "metadata.json").write_text(
            json.dumps({"title": "Attention Is All You Need", "page_count": 15})
        )

        # 设置模拟 LLM
        mock_llm = MockLLMClient([
            # Step 1: Read metadata
            make_response(tool_calls=[
                make_tool_call("c1", "read_file", {"path": "text.md", "limit": 100})
            ]),
            # Step 2: Search for key results
            make_response(tool_calls=[
                make_tool_call("c2", "grep", {"pattern": "BLEU|accuracy|result", "path": "."})
            ]),
            # Step 3: Write analysis
            make_response(tool_calls=[
                make_tool_call("c3", "write_file", {
                    "path": "report/report.md",
                    "content": "# Analysis Report\n\nTest report content."
                })
            ]),
            # Step 4: Final answer (with completion marker)
            make_response(content="Report has been generated successfully. All sections are complete. Task complete."),
        ])

        # 创建 Agent
        tools = ToolRegistry.create_default(paper_dir)
        harness = Harness(paper_dir, max_steps=10)

        config = AgentConfig(
            name="test-agent",
            system_prompt="You are a helpful assistant. Use tools to analyze papers.",
            model="test-model",
            max_steps=10,
        )

        agent = Agent(
            config=config,
            tools=tools,
            llm_client=mock_llm,
            harness=harness,
            workspace_dir=paper_dir,
        )

        # 执行
        result = await agent.run("Analyze the paper at text.md and generate a report.")

        # 验证
        assert result.success is True
        assert result.steps <= 10
        assert len(result.messages) > 0

        # 验证 LLM 调用历史包含正确的消息结构
        assert mock_llm.call_count > 0
        for msgs in mock_llm.history:
            # 第一条消息应该是 system
            assert msgs[0]["role"] == "system"

    async def test_agent_respects_max_steps(self, workspace: Path, sample_text: str):
        """测试 Agent 在达到最大步数时正确退出。"""
        from paperwise.core.agent import Agent
        from paperwise.tools.registry import ToolRegistry
        from paperwise.harness.harness import Harness

        paper_dir = workspace / "test_paper"
        paper_dir.mkdir()
        (paper_dir / "text.md").write_text(sample_text)

        # 模拟 LLM 始终返回工具调用（潜在无限循环）
        tools = ToolRegistry.create_default(paper_dir)

        mock_llm = MockLLMClient([
            make_response(tool_calls=[
                make_tool_call(f"c{i}", "read_file", {"path": "text.md"})
            ]) for i in range(20)  # 20 个工具调用
        ])

        harness = Harness(paper_dir, max_steps=3)  # 限制 3 步

        config = AgentConfig(name="test", system_prompt="...", max_steps=3)
        agent = Agent(config=config, tools=tools, llm_client=mock_llm,
                      harness=harness, workspace_dir=paper_dir)

        result = await agent.run("Do something")

        # 应该在 max_steps 处退出
        assert result.success is False
        assert "max" in result.error_message.lower() or "step" in result.error_message.lower()

    async def test_status_bar_injection(self, workspace: Path, sample_text: str):
        """测试 Agent 状态栏注入。"""
        from paperwise.core.agent import Agent
        from paperwise.tools.registry import ToolRegistry
        from paperwise.harness.harness import Harness
        from paperwise.harness.status_bar import StatusBar

        paper_dir = workspace / "test_paper"
        paper_dir.mkdir()
        (paper_dir / "text.md").write_text(sample_text)

        # 验证 StatusBar 生成正确
        state = AgentState(
            current_step=3,
            max_steps=10,
            token_limit=100000,
            workspace_dir=paper_dir,
            task_description="Test task",
            tool_call_count={"read_file": 3, "grep": 2},
            todo_items=[
                {"text": "Parse paper", "status": "done"},
                {"text": "Analyze method", "status": "in_progress"},
                {"text": "Generate report", "status": "pending"},
            ],
        )

        bar = StatusBar()
        status_xml = bar.generate(state)

        assert "<agent_status>" in status_xml
        assert "read_file: 3 calls" in status_xml
        assert "Parse paper" in status_xml
        assert "Step: 3/10" in status_xml

    async def test_loop_detection(self):
        """测试循环检测。"""
        from paperwise.harness.status_bar import StatusBar

        bar = StatusBar()
        state = AgentState()

        # 模拟连续 5 次相同的工具调用
        tc = ToolCall(id="c1", name="read_file", arguments={"path": "same_file.txt"})
        for _ in range(6):
            state.messages.append(Message(role=Role.ASSISTANT, tool_calls=[tc]))
            state.messages.append(Message(role=Role.TOOL, content="same result",
                                          tool_call_id="c1"))

        warning = bar.detect_loops(state)
        assert warning is not None
        assert "loop" in warning.lower()
        assert "read_file" in warning
