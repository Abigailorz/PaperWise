"""Tier 2/3: Orchestration tests for complexity-aware task routing.

Covers:
- TaskClassifier simple/complex decisions (rule-based, no LLM).
- PaperDAGPlanner dependency correctness.
- SmartOrchestrator simple-path fallback with MockLLM.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

os.environ["PAPERWISE_ALLOW_HOMOGENEOUS_JUDGE"] = "1"

from paperwise.core.types import AgentConfig, ToolCall
from paperwise.core.llm_client import LLMResponse
from paperwise.orchestration.classifier import TaskClassifier, ComplexityLevel
from paperwise.orchestration.paper_dag import PaperDAGPlanner
from paperwise.orchestration.orchestrator import SmartOrchestrator
from paperwise.tools.registry import ToolRegistry
from paperwise.harness.harness import Harness
from tests.helpers.mock_llm import MockLLMClient


@pytest.fixture
def tmp_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def test_classify_simple_fact_lookup(tmp_workspace):
    clf = TaskClassifier()
    result = clf.classify("What is the main contribution of this paper?")
    assert result.level == ComplexityLevel.SIMPLE
    assert result.confidence == "high"


def test_classify_complex_report(tmp_workspace):
    clf = TaskClassifier()
    result = clf.classify("Write a comprehensive analysis report with citations.")
    assert result.level == ComplexityLevel.COMPLEX
    assert result.confidence == "high"


def test_classify_complex_verify(tmp_workspace):
    clf = TaskClassifier()
    result = clf.classify("Verify the numerical claims in the experiments.")
    assert result.level == ComplexityLevel.COMPLEX


def test_classify_complex_critical(tmp_workspace):
    clf = TaskClassifier()
    result = clf.classify("What are the limitations and weaknesses?")
    assert result.level == ComplexityLevel.COMPLEX


def test_simple_plan_minimal(tmp_workspace):
    plan = PaperDAGPlanner.build_simple("What is the contribution?")
    assert [t.id for t in plan.tasks] == ["read_paper", "answer"]
    assert plan.get("answer").depends_on == ["read_paper"]


def test_complex_dag_dependencies(tmp_workspace):
    plan = PaperDAGPlanner.build("Write a report and verify the numbers.")
    ids = {t.id for t in plan.tasks}
    assert "read_paper" in ids
    assert "verify_data" in ids
    assert "generate_report" in ids
    assert "review_report" in ids
    assert "revise_report" in ids

    # verify_data and analyze_method depend on read_paper
    assert plan.get("verify_data").depends_on == ["read_paper"]
    # report depends on verify_data
    assert "verify_data" in plan.get("generate_report").depends_on
    # review depends on report
    assert plan.get("review_report").depends_on == ["generate_report"]


def test_smart_orchestrator_routes_simple(tmp_workspace):
    async def _inner():
        # Mock LLM returns a direct text answer after one read_file
        llm = MockLLMClient([
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="read_file", arguments={"path": "paper/text.md"}),
            ]),
            LLMResponse(content="The paper proposes EfficientGraph. final answer."),
        ])

        ws = tmp_workspace
        paper_dir = ws / "paper"
        paper_dir.mkdir(exist_ok=True)
        (paper_dir / "text.md").write_text(
            "# EfficientGraph\nAchieves 87.2% on Cora.", encoding="utf-8")

        orchestrator = SmartOrchestrator(
            llm_client=llm,
            workspace=ws,
            base_config=AgentConfig(
                name="test", system_prompt="You are a paper analyst.", max_steps=5),
        )
        result = await orchestrator.run(
            "What is the main contribution?", paper_dir=paper_dir)
        assert result.success
        assert "EfficientGraph" in result.final_output

    asyncio.run(_inner())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
