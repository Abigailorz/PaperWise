"""P6 Phase B-F tests: Strategy lifecycle, HypothesisEngine, graph-driven planning."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from paperwise.learning.strategy_library import (
    Strategy,
    StrategyLibrary,
    StrategyLifecycle,
)
from paperwise.opportunity.hypothesis_engine import (
    ExperimentDesign,
    Hypothesis,
    HypothesisEngine,
)
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity


class TestStrategyLifecycle:
    def _library(self) -> StrategyLibrary:
        return StrategyLibrary(Path(tempfile.mkdtemp()), user_id="test")

    def test_initial_lifecycle_is_candidate(self):
        lib = self._library()
        strat = Strategy(task_type="analysis", name="test-strategy")
        lib.strategies[strat.strategy_id] = strat
        assert strat.lifecycle == StrategyLifecycle.CANDIDATE.value

    def test_promotion_candidate_to_experimental(self):
        lib = self._library()
        strat = Strategy(task_type="analysis", name="s", actual_gain=0.05)
        lib.strategies[strat.strategy_id] = strat
        result = lib.promote_strategy(strat.strategy_id, eval_report=strat)
        assert result.lifecycle == StrategyLifecycle.EXPERIMENTAL.value

    def test_promotion_requires_positive_gain(self):
        lib = self._library()
        strat = Strategy(task_type="analysis", name="s", actual_gain=-0.1)
        lib.strategies[strat.strategy_id] = strat
        result = lib.promote_strategy(strat.strategy_id, eval_report=strat)
        assert result.lifecycle == StrategyLifecycle.CANDIDATE.value

    def test_promotion_chain(self):
        lib = self._library()
        strat = Strategy(task_type="analysis", name="s", actual_gain=0.1)
        lib.strategies[strat.strategy_id] = strat
        lib.promote_strategy(strat.strategy_id, eval_report=strat)  # -> experimental
        lib.promote_strategy(strat.strategy_id, eval_report=strat)  # -> validated
        strat.use_count = 6
        strat.success_rate = 0.8
        lib.promote_strategy(strat.strategy_id, eval_report=strat)  # -> trusted
        assert strat.lifecycle == StrategyLifecycle.TRUSTED.value
        assert strat.is_trusted

    def test_demotion_to_deprecated(self):
        lib = self._library()
        strat = Strategy(task_type="analysis", name="s", lifecycle=StrategyLifecycle.VALIDATED.value)
        lib.strategies[strat.strategy_id] = strat
        lib.demote_strategy(strat.strategy_id)
        assert strat.lifecycle == StrategyLifecycle.DEPRECATED.value
        assert not strat.is_usable

    def test_select_excludes_deprecated(self):
        lib = self._library()
        good = Strategy(task_type="analysis", name="good", success_rate=0.8)
        bad = Strategy(task_type="analysis", name="bad", success_rate=0.8,
                       lifecycle=StrategyLifecycle.DEPRECATED.value)
        lib.strategies[good.strategy_id] = good
        lib.strategies[bad.strategy_id] = bad
        selected = lib.select("analysis")
        assert all(s.name != "bad" for s in selected)


class TestHypothesisEngine:
    def test_generate_from_complementarity(self):
        opp = ResearchOpportunity(
            type=OpportunityType.METHOD_COMPLEMENTARITY,
            title="CLIP + SAM",
            description="Combining CLIP and SAM",
            confidence=0.8,
            related_entities=["CLIP", "SAM"],
        )
        hyps = HypothesisEngine().generate([opp])
        assert len(hyps) == 1
        assert "CLIP" in hyps[0].statement
        assert "SAM" in hyps[0].statement
        assert hyps[0].experiment is not None
        assert set(hyps[0].experiment.baseline_methods) == {"CLIP", "SAM"}

    def test_ignores_non_complementarity(self):
        opp = ResearchOpportunity(
            type=OpportunityType.KNOWLEDGE_GAP,
            title="gap",
            description="gap",
            confidence=0.8,
        )
        hyps = HypothesisEngine().generate([opp])
        assert len(hyps) == 0

    def test_max_hypotheses(self):
        opps = [
            ResearchOpportunity(
                type=OpportunityType.METHOD_COMPLEMENTARITY,
                title=f"opp {i}",
                description="d",
                confidence=0.8,
                related_entities=[f"A{i}", f"B{i}"],
            )
            for i in range(5)
        ]
        hyps = HypothesisEngine().generate(opps, max_hypotheses=2)
        assert len(hyps) == 2

    def test_experiment_design_fields(self):
        opp = ResearchOpportunity(
            type=OpportunityType.METHOD_COMPLEMENTARITY,
            title="A+B",
            description="d",
            confidence=0.8,
            related_entities=["Alpha", "Beta"],
        )
        hyps = HypothesisEngine().generate([opp])
        exp = hyps[0].experiment
        assert exp.hypothesis_id == hyps[0].hypothesis_id
        assert "Alpha" in exp.proposed_combination
        assert "Beta" in exp.proposed_combination
        assert len(exp.metrics) > 0

    def test_serialization(self):
        hyp = Hypothesis(statement="test", rationale="r", confidence=0.7)
        hyp.experiment = ExperimentDesign(objective="obj")
        data = hyp.to_dict()
        restored = Hypothesis.from_dict(data)
        assert restored.statement == "test"
        assert restored.experiment.objective == "obj"
