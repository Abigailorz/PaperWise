"""可组合的评估 Grader 接口与实现。

对应 EVALUATION_FRAMEWORK.md 中的 grader 抽象：
- Code-based：文件、关键词、JSON、工具调用检查
- Model-based：Rubric 打分、幻觉检测
- Transcript：过程指标（steps、legal_rate、tool efficiency）
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperwise.core.types import AgentResult, ToolCall
from paperwise.evaluation.rubric import RubricEvaluator
from paperwise.evaluation.hallucination import HallucinationDetector


@dataclass
class GradeResult:
    """单次评分结果"""
    passed: bool = False
    score: float = 0.0
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class Grader(ABC):
    """Grader 抽象基类"""

    @abstractmethod
    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        """对 agent 输出进行评分。

        Args:
            output: agent 最终文本输出或报告路径/内容。
            context: 评测上下文，可包含 paper_text、agent_result、scenario 等。
        """
        ...


class CodeGrader(Grader):
    """基于规则的确定性 Grader。"""

    def __init__(
        self,
        expected_keywords: list[str] | None = None,
        forbidden_keywords: list[str] | None = None,
        required_files: list[str] | None = None,
        expected_tools: list[str] | None = None,
        min_tool_hits: int = 1,
        min_output_chars: int = 0,
        json_valid: bool = False,
    ):
        self.expected_keywords = expected_keywords or []
        self.forbidden_keywords = forbidden_keywords or []
        self.required_files = required_files or []
        self.expected_tools = expected_tools or []
        self.min_tool_hits = min_tool_hits
        self.min_output_chars = min_output_chars
        self.json_valid = json_valid

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        details: list[str] = []
        errors: list[str] = []
        score = 0.0
        total = 0

        # 关键词命中
        if self.expected_keywords:
            total += 1
            text = (output or "").lower()
            hits = [k for k in self.expected_keywords if k.lower() in text]
            if len(hits) >= max(1, len(self.expected_keywords) // 2):
                score += 1
                details.append(f"keywords {len(hits)}/{len(self.expected_keywords)}")
            else:
                errors.append(f"missing keywords: {set(self.expected_keywords) - set(hits)}")

        # 禁用词
        if self.forbidden_keywords:
            total += 1
            text = (output or "").lower()
            bad = [k for k in self.forbidden_keywords if k.lower() in text]
            if not bad:
                score += 1
                details.append("no forbidden keywords")
            else:
                errors.append(f"forbidden keywords: {bad}")

        # 文件检查
        workspace = context.get("workspace")
        if self.required_files:
            total += 1
            missing = []
            for f in self.required_files:
                path = Path(workspace) / f if workspace else Path(f)
                if path.exists():
                    details.append(f"file exists: {f}")
                else:
                    missing.append(f)
            if not missing:
                score += 1
            else:
                errors.append(f"missing files: {missing}")

        # 工具调用检查
        agent_result: AgentResult | None = context.get("agent_result")
        if self.expected_tools and agent_result is not None:
            total += 1
            tool_stats = getattr(agent_result, "tool_stats", {}) or {}
            hits = [t for t in self.expected_tools if tool_stats.get(t, 0) > 0]
            if len(hits) >= min(self.min_tool_hits, len(self.expected_tools)):
                score += 1
                details.append(f"tools {len(hits)}/{len(self.expected_tools)}")
            else:
                errors.append(f"expected tools {self.expected_tools}, got {dict(tool_stats)}")

        # 输出长度
        if self.min_output_chars:
            total += 1
            if len(output or "") >= self.min_output_chars:
                score += 1
                details.append(f"output length {len(output)} >= {self.min_output_chars}")
            else:
                errors.append(f"output too short: {len(output)} < {self.min_output_chars}")

        # JSON 有效性
        if self.json_valid:
            total += 1
            try:
                json.loads(output or "")
                score += 1
                details.append("valid json")
            except json.JSONDecodeError as e:
                errors.append(f"invalid json: {e}")

        final_score = score / max(total, 1)
        return GradeResult(
            passed=final_score >= 0.6 and not errors,
            score=final_score,
            details=details,
            errors=errors,
        )


class RubricGrader(Grader):
    """基于 LLM-as-a-Judge 的 Rubric 评分。"""

    def __init__(self, llm_client, dimensions: list[str] | None = None):
        self.evaluator = RubricEvaluator(llm_client)
        self.dimensions = dimensions

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        paper_text = context.get("paper_text", "")
        result = await self.evaluator.evaluate(output, paper_text)
        details = [f"{k}: {v}" for k, v in result.scores.items()]
        return GradeResult(
            passed=result.passed,
            score=result.overall_score / 4.0,
            details=details,
            raw={
                "scores": result.scores,
                "overall_score": result.overall_score,
                "passed": result.passed,
                "details": result.details,
            },
        )


class HallucinationGrader(Grader):
    """基于 LLM 的幻觉检测。"""

    def __init__(self, llm_client):
        self.detector = HallucinationDetector(llm_client)

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        paper_text = context.get("paper_text", "")
        det = await self.detector.detect(output, paper_text)
        severity = det.get("severity", "none")
        # "error" means detection failed technically, not a hallucination;
        # only fail on critical/major hallucination, not on detection errors.
        passed = severity not in ("critical", "major")
        return GradeResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            details=[f"severity={severity}"] if passed else [],
            errors=[det.get("summary", "")] if not passed else [],
            raw=det,
        )


class TranscriptMetrics(Grader):
    """从 agent 执行轨迹提取过程指标。"""

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        agent_result: AgentResult | None = context.get("agent_result")
        if agent_result is None:
            return GradeResult(passed=True, details=["agent_result missing"])

        tool_stats = getattr(agent_result, "tool_stats", {}) or {}
        total = sum(tool_stats.values()) if tool_stats else 0
        legal_tools = {"read_file", "grep", "write_file", "code_interpreter", "edit_file"}
        legal = sum(c for t, c in tool_stats.items() if t in legal_tools)
        legal_rate = legal / total if total else 1.0
        efficiency = legal / total if total else 0.0

        details = [
            f"steps={getattr(agent_result, 'steps', 0)}",
            f"legal_rate={legal_rate:.2%}",
            f"tool_efficiency={efficiency:.2%}",
        ]
        return GradeResult(
            passed=True,
            score=legal_rate,
            details=details,
            raw={
                "steps": getattr(agent_result, "steps", 0),
                "tool_stats": dict(tool_stats),
                "legal_rate": legal_rate,
                "tool_efficiency": efficiency,
            },
        )


class CompositeGrader(Grader):
    """组合多个 grader，按权重聚合分数。"""

    def __init__(self, graders: list[tuple[Grader, float]]):
        self.graders = graders

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        total_weight = sum(w for _, w in self.graders)
        total = 0.0
        passed = True
        details: list[str] = []
        errors: list[str] = []
        raw: dict[str, Any] = {}
        for grader, weight in self.graders:
            r = await grader.grade(output, context)
            total += r.score * weight
            passed = passed and r.passed
            details.extend([f"[{grader.__class__.__name__}] {d}" for d in r.details])
            errors.extend([f"[{grader.__class__.__name__}] {e}" for e in r.errors])
            raw[grader.__class__.__name__] = r.raw
        return GradeResult(
            passed=passed,
            score=total / max(total_weight, 1e-9),
            details=details,
            errors=errors,
            raw=raw,
        )


class CitationGrader(Grader):
    """Verify that factual claims cite the paper with valid line ranges."""

    def __init__(self, required_citations: int = 1):
        self.required_citations = required_citations

    async def grade(self, output: str, context: dict[str, Any]) -> GradeResult:
        workspace = context.get("workspace")
        paper_path = Path(workspace) / "paper" / "text.md" if workspace else None
        if not paper_path or not paper_path.exists():
            return GradeResult(passed=True, details=["paper text not available; citation check skipped"])
        paper_lines = paper_path.read_text(encoding="utf-8").splitlines()
        import re
        pattern = re.compile(r"\[source:\s*text\.md\s+L(\d+)(?:-L?(\d+))?\]", re.IGNORECASE)
        matches = list(pattern.finditer(output))
        if len(matches) < self.required_citations:
            return GradeResult(
                passed=False,
                score=0.0,
                errors=[f"Only {len(matches)} citations found (required {self.required_citations})"],
            )
        invalid = []
        for m in matches:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            if start < 1 or end > len(paper_lines) or end < start:
                invalid.append(f"L{start}-L{end}")
        if invalid:
            return GradeResult(
                passed=False,
                score=0.0,
                errors=[f"Invalid citation ranges: {invalid}"],
            )
        return GradeResult(
            passed=True,
            score=1.0,
            details=[f"{len(matches)} valid citations"],
        )
