"""LLM API 客户端 — 支持 DeepSeek、Kimi K3 (Moonshot)、OpenAI 及任意兼容端点

所有提供商使用 OpenAI 兼容的 Chat Completions API 格式（含 tool calling）。
"""

import json
import asyncio
import httpx
from typing import Optional, AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI

from paperwise.core.types import Message, Role, ToolCall, ToolResult
from paperwise.config.settings import get_settings


@dataclass
class StreamEvent:
    """流式响应的单个事件"""
    type: str  # "text_delta" | "tool_call_start" | "tool_call_delta" | "tool_call_end" | "done"
    text: str = ""
    tool_id: str = ""
    tool_name: str = ""
    tool_arguments: str = ""


@dataclass
class LLMResponse:
    """统一的 LLM 响应"""
    content: str = ""
    tool_calls: list[ToolCall] = None
    reasoning: str = ""
    stop_reason: str = ""  # "stop" | "tool_calls" | "length"
    usage: dict = None

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []
        if self.usage is None:
            self.usage = {}


class LLMClient:
    """多提供商 LLM 客户端 — 基于 OpenAI 兼容协议"""

    def __init__(self, provider: str = None, model: str = None,
                 api_key: str = None, base_url: str = None):
        settings = get_settings()
        self.provider = provider or settings.llm_provider
        self.model = model or settings.default_model
        self.api_key = api_key or settings.api_key
        self.base_url = base_url or settings.base_url

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            # 直连 + 显式超时：绕开 Windows 系统代理（Clash）的间歇性抽风，
            # 避免上游 API 挂起时无限阻塞（SDK 默认 600s 过长）
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=180.0,
                    write=180.0,
                    pool=30.0,
                ),
                trust_env=False,
            ),
        )
    def _effective_temperature(self, temperature: float) -> float:
        """Kimi Coding series only allows temperature=1, override to avoid 400."""
        if self.provider == "moonshot" and self.base_url and "coding" in self.base_url:
            return 1.0
        return temperature


    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """发送非流式 Chat Completion 请求。"""
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=self._effective_temperature(temperature),
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamEvent]:
        """发送流式 Chat Completion 请求，逐 token 返回事件。"""
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=self._effective_temperature(temperature),
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self.client.chat.completions.create(**kwargs)

        tool_call_buffer: dict[int, dict] = {}  # index → partial ToolCall

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Text content
            if delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            # Reasoning content (DeepSeek-R1, Kimi K3 thinking mode)
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                yield StreamEvent(type="text_delta", text=delta.reasoning_content)

            # Tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index

                    if tc_delta.id:
                        # New tool call starting
                        tool_call_buffer[idx] = {
                            "id": tc_delta.id,
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }
                        yield StreamEvent(
                            type="tool_call_start",
                            tool_id=tc_delta.id,
                            tool_name=tool_call_buffer[idx]["name"],
                        )

                    if tc_delta.function and tc_delta.function.arguments:
                        tool_call_buffer[idx]["arguments"] += tc_delta.function.arguments
                        yield StreamEvent(
                            type="tool_call_delta",
                            tool_id=tool_call_buffer[idx]["id"],
                            tool_arguments=tc_delta.function.arguments,
                        )

            # Finish reason
            if chunk.choices[0].finish_reason:
                # Emit tool_call_end for any completed tool calls
                for idx, tc in tool_call_buffer.items():
                    yield StreamEvent(
                        type="tool_call_end",
                        tool_id=tc["id"],
                        tool_name=tc["name"],
                        tool_arguments=tc["arguments"],
                    )
                yield StreamEvent(type="done")

    def _parse_response(self, response) -> LLMResponse:
        """解析 API 响应为统一的 LLMResponse 格式。"""
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason or "stop"

        content = message.content or ""
        reasoning = getattr(message, 'reasoning_content', '') or ""
        if not content and reasoning:
            content = reasoning

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning=reasoning,
            stop_reason="tool_calls" if tool_calls else finish_reason,
            usage=usage,
        )

    def count_tokens(self, text: str) -> int:
        """估算文本 token 数（使用 tiktoken cl100k_base 编码）。"""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            # 回退：约 4 字符 = 1 token (英文) 或 1.5 字符 = 1 token (中文)
            return len(text) // 2
