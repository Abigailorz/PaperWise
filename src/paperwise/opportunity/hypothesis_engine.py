"""P6 Phase F - Hypothesis Engine.

Converts MethodComplementarity opportunities into testable hypotheses and
experiment designs. This is the first step toward Research-native behavior:
the agent doesn't just report what exists, it proposes what could be tried.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


@dataclass
class ExperimentDesign:
    """A structured experiment proposal derived from a hypothesis."""

    experiment_id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:8]}")
    hypothesis_id: str = ""
    objective: str = ""
    baseline_methods: list[str] = field(default_factory=list)
    proposed_combination: str = ""
    metrics: list[str] = field(default_factory=lambda: ["accuracy", "efficiency"])
    datasets: list[str] = field(default_factory=list)
    success_criteria: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentDesign":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Hypothesis:
    """A testable research hypothesis derived from an opportunity."""

    hypothesis_id: str = field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:8]}")
    statement: str = ""
    rationale: str = ""
    confidence: float = 0.5
    source_opportunity: str = ""
    status: str = "proposed"  # proposed | testing | supported | refuted
    experiment: ExperimentDesign | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Hypothesis":
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if isinstance(kwargs.get("experiment"), dict):
            kwargs["experiment"] = ExperimentDesign.from_dict(kwargs["experiment"])
        return cls(**kwargs)


class HypothesisEngine:
    """Generate hypotheses and experiment designs from opportunities."""

    def generate(
        self,
        opportunities: list[ResearchOpportunity],
        max_hypotheses: int = 3,
    ) -> list[Hypothesis]:
        """Create hypotheses from complementarity opportunities.

        Deterministic: same opportunities -> same hypothesis structure.
        LLM only parameterizes the hypothesis text at execution time.
        """
        hypotheses: list[Hypothesis] = []
        for opp in opportunities:
            if opp.type != OpportunityType.METHOD_COMPLEMENTARITY:
                continue
            if len(hypotheses) >= max_hypotheses:
                break
            entities = opp.related_entities or ["method A", "method B"]
            method_a = entities[0] if len(entities) > 0 else "method A"
            method_b = entities[1] if len(entities) > 1 else "method B"
            hypothesis = Hypothesis(
                statement=(
                    f"Combining {method_a} and {method_b} may yield improved results "
                    f"over either method alone."
                ),
                rationale=f"{opp.title}: {opp.description[:200]}",
                confidence=opp.confidence,
                source_opportunity=opp.opportunity_id,
                status="proposed",
            )
            hypothesis.experiment = self._design_experiment(hypothesis, method_a, method_b)
            hypotheses.append(hypothesis)
        return hypotheses

    @staticmethod
    def _design_experiment(
        hypothesis: Hypothesis,
        method_a: str,
        method_b: str,
    ) -> ExperimentDesign:
        """Create a structured experiment for a complementarity hypothesis."""
        return ExperimentDesign(
            hypothesis_id=hypothesis.hypothesis_id,
            objective=f"Validate: {hypothesis.statement}",
            baseline_methods=[method_a, method_b],
            proposed_combination=f"{method_a} + {method_b}",
            metrics=["accuracy", "mIoU", "inference_time"],
            datasets=["benchmark_dataset"],
            success_criteria="Combined method outperforms both baselines on primary metric.",
        )
