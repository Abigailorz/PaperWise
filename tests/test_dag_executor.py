"""Smoke tests for the dynamic DAG executor.

These tests exercise Mini-DAG and Full-DAG paths without requiring a live LLM.
"""

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.core.plan import Plan
from paperwise.orchestration.dag_executor import DAGExecutor, ExecutionConfig, DAGExecutorError
from paperwise.orchestration.replanner import ReplanAgent
from paperwise.orchestration.types import GraphState, NodeSpec


async def ok_handler(node: NodeSpec, task, state: GraphState):
    state.set_artifact(task.output_artifact, f"{task.id}_result")
    return f"{task.id}_result"


async def fail_once_handler(node: NodeSpec, task, state: GraphState):
    key = f"{task.id}_attempts"
    attempts = state.get_artifact(key) or 0
    state.set_artifact(key, attempts + 1)
    if attempts == 0:
        raise RuntimeError("first attempt fails")
    state.set_artifact(task.output_artifact, f"{task.id}_result")
    return f"{task.id}_result"


async def low_conf_handler(node: NodeSpec, task, state: GraphState):
    return {"value": 1, "confidence": 0.1}


@pytest.fixture
def state():
    return GraphState(
        task="test",
        budget={"token_limit": 100_000, "step_limit": 100},
    )


def test_dag_linear(state):
    async def _inner():
        plan = Plan()
        plan.add("read", task_id="read", output_artifact="a")
        plan.add("write", depends_on=["read"], task_id="write", output_artifact="b")

        executor = DAGExecutor()
        executor.register_handler("read", ok_handler)
        executor.register_handler("write", ok_handler)

        result = await executor.run(plan, state)
        assert result["success"] is True
        assert state.get_artifact("a") == "read_result"
        assert state.get_artifact("b") == "write_result"

    asyncio.run(_inner())


def test_dag_parallel_groups(state):
    async def _inner():
        plan = Plan()
        plan.add("read", task_id="read", output_artifact="a")
        plan.add("analyze", depends_on=["read"], task_id="analyze", parallel_group="research", output_artifact="b")
        plan.add("verify", depends_on=["read"], task_id="verify", parallel_group="research", output_artifact="c")
        plan.add("report", depends_on=["analyze", "verify"], task_id="report", output_artifact="d")

        executor = DAGExecutor()
        for name in ("read", "analyze", "verify", "report"):
            executor.register_handler(name, ok_handler)

        result = await executor.run(plan, state)
        assert result["success"] is True
        assert state.get_artifact("d") == "report_result"

    asyncio.run(_inner())


def test_dag_condition_skip(state):
    async def _inner():
        plan = Plan()
        plan.add("read", task_id="read", output_artifact="a")
        plan.add("optional check", depends_on=["read"], task_id="verify", condition="requires_verification", output_artifact="b")
        plan.add("report", depends_on=["verify"], task_id="report", output_artifact="c")

        executor = DAGExecutor()
        executor.register_handler("read", ok_handler)
        executor.register_handler("verify", ok_handler)
        executor.register_handler("report", ok_handler)

        result = await executor.run(plan, state)
        assert result["success"] is True
        # verify was skipped because task description does not contain "verify"
        assert state.get_artifact("b") is None
        assert state.get_artifact("c") == "report_result"

    asyncio.run(_inner())


def test_dag_replan(state):
    async def _inner():
        plan = Plan()
        plan.add("read", task_id="read", output_artifact="a")
        plan.add("fragile", depends_on=["read"], task_id="fragile", output_artifact="b", max_retries=0)
        plan.add("report", depends_on=["fragile"], task_id="report", output_artifact="c")

        async def replan_callback(plan, failed_task, reason, state):
            # Insert a corrective node and rewire downstream.
            new_plan = Plan()
            new_plan.add("read", task_id="read", output_artifact="a")
            new_plan.mark_done("read", evidence="ok")
            new_plan.add("fix", depends_on=["read"], task_id="fix", output_artifact="b")
            new_plan.add("report", depends_on=["fix"], task_id="report", output_artifact="c")
            return new_plan

        executor = DAGExecutor(
            config=ExecutionConfig(enable_replan=True, replan_callback=replan_callback)
        )
        executor.register_handler("read", ok_handler)
        executor.register_handler("fragile", lambda n, t, s: (_ for _ in ()).throw(RuntimeError("fail")))
        executor.register_handler("fix", ok_handler)
        executor.register_handler("report", ok_handler)

        result = await executor.run(plan, state)
        assert result["success"] is True
        assert state.get_artifact("c") == "report_result"

    asyncio.run(_inner())


def test_dag_budget_exhausted(state):
    async def _inner():
        plan = Plan()
        plan.add("read", task_id="read", output_artifact="a")
        plan.add("write", depends_on=["read"], task_id="write", output_artifact="b")

        state.budget = {"token_limit": 100_000, "step_limit": 1}

        executor = DAGExecutor()
        executor.register_handler("read", ok_handler)
        executor.register_handler("write", ok_handler)

        result = await executor.run(plan, state)
        assert result["success"] is False
        assert "Budget" in result["error_message"]

    asyncio.run(_inner())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
