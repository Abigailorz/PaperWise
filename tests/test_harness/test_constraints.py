"""约束引擎测试"""

import pytest
from pathlib import Path
from paperwise.harness.constraints import ConstraintEngine, ConstraintViolation
from paperwise.core.types import ToolCall, AgentState


class TestConstraintEngine:
    def test_known_tool(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="read_file", arguments={"path": "test.txt"})
        state = AgentState()
        assert engine.check(tc, state) is True

    def test_unknown_tool(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="hack_the_planet", arguments={})
        state = AgentState()
        with pytest.raises(ConstraintViolation, match="Unknown tool"):
            engine.check(tc, state)

    def test_tool_call_limit(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="code_interpreter", arguments={"code": "1+1"})
        state = AgentState()
        state.tool_call_count["code_interpreter"] = 15  # at limit
        with pytest.raises(ConstraintViolation, match="limit reached"):
            engine.check(tc, state)

    def test_dangerous_path_blocked(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="read_file", arguments={"path": "../../etc/passwd"})
        state = AgentState()
        with pytest.raises(ConstraintViolation):
            engine.check(tc, state)

    def test_safe_path_allowed(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="read_file", arguments={"path": "text.md"})
        state = AgentState()
        assert engine.check(tc, state) is True

    def test_blocked_bash_command(self, workspace: Path):
        engine = ConstraintEngine(workspace)
        tc = ToolCall(id="1", name="bash", arguments={"command": "sudo rm -rf /"})
        state = AgentState()
        with pytest.raises(ConstraintViolation, match="Dangerous command"):
            engine.check(tc, state)
