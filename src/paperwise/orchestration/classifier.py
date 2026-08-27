"""Task complexity classifier: rule-based + lightweight LLM fallback.

Deterministically split user tasks into simple (single-agent) or complex
(DAG multi-agent) execution paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from paperwise.orchestration.types import TaskRoute, TaskType, TaskComplexity

class TaskClassifier:
    """Classify a user task as simple or complex.

    Uses fast regex rules first; only ambiguous cases trigger an LLM call.
    """

    # Strong complex indicators (file artifacts, verification, critique, synthesis)
    _COMPLEX_KEYWORDS = [
        r"\breport\b",
        r"\bppt\b",
        r"\bpptx\b",
        r"\bslides?\b",
        r"\bverify\b",
        r"\bvalidat(?:e|ion)\b",
        r"\bnumerical\b",
        r"\bcritical\b",
        r"\bweakness(?:es)?\b",
        r"\bdeep critique\b",
        r"\bcompare\b",
        r"\bcomparison\b",
        r"\bsurvey\b",
        r"\bcomprehensive\b",
        r"\b完整分析\b",
        r"\b生成报告\b",
        r"\b生成PPT\b",
        r"\b验证\b",
        r"\b数值\b",
        r"\b批判\b",
        r"\b不足\b",
        r"\b缺点\b",
        r"\b对比\b",
        r"\b综述\b",
    ]

    # Simple indicators: short factual lookups
    _SIMPLE_KEYWORDS = [
        r"\bwhat\b",
        r"\bwho\b",
        r"\bwhen\b",
        r"\bwhere\b",
        r"\bwhich\b",
        r"\bhow many\b",
        r"\bhow much\b",
        r"\bhow does\b",
        r"\bhow do\b",
        r"\bwhat is the\b",
        r"\bwhat are the\b",
        r"\bwhat does\b",
        r"\bexact numbers?\b",
        r"\b(this paper|the paper) reports?\b",
        r"\breported\b",
        r"\b贡献\b",
        r"\b作者\b",
        r"\b数据集\b",
    ]

    # Tokens that suggest multi-step / artifact generation even without strong keywords
    _MEDIUM_COMPLEX_HINTS = [
        r"\banalyze\b",
        r"\b分析\b",
        r"\bexplain\b",
        r"\b解释\b",
        r"\bmethod\b",
        r"\bexperiment\b",
        r"\b方法\b",
        r"\b实验\b",
    ]

    def __init__(self, llm_client=None, workspace: Optional[Path] = None):
        self.llm = llm_client
        self.workspace = workspace
        self._cache: dict[str, TaskRoute] = {}
        if workspace:
            self._load_cache()

    @staticmethod
    def _is_paper_metric_lookup(text: str) -> bool:
        """Return True for questions about a metric/value reported by the paper."""
        return bool(
            re.search(
                r"\b(what|which|how)\b.*\b(this paper|the paper)\b.*\breport(s|ed)?\b",
                text,
                re.IGNORECASE,
            )
            or re.search(r"\b(what|which|how)\b.*\breported\b", text, re.IGNORECASE)
        )

    async def classify(self, task: str, use_cache: bool = True) -> TaskRoute:
        """Classify a task and return a structured TaskRoute.

        The route carries the execution path (simple/complex), confidence,
        and an ``escalate_on_failure`` flag for ambiguous tasks that should
        first try a fast single-agent path but fall back to the DAG if it
        fails or produces low-confidence output.
        """
        key = self._cache_key(task)
        if use_cache and key in self._cache:
            return self._cache[key]

        text = task.lower().strip()

        # Pre-rule: short questions about what the paper reports are simple factual lookups.
        is_metric_lookup = self._is_paper_metric_lookup(text)
        if is_metric_lookup and len(text.split()) <= 45:
            result = TaskRoute(
                task_type=TaskType.SIMPLE_QA,
                complexity=TaskComplexity.SIMPLE,
                requires_tools=False,
                requires_planning=False,
                requires_artifacts=False,
                workflow="simple_qa",
                confidence="high",
                escalate_on_failure=False,
            )
            self._maybe_cache(key, result, use_cache)
            return result

        complex_hits = sum(1 for p in self._COMPLEX_KEYWORDS if re.search(p, text, re.IGNORECASE))
        simple_hits = sum(1 for p in self._SIMPLE_KEYWORDS if re.search(p, text, re.IGNORECASE))
        medium_hits = sum(1 for p in self._MEDIUM_COMPLEX_HINTS if re.search(p, text, re.IGNORECASE))

        # Strong complex rule: any complex keyword -> complex (high confidence)
        if complex_hits > 0:
            result = TaskRoute(
                task_type=TaskType.RESEARCH,
                complexity=TaskComplexity.COMPLEX,
                requires_tools=True,
                requires_planning=True,
                requires_artifacts=True,
                workflow="paper_analysis",
                confidence="high",
                escalate_on_failure=False,
                reason=f"matched {complex_hits} complex keyword(s)",
            )
        # Strong simple rule: simple keyword and no complex keyword AND short
        elif simple_hits > 0 and complex_hits == 0 and len(text.split()) <= 45:
            result = TaskRoute(
                task_type=TaskType.SIMPLE_QA,
                complexity=TaskComplexity.SIMPLE,
                requires_tools=False,
                requires_planning=False,
                requires_artifacts=False,
                workflow="simple_qa",
                confidence="high",
                escalate_on_failure=False,
                reason="factual lookup with simple keyword and no artifact keyword",
            )
        # Number-focused factual questions without complex artifact keywords are simple
        elif (
            re.search(r"\b\d+(\.\d+)?%?\b", text)
            and complex_hits == 0
            and len(text.split()) <= 50
        ):
            result = TaskRoute(
                task_type=TaskType.SIMPLE_QA,
                complexity=TaskComplexity.SIMPLE,
                requires_tools=False,
                requires_planning=False,
                requires_artifacts=False,
                workflow="simple_qa",
                confidence="high",
                escalate_on_failure=True,
                reason="number-focused factual lookup without complex artifact keywords; escalate if number not found",
            )
        # Ambiguous: contains analysis/explain/method but no artifact keyword -> simple with DAG fallback
        elif medium_hits > 0 and complex_hits == 0 and len(text.split()) <= 25:
            route = TaskRoute(
                task_type=TaskType.SIMPLE_QA,
                complexity=TaskComplexity.SIMPLE,
                requires_tools=False,
                requires_planning=False,
                requires_artifacts=False,
                workflow="simple_qa",
                confidence="medium",
                escalate_on_failure=True,
                reason="analysis keyword without artifact keyword; try simple first, fallback to DAG",
            )
            self._maybe_cache(key, route, use_cache)
            return route
        # Default conservative: complex
        else:
            result = TaskRoute(
                task_type=TaskType.RESEARCH,
                complexity=TaskComplexity.COMPLEX,
                requires_tools=True,
                requires_planning=True,
                requires_artifacts=True,
                workflow="paper_analysis",
                confidence="low",
                escalate_on_failure=False,
                reason="no clear simple signal; default to complex",
            )

        self._maybe_cache(key, result, use_cache)
        return result

    def _maybe_cache(self, key: str, route: TaskRoute, use_cache: bool) -> None:
        if use_cache:
            self._cache[key] = route
            self._save_cache()

    async def _llm_classify(self, task: str) -> TaskRoute:
        """Lightweight LLM fallback for ambiguous tasks."""
        if not self.llm:
            return TaskRoute(
                task_type=TaskType.SIMPLE_QA,
                complexity=TaskComplexity.SIMPLE,
                requires_tools=False,
                requires_planning=False,
                requires_artifacts=False,
                workflow="simple_qa",
                confidence="medium",
                escalate_on_failure=True,
                reason="ambiguous but no LLM available; try simple first, fallback to DAG",
            )

        prompt = (
            "Classify the following user request as 'simple' or 'complex'.\n"
            "simple = a single factual lookup that only needs reading/searching.\n"
            "complex = requires writing a report, verifying numbers, critical analysis, "
            "multi-step synthesis, or generating files.\n\n"
            f"Request: {task}\n\n"
            'Reply with JSON only: {"complexity": "simple|complex", "reason": "..."}'
        )
        try:
            resp = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=60,
            )
            content = (resp.content or "").strip()
            match = re.search(r'\{.*?\}', content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                level = data.get("complexity", "complex").lower()
                reason = data.get("reason", "LLM fallback")
                if level == "simple":
                    return TaskRoute(
                        task_type=TaskType.SIMPLE_QA,
                        complexity=TaskComplexity.SIMPLE,
                        requires_tools=False,
                        requires_planning=False,
                        requires_artifacts=False,
                        workflow="simple_qa",
                        confidence="medium",
                        escalate_on_failure=True,
                        reason=reason,
                    )
                return TaskRoute(
                    task_type=TaskType.RESEARCH,
                    complexity=TaskComplexity.COMPLEX,
                    requires_tools=True,
                    requires_planning=True,
                    requires_artifacts=True,
                    workflow="paper_analysis",
                    confidence="medium",
                    escalate_on_failure=False,
                    reason=reason,
                )
        except Exception:
            pass
        return TaskRoute(
            task_type=TaskType.RESEARCH,
            complexity=TaskComplexity.COMPLEX,
            requires_tools=True,
            requires_planning=True,
            requires_artifacts=True,
            workflow="paper_analysis",
            confidence="low",
            escalate_on_failure=False,
            reason="LLM fallback failed; default to complex",
        )

    def _cache_key(self, task: str) -> str:
        return hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]

    def _cache_path(self) -> Optional[Path]:
        if not self.workspace:
            return None
        return self.workspace / ".task_classifier_cache.json"

    def _load_cache(self) -> None:
        path = self._cache_path()
        if path and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self._cache[k] = TaskRoute(
                        task_type=TaskType(v.get("task_type", "research")),
                        complexity=TaskComplexity(v.get("complexity", "complex")),
                        requires_tools=v.get("requires_tools", False),
                        requires_planning=v.get("requires_planning", True),
                        requires_artifacts=v.get("requires_artifacts", True),
                        workflow=v.get("workflow", "paper_analysis"),
                        confidence=v.get("confidence", "low"),
                        escalate_on_failure=v.get("escalate_on_failure", False),
                    )
                    if "reason" in v:
                        self._cache[k].reason = v["reason"]
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        path = self._cache_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = {
                k: {
                    "task_type": v.task_type.value,
                    "complexity": v.complexity.value,
                    "requires_tools": v.requires_tools,
                    "requires_planning": v.requires_planning,
                    "requires_artifacts": v.requires_artifacts,
                    "workflow": v.workflow,
                    "confidence": v.confidence,
                    "escalate_on_failure": v.escalate_on_failure,
                    "reason": getattr(v, "reason", ""),
                }
                for k, v in self._cache.items()
            }
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
