from types import SimpleNamespace

from paperwise.core.agent_loop import AgentLoopMixin
from paperwise.core.plan import Plan
from paperwise.core.types import ToolCall, ToolResult


class _PlanTracker(AgentLoopMixin):
    def __init__(self, plan: Plan):
        self._plan = plan
        self.state = SimpleNamespace(todo_items=plan.to_todo_items())


def test_report_writer_sections_mark_plan_done():
    plan = Plan.from_task_text("Generate a report with limitations analysis")
    tracker = _PlanTracker(plan)

    writes = [
        {"path": "report\\sections\\methodology.md"},
        {"path": "report\\sections\\experiments.md"},
        {"path": "report\\sections\\limitations.md"},
        {"path": "report\\report.md"},
    ]
    for i, arguments in enumerate(writes, 1):
        tracker._update_plan_from_tool_call(
            ToolCall(id=str(i), name="write_file", arguments=arguments),
            ToolResult(tool_call_id=str(i), name="write_file", output="done"),
        )

    statuses = {item["id"]: item["status"] for item in tracker.state.todo_items}
    assert statuses["analyze_method"] == "done"
    assert statuses["critical_analysis"] == "done"
    assert statuses["generate_report"] == "done"
