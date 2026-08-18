"""Explicit plan structure: code-managed tasks instead of LLM-inferred TODOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    evidence: str = ""

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

    def add(self, description: str, depends_on: list[str] | None = None,
            task_id: Optional[str] = None) -> Task:
        tid = task_id or f"task_{len(self.tasks) + 1}"
        task = Task(id=tid, description=description, depends_on=depends_on or [])
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
            }[t.status.value]
            lines.append(f"  {icon} [{t.id}] {t.description}")
            if t.evidence:
                lines.append(f"      evidence: {t.evidence[:80]}")
        lines.append("</task_plan>")
        return "\n".join(lines)

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
