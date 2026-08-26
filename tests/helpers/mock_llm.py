"""Mock LLM client for deterministic agent-loop tests.

Usage:
    mock = MockLLMClient([
        LLMResponse(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "paper/text.md"})]),
        LLMResponse(content="done"),
    ])
"""

from paperwise.core.llm_client import LLMResponse, StreamEvent
from paperwise.core.types import ToolCall


class MockLLMClient:
    """Deterministic LLM client that replays a scripted response sequence."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self.responses = responses or []
        self.calls: list[dict] = []
        self._idx = 0

    def reset(self, responses: list[LLMResponse] | None = None):
        if responses is not None:
            self.responses = responses
        self._idx = 0
        self.calls.clear()

    def count_tokens(self, text: str) -> int:
        return len(text) // 2

    async def chat(self, messages, tools=None, temperature=0.3, max_tokens=4096):
        self.calls.append({"messages": messages, "tools": tools})
        if self._idx < len(self.responses):
            resp = self.responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(content="mock fallback")

    async def chat_stream(self, messages, tools=None, temperature=0.3, max_tokens=4096):
        self.calls.append({"messages": messages, "tools": tools})
        if self._idx >= len(self.responses):
            yield StreamEvent(type="text_delta", text="mock fallback")
            yield StreamEvent(type="done")
            return

        resp = self.responses[self._idx]
        self._idx += 1

        if resp.content:
            yield StreamEvent(type="text_delta", text=resp.content)
        for tc in (resp.tool_calls or []):
            yield StreamEvent(type="tool_call_start", tool_id=tc.id, tool_name=tc.name)
            yield StreamEvent(
                type="tool_call_delta",
                tool_id=tc.id,
                tool_arguments=__import__("json").dumps(tc.arguments, ensure_ascii=False),
            )
            yield StreamEvent(type="tool_call_end", tool_id=tc.id, tool_name=tc.name)
        yield StreamEvent(type="done")


    def estimate_cost(self, usage: dict) -> float:
        return 0.0


__all__ = ["MockLLMClient"]
