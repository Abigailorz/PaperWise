import asyncio

import pytest

from paperwise.core.plan import Plan, TaskStatus
from paperwise.orchestration.dag_executor import (
    DAGExecutor,
    DAGExecutorError,
    ExecutionConfig,
)
from paperwise.orchestration.types import GraphState


def test_fatal_infrastructure_error_skips_replan():
    replan_calls = []

    async def replan(plan, task, reason, state):
        replan_calls.append((task.id, reason))
        return plan

    executor = DAGExecutor(ExecutionConfig(
        enable_replan=True,
        replan_callback=replan,
    ))
    plan = Plan()
    plan.add("Read the paper", task_id="read_paper")
    state = GraphState(task="Write a report")

    with pytest.raises(DAGExecutorError, match="Infrastructure failure"):
        asyncio.run(executor._process_result(
            plan.tasks[0],
            RuntimeError("APIConnectionError: Connection error."),
            plan,
            state,
            None,
        ))

    assert replan_calls == []
    assert plan.tasks[0].status == TaskStatus.PENDING
