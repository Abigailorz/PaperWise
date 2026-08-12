"""Harness 回归测试 — 瞬时状态消息清理"""

from paperwise.core.types import Message, Role, AgentState
from paperwise.harness.harness import Harness


def test_pre_llm_strips_previous_transient_messages(tmp_path):
    """每轮 pre_llm 应清理上一轮注入的状态栏/预算提醒，只保留当前一步。"""
    h = Harness(tmp_path)
    state = AgentState()
    state.messages = [
        Message(role=Role.USER, content="<agent_status>old status</agent_status>"),
        Message(role=Role.USER, content="<budget_note>old budget</budget_note>"),
        Message(role=Role.USER, content="normal conversation"),
    ]

    h.pre_llm(state)

    status_bars = [m for m in state.messages if (m.content or "").startswith("<agent_status>")]
    budget_notes = [m for m in state.messages if (m.content or "").startswith("<budget")]
    assert len(status_bars) == 1          # 当前轮新注入的状态栏
    assert len(budget_notes) == 0         # 上一轮的预算提醒已被清理
    assert any(m.content == "normal conversation" for m in state.messages)


def test_loop_warning_injected_once(tmp_path):
    """同一轮内循环警告只注入一次，多次 pre_llm 不会叠加。"""
    h = Harness(tmp_path)
    state = AgentState()
    state.messages = [
        Message(role=Role.USER, content="<agent_status>status</agent_status>"),
        Message(role=Role.USER, content="<loop_warning>warning</loop_warning>"),
    ]

    h.pre_llm(state)
    h.pre_llm(state)

    loops = [m for m in state.messages if (m.content or "").startswith("<loop_warning>")]
    statuses = [m for m in state.messages if (m.content or "").startswith("<agent_status>")]
    assert len(loops) <= 1
    assert len(statuses) == 1
