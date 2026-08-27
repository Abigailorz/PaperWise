"""Dynamic DAG executor for the PaperWise orchestration layer.

Runs a Plan as a stateful, conditional, parallel task graph.  Nodes are
executed by caller-provided handlers, which allows the executor to stay
agnostic of the actual LLM / tool implementations.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from paperwise.core.plan import Plan, Task, TaskStatus
from paperwise.orchestration.types import GraphState, CriticResult, NodeSpec
from paperwise.orchestration.registries import NODE_REGISTRY


NodeHandler = Callable[[NodeSpec, Task, GraphState], Awaitable[Any]]
ConditionFn = Callable[[GraphState, Task], bool]


class DAGExecutorError(Exception):
    """Raised when the DAG executor cannot complete a plan."""


class ReplanNeededError(Exception):
    """Raised when a task should trigger a replan."""

    def __init__(self, task_id: str, reason: str):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Replan needed for {task_id}: {reason}")


# Default condition predicates.  Add more as the system grows.
DEFAULT_CONDITIONS: dict[str, ConditionFn] = {
    "always": lambda _state, _task: True,
    "requires_verification": lambda _state, task: "verify" in task.description.lower(),
    "critic_has_issues": lambda state, _task: _critic_has_issues(state),
}


def _critic_has_issues(state: GraphState) -> bool:
    """Return True if the latest critic result still has critical or major issues."""
    critic = state.get_artifact("critic_result")
    if isinstance(critic, dict):
        return critic.get("critical", 0) > 0 or critic.get("major", 0) > 0
    if isinstance(critic, CriticResult):
        return critic.has_critical or critic.has_major
    return False


@dataclass
class ExecutionConfig:
    """Runtime configuration for the DAG executor."""

    max_parallel: int = 4
    default_timeout: float = 300.0
    enable_replan: bool = False
    replan_callback: Optional[Callable[[Plan, Task, str, GraphState], Awaitable[Plan]]] = None


class DAGExecutor:
    """Execute a Plan as a dynamic DAG.

    Usage:
        executor = DAGExecutor()
        executor.register_handler("reader", my_reader_handler)
        ...
        result = await executor.run(plan, graph_state)
    """

    def __init__(self, config: Optional[ExecutionConfig] = None):
        self.config = config or ExecutionConfig()
        self._handlers: dict[str, NodeHandler] = {}
        self._conditions: dict[str, ConditionFn] = dict(DEFAULT_CONDITIONS)

    def register_handler(self, node_id: str, handler: NodeHandler) -> None:
        self._handlers[node_id] = handler

    def register_condition(self, name: str, fn: ConditionFn) -> None:
        self._conditions[name] = fn

    def _should_skip(self, task: Task, state: GraphState) -> bool:
        """Return True if the task should be skipped due to its condition."""
        if not task.condition:
            return False
        fn = self._conditions.get(task.condition)
        if fn is None:
            # Unknown condition: be conservative and do NOT skip.
            return False
        try:
            return not fn(state, task)
        except Exception:
            return False

    def _node_spec(self, node_id: str) -> NodeSpec:
        node = NODE_REGISTRY.get(node_id)
        if node is None:
            raise DAGExecutorError(f"Node {node_id} not registered")
        return node

    async def run(
        self,
        plan: Plan,
        state: GraphState,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> dict[str, Any]:
        """Execute the plan until completion or irrecoverable failure.

        Returns a dict with {success, final_artifacts, steps, error_message}.
        """
        total_steps = 0
        final_error = ""

        try:
            while not plan.done:
                if state.is_budget_exhausted():
                    raise DAGExecutorError("Budget exhausted")

                ready = plan.next_executable_group()
                if not ready:
                    # No ready tasks but plan not done -> likely a cycle or blocked node.
                    pending = [t.id for t in plan.tasks if t.status == TaskStatus.PENDING]
                    raise DAGExecutorError(f"No executable tasks; pending: {pending}")

                # Skip tasks whose condition is false.
                to_run: list[Task] = []
                for task in ready:
                    if self._should_skip(task, state):
                        plan.mark_done(task.id, evidence="skipped_by_condition")
                        if progress_callback:
                            progress_callback(task.id, "skipped")
                    else:
                        to_run.append(task)

                if not to_run:
                    continue

                if progress_callback:
                    for task in to_run:
                        progress_callback(task.id, "started")

                # Execute the group in parallel.
                results = await asyncio.gather(
                    *[self._execute_task(task, state, progress_callback) for task in to_run],
                    return_exceptions=True,
                )

                for task, result in zip(to_run, results):
                    total_steps += await self._process_result(
                        task, result, plan, state, progress_callback
                    )

        except DAGExecutorError as e:
            final_error = str(e)

        success = final_error == "" and plan.done and all(
            t.status == TaskStatus.DONE for t in plan.tasks
        )
        return {
            "success": success,
            "plan_done": plan.done,
            "steps": total_steps,
            "error_message": final_error,
            "artifacts": dict(state.artifacts),
        }

    async def _execute_task(
        self,
        task: Task,
        state: GraphState,
        progress_callback: Optional[Callable[[str, str], None]],
    ) -> Any:
        """Run a single task using its registered handler."""
        handler = self._handlers.get(task.id)
        if handler is None:
            # Fall back to node id from registry, but no handler means we cannot run it.
            raise DAGExecutorError(f"No handler registered for node {task.id}")

        node_spec = self._node_spec(task.id)
        task.status = TaskStatus.IN_PROGRESS
        state.log_node(task.id, {"status": "started"})

        try:
            return await asyncio.wait_for(
                handler(node_spec, task, state),
                timeout=self.config.default_timeout,
            )
        except asyncio.TimeoutError:
            raise DAGExecutorError(f"Node {task.id} timed out")

    async def _process_result(
        self,
        task: Task,
        result: Any,
        plan: Plan,
        state: GraphState,
        progress_callback: Optional[Callable[[str, str], None]],
    ) -> int:
        """Process a single task result and update plan / state.

        Returns the number of sub-steps consumed (heuristic: 1 per task execution).
        """
        if isinstance(result, Exception):
            task.retry_count += 1
            task.error_message = str(result)
            state.log_node(task.id, {"status": "error", "error": str(result)})

            if task.retry_count <= task.max_retries:
                task.status = TaskStatus.PENDING
                if progress_callback:
                    progress_callback(task.id, f"retry_{task.retry_count}")
                return 1

            if self.config.enable_replan and self.config.replan_callback:
                try:
                    new_plan = await self.config.replan_callback(plan, task, str(result), state)
                    plan.tasks = new_plan.tasks
                    if progress_callback:
                        progress_callback(task.id, "replan")
                    return 1
                except Exception as replan_err:
                    task.error_message = f"replan_failed: {replan_err}"

            plan.mark_failed(task.id, evidence=str(result))
            if progress_callback:
                progress_callback(task.id, "failed")
            return 1

        # Save result on task and state if output_artifact specified.
        task.result = result
        if task.output_artifact:
            state.set_artifact(task.output_artifact, result)
        state.log_node(task.id, {"status": "success", "artifact": task.output_artifact})

        # Confidence-based replan trigger.
        confidence = getattr(result, "confidence", None)
        if confidence is None and isinstance(result, dict):
            confidence = result.get("confidence")
        if task.confidence_threshold and confidence is not None and confidence < task.confidence_threshold:
            if self.config.enable_replan and self.config.replan_callback:
                new_plan = await self.config.replan_callback(
                    plan, task, f"confidence {confidence} < threshold {task.confidence_threshold}", state
                )
                plan.tasks = new_plan.tasks
                if progress_callback:
                    progress_callback(task.id, "replan_low_confidence")
                return 1

        plan.mark_done(task.id, evidence=str(task.output_artifact or "ok"))
        if progress_callback:
            progress_callback(task.id, "done")
        return 1


def build_plan_from_workflow(
    workflow_id: str,
    context: dict[str, Any],
) -> Plan:
    """Instantiate a Plan from a WorkflowTemplate.

    The resulting Plan contains the base DAG nodes with their dependencies,
    parallel groups and conditions.
    """
    from paperwise.orchestration.registries import WORKFLOW_REGISTRY

    workflow = WORKFLOW_REGISTRY.get(workflow_id)
    if workflow is None:
        raise DAGExecutorError(f"Workflow {workflow_id} not found")

    plan = Plan()
    for node_def in workflow.base_dag:
        plan.add(
            description=node_def.get("description", node_def["id"]),
            task_id=node_def["id"],
            depends_on=node_def.get("depends_on", []),
            parallel_group=node_def.get("parallel_group"),
            condition=node_def.get("condition"),
            max_retries=node_def.get("max_retries", 0),
            output_artifact=node_def.get("output_artifact"),
            confidence_threshold=node_def.get("confidence_threshold", 0.0),
        )
    return plan
