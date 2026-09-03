from pathlib import Path

from paperwise.core.types import Message, Role
from paperwise.memory.session_memory import SessionMemory


def test_extract_and_commit_are_idempotent(tmp_path: Path):
    memory = SessionMemory(tmp_path, session_id="s1", token_threshold=10)
    messages = [
        Message(role=Role.USER, content="first task"),
        Message(role=Role.ASSISTANT, content="second finding"),
        Message(role=Role.TOOL, content="tool evidence", tool_call_id="call_1"),
    ]
    memory.observe_all(messages)

    delta, triggers = memory.maybe_extract()
    assert triggers == ["token_delta"]
    assert delta is not None and len(delta.message_ids) == 3

    memory.commit(delta)
    assert memory.state.last_processed_message_id == messages[-1].message_id
    snapshot = memory.state.to_dict()
    repeated = memory.extract_delta()
    assert repeated.message_ids == []

    memory.commit(repeated)
    memory.commit(delta)
    assert memory.state.to_dict() == snapshot


def test_before_compaction_is_always_a_trigger(tmp_path: Path):
    memory = SessionMemory(tmp_path, session_id="s1", token_threshold=10_000)
    message = Message(role=Role.USER, content="important state")
    memory.observe(message)

    delta, triggers = memory.maybe_extract(before_compaction=True)
    assert triggers == ["before_compaction"]
    assert delta and delta.message_ids == [message.message_id]


def test_state_survives_restart(tmp_path: Path):
    first = SessionMemory(tmp_path, session_id="resume")
    message = Message(role=Role.USER, content="pending " + "x" * 100)
    first.observe(message)
    first.commit(first.extract_delta())

    second = SessionMemory(tmp_path, session_id="resume")
    assert second.state.last_processed_message_id == message.message_id
    assert second.state.summary
    assert second.extract_delta().message_ids == []
