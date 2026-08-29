"""Explicit plan structure: code-managed tasks instead of LLM-inferred TODOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REPLAN = "needs_replan"


@dataclass
class Task:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    evidence: str = ""
    # Dynamic DAG extensions
    parallel_group: Optional[str] = None
    condition: Optional[str] = None          # condition name / predicate id
    condition_fn: Optional[Callable[[Any], bool]] = None
    retry_count: int = 0
    max_retries: int = 0
    output_artifact: Optional[str] = None
    confidence_threshold: float = 0.0
    result: Optional[Any] = None
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "evidence": self.evidence,
        }


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)
    current_task_id: Optional[str] = None

    def add(
        self,
        description: str,
        depends_on: list[str] | None = None,
        task_id: Optional[str] = None,
        parallel_group: Optional[str] = None,
        condition: Optional[str] = None,
        condition_fn: Optional[Callable[[Any], bool]] = None,
        max_retries: int = 0,
        output_artifact: Optional[str] = None,
        confidence_threshold: float = 0.0,
    ) -> Task:
        tid = task_id or f"task_{len(self.tasks) + 1}"
        task = Task(
            id=tid,
            description=description,
            depends_on=depends_on or [],
            parallel_group=parallel_group,
            condition=condition,
            condition_fn=condition_fn,
            max_retries=max_retries,
            output_artifact=output_artifact,
            confidence_threshold=confidence_threshold,
        )
        self.tasks.append(task)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def next_executable(self) -> Optional[Task]:
        for t in self.tasks:
            if t.status != TaskStatus.PENDING:
                continue
            if all(self.get(dep).status == TaskStatus.DONE for dep in t.depends_on):
                return t
        return None

    def next_executable_group(self) -> list[Task]:
        """Return all pending tasks whose dependencies are done, grouped by parallel_group."""
        ready = [
            t for t in self.tasks
            if t.status == TaskStatus.PENDING
            and all(self.get(dep).status == TaskStatus.DONE for dep in t.depends_on)
        ]
        if not ready:
            return []
        first_group = ready[0].parallel_group
        if first_group:
            return [t for t in ready if t.parallel_group == first_group]
        return [ready[0]]

    def mark_done(self, task_id: str, evidence: str = "") -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.DONE
        task.evidence = evidence
        return True

    def mark_in_progress(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.IN_PROGRESS
        self.current_task_id = task_id
        return True

    def mark_failed(self, task_id: str, evidence: str = "") -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.FAILED
        task.evidence = evidence
        return True

    def mark_needs_replan(self, task_id: str, evidence: str = "") -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.NEEDS_REPLAN
        task.evidence = evidence
        return True

    @property
    def done(self) -> bool:
        return all(t.status == TaskStatus.DONE for t in self.tasks)

    @property
    def progress(self) -> tuple[int, int]:
        done = sum(1 for t in self.tasks if t.status == TaskStatus.DONE)
        return done, len(self.tasks)

    def to_status_text(self) -> str:
        if not self.tasks:
            return ""
        lines = ["<task_plan>"]
        for t in self.tasks:
            icon = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "done": "[x]",
                "failed": "[!]",
                "needs_replan": "[?]",
            }[t.status.value]
            lines.append(f"  {icon} [{t.id}] {t.description}")
            if t.evidence:
                lines.append(f"      evidence: {t.evidence[:80]}")
        lines.append("</task_plan>")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "current_task_id": self.current_task_id,
            "done": self.done,
            "progress": self.progress,
        }

    def merge(self, other: "Plan") -> "Plan":
        """合并另一个 Plan 的任务到当前 Plan，跳过已存在的 task_id。

        用于 replan 时把恢复节点插入现有 Plan。
        """
        existing_ids = {t.id for t in self.tasks}
        for task in other.tasks:
            if task.id not in existing_ids:
                self.tasks.append(task)
                existing_ids.add(task.id)
        return self

    def to_dependency_graph(self) -> dict[str, list[str]]:
        """返回 task_id -> list[dependency_id] 的依赖图。"""
        return {t.id: list(t.depends_on) for t in self.tasks}

    def to_todo_items(self) -> list[dict]:
        return [
            {"text": t.description, "status": t.status.value, "id": t.id}
            for t in self.tasks
        ]

    @classmethod
    def from_task_text(cls, task: str) -> "Plan":
        """Generate an initial plan from task text without a model call."""
        plan = cls()
        text = task.lower()

        plan.add("Read and understand the paper", task_id="read_paper")

        if any(k in text for k in ["report", "write", "generate"]) or "section" in text:
            plan.add("Analyze methodology and experiments",
                     depends_on=["read_paper"], task_id="analyze_method")
            plan.add("Generate structured analysis report",
                     depends_on=["analyze_method"], task_id="generate_report")

        if any(k in text for k in ["verify", "validate", "code", "numerical", "data"]):
            plan.add("Verify numerical claims with code",
                     depends_on=["read_paper"], task_id="verify_data")

        if any(k in text for k in ["critical", "limitation", "weakness"]):
            plan.add("Conduct critical analysis of limitations",
                     depends_on=["read_paper"], task_id="critical_analysis")

        if any(k in text for k in ["ppt", "pptx", "presentation", "slides", "slide"]):
            plan.add("Build academic presentation slides",
                     depends_on=["read_paper"], task_id="generate_pptx")

        return plan
