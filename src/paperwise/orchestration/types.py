"""Shared orchestration types.

This module defines the standardized contracts used by the dynamic DAG
execution layer: NodeSpec, WorkflowTemplate, Capability, GraphState,
TaskRoute, and base Artifact types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from enum import Enum


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class TaskType(str, Enum):
    CHAT = "chat"
    SIMPLE_QA = "simple_qa"
    TOOL_QA = "tool_qa"
    SIMPLE_TASK = "simple_task"
    COMPLEX_TASK = "complex_task"
    RESEARCH = "research"
    GENERATION = "generation"
    HYBRID = "hybrid"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REPLAN = "needs_replan"


@dataclass
class TaskRoute:
    """Routing decision produced by the TaskClassifier."""

    task_type: TaskType = TaskType.RESEARCH
    complexity: TaskComplexity = TaskComplexity.COMPLEX
    requires_tools: bool = False
    requires_planning: bool = True
    requires_artifacts: bool = True
    workflow: str = "paper_analysis"
    confidence: str = "low"  # high | medium | low
    escalate_on_failure: bool = False
    reason: str = ""

    @property
    def is_simple(self) -> bool:
        return self.complexity == TaskComplexity.SIMPLE

    @property
    def is_complex(self) -> bool:
        return self.complexity == TaskComplexity.COMPLEX


@dataclass
class Capability:
    """A high-level capability the system can perform."""

    id: str
    name: str
    description: str
    required_nodes: list[str] = field(default_factory=list)
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)


@dataclass
class Artifact:
    """Base class for structured data passed between nodes."""

    artifact_type: str = "artifact"
    source_node: str = ""
    path: Optional[Path] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class PaperArtifact(Artifact):
    paper_id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    text_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    figure_paths: list[Path] = field(default_factory=list)
    table_paths: list[Path] = field(default_factory=list)


@dataclass
class SectionArtifact(Artifact):
    title: str = ""
    start_line: int = 0
    end_line: int = 0
    summary: str = ""


@dataclass
class ClaimArtifact(Artifact):
    claim: str = ""
    evidence: list[str] = field(default_factory=list)
    source_lines: list[tuple[int, int]] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class MethodArtifact(Artifact):
    problem: str = ""
    method: str = ""
    key_idea: str = ""
    pipeline: list[str] = field(default_factory=list)
    claims: list[ClaimArtifact] = field(default_factory=list)


@dataclass
class ReportArtifact(Artifact):
    outline: dict = field(default_factory=dict)
    section_paths: dict[str, Path] = field(default_factory=dict)
    final_report_path: Optional[Path] = None
    citations_verified: bool = False


@dataclass
class SlideArtifact(Artifact):
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    figure_path: Optional[Path] = None


@dataclass
class VerificationPolicy:
    """How a node output should be verified."""

    required: bool = True
    citation_check: bool = False
    json_schema_check: bool = False
    output_exists_check: bool = True
    min_output_length: int = 0


@dataclass
class RetryPolicy:
    max_retries: int = 0
    backoff_seconds: float = 1.0


@dataclass
class NodeSpec:
    """Standard interface for a single specialist node / sub-agent."""

    id: str
    category: str
    name: str
    description: str
    system_prompt: str = ""
    task_template: str = ""
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    required_capabilities: list[str] = field(default_factory=list)
    optional_capabilities: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    cost: str = "medium"  # low | medium | high
    latency: str = "medium"
    allowed_tools: list[str] = field(default_factory=list)
    output_path: str = ""
    max_steps: int = 25
    enable_plan: bool = False
    context_xml: str = ""
    verification_policy: VerificationPolicy = field(default_factory=VerificationPolicy)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    # Runtime binding (optional): if this node maps to a tool rather than an agent.
    tool_name: Optional[str] = None


@dataclass
class WorkflowTemplate:
    """A pre-defined workflow template for a domain or task family."""

    id: str
    name: str
    description: str
    trigger_intents: list[str] = field(default_factory=list)
    # List of base node ids with dependencies.
    base_dag: list[dict] = field(default_factory=list)
    default_artifacts: list[str] = field(default_factory=list)
    dynamic_expandable: bool = True


@dataclass
class GraphState:
    """Runtime state shared across the dynamic DAG execution."""

    task: str = ""
    objectives: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    execution_history: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    budget: dict[str, float] = field(default_factory=dict)
    iteration: int = 0

    def get_artifact(self, name: str) -> Any:
        return self.artifacts.get(name)

    def set_artifact(self, name: str, value: Any) -> None:
        self.artifacts[name] = value

    def log_node(self, node_id: str, result: dict) -> None:
        self.execution_history.append({"node_id": node_id, "result": result})

    def is_budget_exhausted(self) -> bool:
        tokens = self.budget.get("tokens_used", 0)
        token_limit = self.budget.get("token_limit", 180_000)
        steps = self.budget.get("steps_used", 0)
        step_limit = self.budget.get("step_limit", 100)
        return tokens >= token_limit or steps >= step_limit


@dataclass
class CriticResult:
    """Structured output from a Critic node."""

    status: str = "unknown"  # pass | incomplete | reject
    confidence: float = 0.0
    missing_evidence: list[str] = field(default_factory=list)
    missing_tasks: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    severity: dict = field(default_factory=lambda: {"critical": 0, "major": 0, "minor": 0})

    @property
    def has_critical(self) -> bool:
        return self.severity.get("critical", 0) > 0

    @property
    def has_major(self) -> bool:
        return self.severity.get("major", 0) > 0
