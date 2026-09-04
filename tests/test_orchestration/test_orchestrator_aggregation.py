from paperwise.core.plan import Plan
from paperwise.core.types import AgentResult, Message, Role
from paperwise.orchestration.orchestrator import SmartOrchestrator


def test_aggregates_sub_agent_trajectories():
    orchestrator = SmartOrchestrator.__new__(SmartOrchestrator)
    orchestrator._sub_agent_messages = []
    orchestrator._sub_agent_tool_stats = {}
    orchestrator._sub_agent_tokens = 0
    result = AgentResult(
        messages=[Message(role=Role.TOOL, content="ok", tool_call_id="call_1")],
        tool_stats={"read_file": 2},
        tokens_used=7,
    )

    orchestrator._record_sub_agent_trajectory(result)

    assert len(orchestrator._sub_agent_messages) == 1
    assert orchestrator._sub_agent_tool_stats == {"read_file": 2}
    assert orchestrator._sub_agent_tokens == 7
