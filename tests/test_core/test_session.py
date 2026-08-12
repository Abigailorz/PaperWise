"""会话持久化回归测试 — 首次运行创建 DB + load 不产生重复 system 消息"""

from pathlib import Path

from paperwise.core.session import AgentSession
from paperwise.core.types import Message, Role
from paperwise.harness.harness import Harness
from paperwise.tools.registry import ToolRegistry


class DummyLLM:
    """最小 LLM 桩 — 仅满足 AgentSession 构造，不触发真实 API。"""

    async def chat_stream(self, **kwargs):
        yield type("Event", (), {"type": "done"})()


def _make_session(ws: Path) -> AgentSession:
    tools = ToolRegistry.create_default(ws)
    harness = Harness(ws)
    return AgentSession(
        workspace=ws, llm_client=DummyLLM(), tools=tools, harness=harness,
        memory=None, knowledge_base=None, skills=None,
    )


def test_session_persists_on_first_run(tmp_path):
    """全新 workspace 下首次保存应创建 SQLite 数据库（路径冲突回归）。"""
    ws = tmp_path / "ws"
    sess = _make_session(ws)
    sess.state.messages = [
        Message(role=Role.SYSTEM, content="<agent_identity>old</agent_identity>"),
        Message(role=Role.USER, content="你好"),
    ]

    sess._save()

    db = ws / ".sessions" / "paperwise.db"
    assert db.exists(), "首次运行时会话数据库应被创建"


def test_session_load_no_duplicate_system_message(tmp_path):
    """load 后应只有一条最新 system 消息，旧 system 不重复。"""
    ws = tmp_path / "ws"
    sess = _make_session(ws)
    sess.state.messages = [
        Message(role=Role.SYSTEM, content="<agent_identity>old</agent_identity>"),
        Message(role=Role.USER, content="你好"),
    ]
    sess._save()

    loaded = AgentSession.load(
        sess.session_id, ws, DummyLLM(),
        ToolRegistry.create_default(ws), Harness(ws),
        memory=None, knowledge_base=None, skills=None,
    )

    assert loaded is not None, "会话应从数据库恢复"
    system_msgs = [m for m in loaded.state.messages if m.role == Role.SYSTEM]
    assert len(system_msgs) == 1
    assert system_msgs[0].content.startswith("<agent_identity>")


async def test_session_tracks_token_usage_across_turns(tmp_path):
    """对话模式应累计 token 消耗，使上下文压缩的触发条件可满足。"""
    ws = tmp_path / "ws"
    sess = _make_session(ws)

    await sess.chat("你好")
    await sess.chat("再详细一点")

    assert sess._tokens_used > 0, "多轮对话后应累计 token 消耗"
    assert sess._token_limit > 0
    state = sess._build_agent_state()
    assert state.tokens_used == sess._tokens_used
    assert state.token_limit == sess._token_limit
