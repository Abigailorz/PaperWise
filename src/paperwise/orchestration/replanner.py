"""Dynamic replanning for the PaperWise DAG executor.

When a node fails, returns low confidence, or the Critic finds gaps, the
ReplanAgent inserts new nodes into the Plan while preserving completed work.
"""

from __future__ import annotations

from paperwise.core.plan import Plan, Task
from paperwise.orchestration.types import GraphState


class ReplanAgent:
    """Insert recovery / expansion nodes into a running Plan."""

    async def replan(
        self,
        plan: Plan,
        failed_task: Task,
        reason: str,
        state: GraphState,
    ) -> Plan:
        """Return a new Plan that keeps done tasks and adds corrective nodes.

        This is intentionally conservative: we only add nodes that are directly
        related to the failure reason.
        """
        new_plan = Plan()
        # Copy completed tasks (immutable state).
        for t in plan.tasks:
            if t.status.value == "done":
                new_plan.add(
                    description=t.description,
                    task_id=t.id,
                    depends_on=list(t.depends_on),
                    parallel_group=t.parallel_group,
                    condition=t.condition,
                    max_retries=t.max_retries,
                    output_artifact=t.output_artifact,
                    confidence_threshold=t.confidence_threshold,
                )
                new_plan.mark_done(t.id, evidence=t.evidence)

        # Determine corrective action based on the failed node and reason.
        corrective_id = self._pick_corrective_node(failed_task, reason, state)

        # Add the corrective node after the failed task's done dependencies.
        deps = self._dependencies_for_corrective(failed_task, plan)
        new_plan.add(
            description=f"Replan correction for {failed_task.id}: {reason}",
            task_id=corrective_id,
            depends_on=deps,
            max_retries=1,
        )

        # Re-add downstream nodes that have not yet run so they can consume the
        # corrected artifact.
        for t in plan.tasks:
            if t.status.value != "done" and t.id != failed_task.id and t.id != corrective_id:
                depends_on = list(t.depends_on)
                if failed_task.id in depends_on:
                    depends_on = [dep if dep != failed_task.id else corrective_id for dep in depends_on]
                new_plan.add(
                    description=t.description,
                    task_id=t.id,
                    depends_on=depends_on,
                    parallel_group=t.parallel_group,
                    condition=t.condition,
                    max_retries=t.max_retries,
                    output_artifact=t.output_artifact,
                    confidence_threshold=t.confidence_threshold,
                )

        return new_plan

    def _pick_corrective_node(self, failed_task: Task, reason: str, state: GraphState) -> str:
        reason_lower = reason.lower()
        if "reader" in failed_task.id or "read_paper" in failed_task.id:
            return "re_read_section"
        if "verif" in failed_task.id or "numerical" in reason_lower or "citation" in reason_lower:
            return "re_verify_with_code"
        if "writer" in failed_task.id or "report" in failed_task.id or "ppt" in failed_task.id:
            return "revision"
        if "critic" in failed_task.id or "missing_evidence" in reason_lower:
            return "expand_evidence"
        return "dynamic_research"

    def _dependencies_for_corrective(self, failed_task: Task, plan: Plan) -> list[str]:
        """Return the nearest completed dependencies that the corrective node can build on."""
        # Prefer the failed task's own dependencies; if none are done, use the
        # most recently completed task.
        done_deps = [dep for dep in failed_task.depends_on
                     if plan.get(dep) and plan.get(dep).status.value == "done"]
        if done_deps:
            return done_deps
        done_tasks = [t.id for t in plan.tasks if t.status.value == "done"]
        return done_tasks[-1:] if done_tasks else []
