"""Registry layer for the dynamic orchestration system.

Provides CapabilityRegistry, NodeRegistry, WorkflowRegistry, and ArtifactRegistry.
These registries allow the system to reason about what it can do, which nodes
exist, which workflow templates are available, and what artifacts they
produce/consume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from paperwise.orchestration.types import (
    Artifact,
    Capability,
    ClaimArtifact,
    MethodArtifact,
    NodeSpec,
    PaperArtifact,
    ReportArtifact,
    SectionArtifact,
    SlideArtifact,
    WorkflowTemplate,
    VerificationPolicy,
)


class CapabilityRegistry:
    """High-level capabilities of the system."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            Capability(
                id="paper_summarize",
                name="Paper Summarization",
                description="Produce a concise summary of a single paper.",
                required_nodes=["parse_pdf", "extract_text", "summarize"],
                input_artifacts=["PaperArtifact"],
                output_artifacts=["ReportArtifact"],
            )
        )
        self.register(
            Capability(
                id="paper_deep_analysis",
                name="Paper Deep Analysis",
                description="Analyze problem, method, experiments, related work and limitations.",
                required_nodes=[
                    "parse_pdf",
                    "problem_analysis",
                    "method_analysis",
                    "experiment_analysis",
                    "related_work_analysis",
                    "synthesis",
                ],
                input_artifacts=["PaperArtifact"],
                output_artifacts=["MethodArtifact", "ReportArtifact"],
            )
        )
        self.register(
            Capability(
                id="paper_to_report",
                name="Paper to Report",
                description="Generate a structured Markdown analysis report from a paper.",
                required_nodes=[
                    "paper_deep_analysis",
                    "report_outline",
                    "report_section",
                    "report_assemble",
                    "critic",
                    "revision",
                ],
                input_artifacts=["PaperArtifact"],
                output_artifacts=["ReportArtifact"],
            )
        )
        self.register(
            Capability(
                id="paper_to_ppt",
                name="Paper to PPT",
                description="Generate an academic presentation from a paper.",
                required_nodes=[
                    "paper_deep_analysis",
                    "ppt_outline",
                    "ppt_slide",
                    "ppt_assemble",
                ],
                input_artifacts=["PaperArtifact"],
                output_artifacts=["SlideArtifact"],
            )
        )

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.id] = capability

    def get(self, capability_id: str) -> Optional[Capability]:
        return self._capabilities.get(capability_id)

    def list(self) -> list[str]:
        return list(self._capabilities.keys())

    def find_for_task(
        self,
        task: str,
        required_output_artifacts: Optional[list[str]] = None,
    ) -> list[Capability]:
        """根据 task 文本和期望输出 artifacts 选择匹配的 capability。

        匹配逻辑（按优先级）：
        1. 若 required_output_artifacts 包含 SlideArtifact，优先返回 paper_to_ppt
        2. 若包含 ReportArtifact，优先返回 paper_to_report
        3. 若 task 含 summary / overview，返回 paper_summarize
        4. 否则返回 paper_deep_analysis
        """
        task_lower = task.lower()
        results: list[Capability] = []

        if required_output_artifacts:
            if any("SlideArtifact" in a for a in required_output_artifacts):
                cap = self.get("paper_to_ppt")
                if cap:
                    results.append(cap)
            if any("ReportArtifact" in a for a in required_output_artifacts):
                cap = self.get("paper_to_report")
                if cap and cap not in results:
                    results.append(cap)

        if any(k in task_lower for k in ("summarize", "summary", "overview", "brief")):
            cap = self.get("paper_summarize")
            if cap and cap not in results:
                results.append(cap)

        if not results:
            cap = self.get("paper_deep_analysis")
            if cap:
                results.append(cap)

        return results

    def resolve_nodes(self, capability: Capability, node_registry: Optional["NodeRegistry"] = None) -> list[str]:
        """将 capability 的 required_nodes 展开为节点 id 列表。

        如果 required_nodes 中包含 capability id（如 paper_deep_analysis），
        则递归解析其对应 capability 的节点。
        """
        from paperwise.orchestration.registries import NODE_REGISTRY
        nodes: list[str] = []
        registry = node_registry or NODE_REGISTRY
        for node_ref in capability.required_nodes:
            if self.get(node_ref):
                # node_ref 是子 capability
                sub_cap = self.get(node_ref)
                nodes.extend(self.resolve_nodes(sub_cap, registry))
            elif registry.get(node_ref):
                nodes.append(node_ref)
            else:
                # 未知节点，保留但不做处理
                nodes.append(node_ref)
        return nodes


class NodeRegistry:
    """Registry of all executable nodes in the system."""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeSpec] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            NodeSpec(
                id="parse_pdf",
                category="input",
                name="PDF Parser",
                description="Parse a PDF paper into text, figures, tables and metadata.",
                input_schema={"pdf_path": "str"},
                output_schema={"paper": "PaperArtifact"},
                required_capabilities=["pdf_parsing"],
                output_path="metadata.json",
            )
        )
        self.register(
            NodeSpec(
                id="extract_text",
                category="extraction",
                name="Text Extractor",
                description="Extract structured text sections from a parsed paper.",
                input_schema={"paper": "PaperArtifact"},
                output_schema={"sections": "SectionArtifact[]"},
                required_capabilities=["pdf_parsing"],
                output_path="sections.json",
            )
        )
        self.register(
            NodeSpec(
                id="problem_analysis",
                category="research",
                name="Problem Analysis",
                description="Analyze the problem statement and motivation of the paper.",
                input_schema={"paper": "PaperArtifact", "sections": "SectionArtifact[]"},
                output_schema={"problem": "str"},
                required_capabilities=["long_context"],
                output_path="facts.json",
                max_steps=12,
            )
        )
        self.register(
            NodeSpec(
                id="method_analysis",
                category="research",
                name="Method Analysis",
                description="Analyze the methodology and key ideas of the paper.",
                input_schema={"paper": "PaperArtifact", "sections": "SectionArtifact[]"},
                output_schema={"method": "MethodArtifact"},
                required_capabilities=["long_context"],
                output_path="facts.json",
                max_steps=12,
            )
        )
        self.register(
            NodeSpec(
                id="experiment_analysis",
                category="research",
                name="Experiment Analysis",
                description="Analyze experiments, metrics and results.",
                input_schema={"paper": "PaperArtifact", "method": "MethodArtifact"},
                output_schema={"experiment": "dict"},
                required_capabilities=["long_context"],
                output_path="facts.json",
                max_steps=12,
            )
        )
        self.register(
            NodeSpec(
                id="related_work_analysis",
                category="research",
                name="Related Work Analysis",
                description="Identify and summarize related work referenced by the paper.",
                input_schema={"paper": "PaperArtifact"},
                output_schema={"related_work": "list"},
                required_capabilities=["retrieval"],
                output_path="facts.json",
                max_steps=10,
            )
        )
        self.register(
            NodeSpec(
                id="synthesis",
                category="reasoning",
                name="Synthesis",
                description="Synthesize problem, method and experiments into coherent analysis.",
                input_schema={
                    "problem": "str",
                    "method": "MethodArtifact",
                    "experiment": "dict",
                },
                output_schema={"synthesis": "str"},
                required_capabilities=["long_context"],
                output_path="facts.json",
                max_steps=10,
            )
        )
        self.register(
            NodeSpec(
                id="evidence_verification",
                category="reasoning",
                name="Evidence Verification",
                description="Verify claims against the paper text with citations.",
                input_schema={"claims": "ClaimArtifact[]", "paper": "PaperArtifact"},
                output_schema={"verified_claims": "ClaimArtifact[]"},
                required_capabilities=["long_context"],
                output_path="verified.json",
                max_steps=15,
            )
        )
        self.register(
            NodeSpec(
                id="report_outline",
                category="generation",
                name="Report Outline Generator",
                description="Generate the outline for the analysis report.",
                input_schema={"synthesis": "str"},
                output_schema={"outline": "dict"},
                required_capabilities=["generation"],
                output_path="report/outline.json",
                max_steps=10,
            )
        )
        self.register(
            NodeSpec(
                id="report_section",
                category="generation",
                name="Report Section Writer",
                description="Write individual sections of the analysis report.",
                input_schema={"outline": "dict", "facts": "dict"},
                output_schema={"sections": "dict[str, Path]"},
                required_capabilities=["generation"],
                output_path="report/sections",
                max_steps=15,
            )
        )
        self.register(
            NodeSpec(
                id="report_assemble",
                category="generation",
                name="Report Assembler",
                description="Assemble sections into the final Markdown report.",
                input_schema={"sections": "dict[str, Path]"},
                output_schema={"report": "ReportArtifact"},
                required_capabilities=["generation"],
                output_path="report/report.md",
                max_steps=10,
            )
        )
        self.register(
            NodeSpec(
                id="ppt_outline",
                category="generation",
                name="PPT Outline Generator",
                description="Generate the outline for the academic slides.",
                input_schema={"synthesis": "str"},
                output_schema={"outline": "dict"},
                required_capabilities=["generation"],
                output_path="ppt/outline.json",
                max_steps=10,
            )
        )
        self.register(
            NodeSpec(
                id="ppt_assemble",
                category="generation",
                name="PPT Assembler",
                description="Assemble slides into the final PPTX file.",
                input_schema={"slides": "SlideArtifact[]"},
                output_schema={"presentation": "SlideArtifact[]"},
                required_capabilities=["generation"],
                output_path="slides.pptx",
                max_steps=15,
            )
        )
        self.register(
            NodeSpec(
                id="critic",
                category="verification",
                name="Critic / Reviewer",
                description="Adversarially review artifacts for hallucinations and omissions.",
                input_schema={"report": "ReportArtifact", "paper": "PaperArtifact"},
                output_schema={"critic_result": "CriticResult"},
                required_capabilities=["long_context"],
                output_path="review/findings.json",
                max_steps=25,
                verification_policy=VerificationPolicy(required=True, output_exists_check=True),
            )
        )
        self.register(
            NodeSpec(
                id="revision",
                category="generation",
                name="Revision Writer",
                description="Revise artifacts based on critic findings.",
                input_schema={"report": "ReportArtifact", "critic_result": "CriticResult"},
                output_schema={"report": "ReportArtifact"},
                required_capabilities=["generation"],
                output_path="report/report.md",
                max_steps=35,
            )
        )

    def register(self, node: NodeSpec) -> None:
        self._nodes[node.id] = node

    def get(self, node_id: str) -> Optional[NodeSpec]:
        return self._nodes.get(node_id)

    def list(self) -> list[str]:
        return list(self._nodes.keys())

    def by_category(self, category: str) -> list[NodeSpec]:
        return [n for n in self._nodes.values() if n.category == category]

    def select_by_category(self, category: str) -> list[NodeSpec]:
        """Alias for by_category with clearer intent."""
        return self.by_category(category)

    def filter_by_capabilities(self, capability_ids: list[str]) -> list[NodeSpec]:
        """返回 required_capabilities 与给定 capability ids 有交集的节点。"""
        cap_set = set(capability_ids)
        return [
            n for n in self._nodes.values()
            if cap_set.intersection(n.required_capabilities or [])
        ]

    def filter_by_output_artifact(self, artifact_type: str) -> list[NodeSpec]:
        """返回 output_schema 值包含指定 artifact 类型的节点（简单字符串匹配）。"""
        return [
            n for n in self._nodes.values()
            if artifact_type in str(n.output_schema)
        ]


class WorkflowRegistry:
    """Registry of pre-defined workflow templates."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowTemplate] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            WorkflowTemplate(
                id="paper_analysis",
                name="Paper Analysis",
                description="Deep analysis of a single academic paper.",
                trigger_intents=["analyze paper", "deep analysis", "paper analysis"],
                base_dag=[
                    {"id": "parse_pdf", "depends_on": []},
                    {"id": "extract_text", "depends_on": ["parse_pdf"]},
                    {"id": "problem_analysis", "depends_on": ["extract_text"], "parallel_group": "research"},
                    {"id": "method_analysis", "depends_on": ["extract_text"], "parallel_group": "research"},
                    {"id": "experiment_analysis", "depends_on": ["method_analysis"], "parallel_group": "research"},
                    {"id": "related_work_analysis", "depends_on": ["extract_text"], "parallel_group": "research"},
                    {"id": "synthesis", "depends_on": ["problem_analysis", "method_analysis", "experiment_analysis", "related_work_analysis"]},
                    {"id": "evidence_verification", "depends_on": ["synthesis"], "condition": "requires_verification"},
                ],
                default_artifacts=["PaperArtifact", "MethodArtifact"],
                dynamic_expandable=True,
            )
        )
        self.register(
            WorkflowTemplate(
                id="paper_to_report",
                name="Paper to Report",
                description="Generate a structured Markdown report from a paper.",
                trigger_intents=["generate report", "write report", "report"],
                base_dag=[
                    {"id": "paper_analysis", "depends_on": [], "sub_workflow": True},
                    {"id": "report_outline", "depends_on": ["paper_analysis"]},
                    {"id": "report_section", "depends_on": ["report_outline"]},
                    {"id": "report_assemble", "depends_on": ["report_section"]},
                    {"id": "critic", "depends_on": ["report_assemble"]},
                    {"id": "revision", "depends_on": ["critic"], "condition": "critic_has_issues"},
                ],
                default_artifacts=["ReportArtifact"],
                dynamic_expandable=True,
            )
        )
        self.register(
            WorkflowTemplate(
                id="paper_to_ppt",
                name="Paper to PPT",
                description="Generate academic slides from a paper.",
                trigger_intents=["generate ppt", "generate slides", "ppt", "presentation"],
                base_dag=[
                    {"id": "paper_analysis", "depends_on": [], "sub_workflow": True},
                    {"id": "ppt_outline", "depends_on": ["paper_analysis"]},
                    {"id": "ppt_assemble", "depends_on": ["ppt_outline"]},
                    {"id": "critic", "depends_on": ["ppt_assemble"]},
                    {"id": "revision", "depends_on": ["critic"], "condition": "critic_has_issues"},
                ],
                default_artifacts=["SlideArtifact"],
                dynamic_expandable=True,
            )
        )

    def register(self, workflow: WorkflowTemplate) -> None:
        self._workflows[workflow.id] = workflow

    def get(self, workflow_id: str) -> Optional[WorkflowTemplate]:
        return self._workflows.get(workflow_id)

    def list(self) -> list[str]:
        return list(self._workflows.keys())

    def select(self, task_route: Any) -> Optional[WorkflowTemplate]:
        """Pick a workflow template for a given task route.

        优先使用 task_route.workflow；若未匹配，则根据 task 文本和意图打分。
        """
        if task_route and getattr(task_route, "workflow", None):
            wf = self.get(task_route.workflow)
            if wf:
                return wf

        task_text = ""
        if task_route:
            task_text = getattr(task_route, "task_text", "") or ""
        task_lower = task_text.lower()

        best: Optional[WorkflowTemplate] = None
        best_score = -1
        for wf in self._workflows.values():
            score = 0
            for intent in wf.trigger_intents:
                if intent in task_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best = wf
        return best if best_score > 0 else None


class ArtifactRegistry:
    """Registry of artifact types and their schemas."""

    ARTIFACT_TYPES: dict[str, type] = {
        "Artifact": Artifact,
        "PaperArtifact": PaperArtifact,
        "SectionArtifact": SectionArtifact,
        "ClaimArtifact": ClaimArtifact,
        "MethodArtifact": MethodArtifact,
        "ReportArtifact": ReportArtifact,
        "SlideArtifact": SlideArtifact,
    }

    @classmethod
    def get_schema(cls, artifact_type: str) -> Optional[type]:
        return cls.ARTIFACT_TYPES.get(artifact_type)


# Global singletons
CAPABILITY_REGISTRY = CapabilityRegistry()
NODE_REGISTRY = NodeRegistry()
WORKFLOW_REGISTRY = WorkflowRegistry()
ARTIFACT_REGISTRY = ArtifactRegistry()
