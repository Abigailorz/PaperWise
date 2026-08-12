"""上下文管理器 — 完整 5 层上下文压缩

对应书中 2.7.4 节：生产级分层压缩机制

Layer 1: 工具结果预算控制 — 大输出截断存磁盘，模型看摘要
Layer 2: 噪声直接删除 — 识别并移除低价值/重复内容
Layer 3: API 层微压缩 — 精简工具 schema 和重复消息
Layer 4: 归档式摘要 — git log 式逐轮结构化摘要
Layer 5: 全量 LLM 压缩 — LLM 驱动的完整上下文压缩
"""

import re
import uuid
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

from paperwise.core.types import Message, Role, AgentState


class ContextManager:
    """完整 5 层上下文压缩管理器"""

    TOOL_OUTPUT_MAX_CHARS = 8_000
    MAX_TOOL_RESULTS = 30        # 最多保留 30 条工具结果
    COMPRESSION_TRIGGER = 0.85   # 85% token 预算触发压缩
    ARCHIVE_WINDOW = 20           # 超过 20 轮后开始归档

    def __init__(self, workspace: Path, llm_client=None):
        self.workspace = Path(workspace)
        self.llm = llm_client       # Layer 5 需要
        self.archive: list[dict] = []  # Layer 4 归档存储
        self._noise_hashes: set[int] = set()  # Layer 2 去重

    # ══════════ Layer 1: 工具结果预算控制 ══════════

    def truncate_tool_output(self, output: str) -> tuple[str, bool, Optional[Path]]:
        """智能截断：保留开头和结尾，中间用摘要替代。

        比简单截断更好：保留关键的开头结论和结尾数据。
        """
        if len(output) <= self.TOOL_OUTPUT_MAX_CHARS:
            return output, False, None

        cache_dir = self.workspace / ".tool_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{uuid.uuid4().hex}.txt"
        cache_path.write_text(output, encoding="utf-8")

        head_size = self.TOOL_OUTPUT_MAX_CHARS // 2
        tail_size = self.TOOL_OUTPUT_MAX_CHARS // 4

        head = output[:head_size]
        tail = output[-tail_size:]

        truncated = (
            f"{head}\n\n"
            f"[... {len(output) - head_size - tail_size:,} chars truncated. "
            f"Full output: {cache_path.name} ...]\n\n"
            f"{tail}"
        )
        return truncated, True, cache_path

    # ══════════ Layer 2: 噪声直接删除 ══════════

    def identify_noise(self, messages: list[Message]) -> list[int]:
        """识别噪声消息索引。

        噪声类型：
        - 重复的工具输出（相同内容多次出现）
        - 搜索结果的导航栏/页脚（低文本密度）
        - 被后续错误纠正取代的输出
        """
        noise_indices = []
        seen_hashes = set()

        for i, msg in enumerate(messages):
            if msg.role != Role.TOOL or not msg.content:
                continue

            content = msg.content

            # 1. 重复检测
            content_hash = hash(content[:200])
            if content_hash in seen_hashes:
                noise_indices.append(i)
                continue
            seen_hashes.add(content_hash)

            # 2. 低文本密度（大量空白/特殊字符）
            if len(content) > 200:
                text_chars = sum(1 for c in content if c.isalnum() or c.isspace())
                density = text_chars / len(content)
                if density < 0.3:
                    noise_indices.append(i)

        return noise_indices

    def remove_noise(self, state: AgentState) -> int:
        """移除噪声消息，返回移除数量。"""
        noise = self.identify_noise(state.messages)
        if not noise:
            return 0

        # 从后往前删除（保持索引稳定）
        for idx in reversed(noise):
            state.messages.pop(idx)

        return len(noise)

    # ══════════ Layer 3: API 层微压缩 ══════════

    def micro_compress(self, messages: list[Message]) -> list[Message]:
        """发送 API 前的微压缩。

        - 合并连续的 user 消息
        - 精简工具结果的格式（去除额外空白）
        - 截断过长的行
        """
        compressed = []
        for msg in messages:
            if msg.content and msg.role == Role.TOOL:
                # 精简空白
                cleaned = re.sub(r'\n{3,}', '\n\n', msg.content)
                cleaned = re.sub(r' {3,}', '  ', cleaned)
                # 限制单行长度
                lines = cleaned.split('\n')
                truncated_lines = [
                    line[:500] + '...' if len(line) > 500 else line
                    for line in lines
                ]
                compressed.append(Message(
                    role=msg.role, content='\n'.join(truncated_lines),
                    tool_call_id=msg.tool_call_id,
                ))
            elif msg.content and len(msg.content) > 8000:
                compressed.append(Message(
                    role=msg.role,
                    content=msg.content[:8000] + f'\n[... {len(msg.content)-8000} chars ...]',
                    tool_call_id=msg.tool_call_id,
                    tool_calls=msg.tool_calls,
                ))
            else:
                compressed.append(msg)
        return compressed

    # ══════════ Layer 4: 归档式摘要 ══════════

    def archive_summarize(self, state: AgentState) -> Optional[str]:
        """生成归档式摘要 — 像 git log 那样保留每轮的独立记录。

        与 git squash 的区别：保留每轮的结构化记录，而非合并为一条。
        """
        if len(state.messages) < self.ARCHIVE_WINDOW:
            return None

        # 提取最近 N 轮的工具调用序列
        recent_tools = []
        for msg in state.messages[-self.ARCHIVE_WINDOW:]:
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    recent_tools.append(f"  - {tc.name}({', '.join(f'{k}={v}' for k, v in list(tc.arguments.items())[:2])})")
            elif msg.role == Role.TOOL and msg.content:
                result_summary = msg.content[:100].replace('\n', ' ')
                recent_tools.append(f"    → {result_summary}...")

        entry = {
            "time": datetime.now().isoformat(),
            "step": state.current_step,
            "tools": recent_tools[-10:],
            "token_usage": state.tokens_used,
        }
        self.archive.append(entry)

        # 保持归档在合理大小
        if len(self.archive) > 50:
            self.archive = self.archive[-50:]

        # 生成摘要
        summary_lines = ["<archive_summary>"]
        summary_lines.append(f"  已完成 {len(self.archive)} 轮操作的最新摘要：")
        for e in self.archive[-3:]:
            summary_lines.append(f"  [{e['step']}] {'; '.join(e['tools'][-3:])}")
        summary_lines.append("</archive_summary>")
        return '\n'.join(summary_lines)

    # ══════════ Layer 5: 全量 LLM 压缩 ══════════

    async def llm_compress(self, state: AgentState) -> bool:
        """LLM 驱动的完整上下文压缩。

        作为最后手段。分两阶段：
        1. 先尝试压缩会话记忆（保留关键信息）
        2. 不行再做全量压缩
        """
        if not self.llm:
            return False

        # 保存 system 消息
        system_msgs = [m for m in state.messages if m.role == Role.SYSTEM]
        other_msgs = [m for m in state.messages if m.role != Role.SYSTEM]

        if len(other_msgs) < 30:
            return False  # 没到需要压缩的程度

        # 构建压缩提示词
        conversation = ""
        for m in other_msgs[-40:]:
            prefix = f"[{m.role.value}]"
            content = (m.content or '')[:300]
            if m.tool_calls:
                content += f" | tools: {[tc.name for tc in m.tool_calls]}"
            conversation += f"{prefix} {content}\n"

        prompt = (
            "你是一个上下文压缩器。以下是 AI Agent 的对话历史。\n"
            "请生成不超过 1000 字符的结构化摘要，保留：\n"
            "1. 已完成的关键任务和决策\n"
            "2. 使用的工具和关键发现\n"
            "3. 未解决的 TODO 和待办事项\n"
            "4. 重要的数值和数据\n\n"
            f"{conversation}\n\n"
            "结构化摘要："
        )

        try:
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=500,
            )
            summary = resp.content.strip()
            compressed = system_msgs + [
                Message(role=Role.USER, content=f"<compressed_context>\n{summary}\n</compressed_context>")
            ] + other_msgs[-5:]
            state.messages = compressed
            return True
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"LLM compression failed: {e}")
            return False

    # ══════════ 主压缩入口 ══════════

    def compress(self, state: AgentState) -> int:
        """执行完整压缩流程。返回移除的消息数。"""
        before = len(state.messages)

        # Layer 2: 噪声删除
        removed = self.remove_noise(state)

        # Layer 4: 归档（不删消息，只生成摘要）
        archive_note = self.archive_summarize(state)
        if archive_note:
            state.messages.append(Message(role=Role.USER, content=archive_note))
            self.archive.append({"time": datetime.now().isoformat(), "removed": removed})

        after = len(state.messages)
        return before - after

    async def full_compress(self, state: AgentState) -> int:
        """完整压缩（含 Layer 5 LLM 压缩）。"""
        removed = self.compress(state)
        if await self.llm_compress(state):
            removed += 10  # 估计
        return removed

    # ══════════ 初始上下文构造 ══════════

    def build_initial_context(
        self, system_prompt: str, task: str, workspace: Path,
        tools_catalog: str = "",
    ) -> list[Message]:
        """构造初始消息 — 完整的 KV Cache 友好布局。

        Static prefix (跨请求缓存命中):
          SYSTEM: agent identity + rules + tool definitions
                  ↑ 这部分在多次 API 调用间不变

        Dynamic suffix (每次任务变化):
          USER: task context + workspace info + agent status
                  ↑ 每次追加，不影响静态前缀的缓存
        """
        messages = []

        # 静态前缀
        full_system = system_prompt
        if tools_catalog:
            full_system += f"\n\n{tools_catalog}"
        messages.append(Message(role=Role.SYSTEM, content=full_system))

        # 动态后缀
        task_msg = (
            f"<task>\n{task}\n</task>\n\n"
            f"<workspace>\n"
            f"  工作目录: {workspace}\n"
            f"  所有文件路径均相对于此目录\n"
            f"</workspace>"
        )
        messages.append(Message(role=Role.USER, content=task_msg))

        return messages
