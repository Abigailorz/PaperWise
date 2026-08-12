"""会话恢复 API 测试"""

import pytest
from pathlib import Path

from paperwise.api.server import app
from paperwise.core.session import AgentSession
from paperwise.core.types import Message, Role
from paperwise.harness.harness import Harness
from paperwise.tools.registry import ToolRegistry


class DummyLLM:
    async def chat_stream(self, **kwargs):
        yield type("Event", (), {"type": "done"})()


def test_sessions_list_roundtrip(client, tmp_path):
    import asyncio
    import paperwise.config.settings as settings_mod
    ws = settings_mod.get_settings().workspace_dir

    # 1. 创建会话并保存到磁盘
    r = client.post("/api/sessions")
    sid = r.json()["session_id"]
    assert sid

    ws_dir = ws / f"session_{sid}"
    ws_dir.mkdir(parents=True, exist_ok=True)
    tools = ToolRegistry.create_default(ws_dir)
    harness = Harness(ws_dir)
    sess = AgentSession(workspace=ws_dir, llm_client=DummyLLM(), tools=tools,
                        harness=harness, memory=None, knowledge_base=None,
                        skills=None, session_id=sid)
    sess.state.messages = [Message(role=Role.USER, content="你好")]
    sess._save()

    # 2. GET /api/sessions 应列出该会话
    r = client.get("/api/sessions")
    items = r.json()["sessions"]
    assert any(item["session_id"] == sid for item in items)

    # 3. 通过 _ensure_session 走真实恢复路径（从磁盘加载，不触发网络）
    from paperwise.api.server import _ensure_session
    restored = asyncio.run(_ensure_session(sid, ws_dir))
    assert any(
        m.role == Role.USER and m.content == "你好"
        for m in restored.state.messages
    )

    # 4. 历史消息可读
    r = client.get(f"/api/sessions/{sid}/history")
    assert r.status_code == 200
    assert any(m["content"] == "你好" for m in r.json()["messages"])
