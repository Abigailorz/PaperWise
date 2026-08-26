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


class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


@dataclass
class TaskComplexity:
    """Classification result for a task."""

    level: ComplexityLevel
    confidence: str  # "high" | "medium" | "low"
    reason: str = ""

    @property
    def is_simple(self) -> bool:
        return self.level == ComplexityLevel.SIMPLE

    @property
    def is_complex(self) -> bool:
        return self.level == ComplexityLevel.COMPLEX


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
        r"\blimitation\b",
        r"\bweakness\b",
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
        self._cache: dict[str, TaskComplexity] = {}
        if workspace:
            self._load_cache()

    def classify(self, task: str, use_cache: bool = True) -> TaskComplexity:
        """Classify a task. Cached results are reused when use_cache=True."""
        key = self._cache_key(task)
        if use_cache and key in self._cache:
            return self._cache[key]

        text = task.lower().strip()

        complex_hits = sum(1 for p in self._COMPLEX_KEYWORDS if re.search(p, text, re.IGNORECASE))
        simple_hits = sum(1 for p in self._SIMPLE_KEYWORDS if re.search(p, text, re.IGNORECASE))
        medium_hits = sum(1 for p in self._MEDIUM_COMPLEX_HINTS if re.search(p, text, re.IGNORECASE))

        # Strong complex rule: any complex keyword -> complex (high confidence)
        if complex_hits > 0:
            result = TaskComplexity(
                level=ComplexityLevel.COMPLEX,
                confidence="high",
                reason=f"matched {complex_hits} complex keyword(s)",
            )
        # Strong simple rule: simple keyword and no complex keyword AND short
        elif simple_hits > 0 and len(text.split()) <= 25:
            result = TaskComplexity(
                level=ComplexityLevel.SIMPLE,
                confidence="high",
                reason="short factual lookup with simple keyword",
            )
        # Ambiguous: contains analysis/explain/method but no artifact keyword -> medium
        elif medium_hits > 0 and len(text.split()) <= 20:
            result = self._llm_classify(task)
        # Default conservative: complex
        else:
            result = TaskComplexity(
                level=ComplexityLevel.COMPLEX,
                confidence="low",
                reason="no clear simple signal; default to complex",
            )

        if use_cache:
            self._cache[key] = result
            self._save_cache()
        return result

    def _llm_classify(self, task: str) -> TaskComplexity:
        """Lightweight LLM fallback for ambiguous tasks."""
        if not self.llm:
            # No LLM available -> conservative complex
            return TaskComplexity(
                level=ComplexityLevel.COMPLEX,
                confidence="low",
                reason="ambiguous but no LLM available",
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
            resp = self.llm.chat(
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
                return TaskComplexity(
                    level=ComplexityLevel.SIMPLE if level == "simple" else ComplexityLevel.COMPLEX,
                    confidence="medium",
                    reason=reason,
                )
        except Exception:
            pass
        return TaskComplexity(
            level=ComplexityLevel.COMPLEX,
            confidence="low",
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
                    self._cache[k] = TaskComplexity(
                        level=ComplexityLevel(v.get("level", "complex")),
                        confidence=v.get("confidence", "low"),
                        reason=v.get("reason", ""),
                    )
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        path = self._cache_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = {
                k: {"level": v.level.value, "confidence": v.confidence, "reason": v.reason}
                for k, v in self._cache.items()
            }
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
