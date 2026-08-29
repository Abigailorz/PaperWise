"""Tests for the persistent strategy library."""

from paperwise.learning.signals import LearningSignal, SignalType
from paperwise.learning.strategy_library import Strategy, StrategyLibrary


def _library(tmp_path) -> StrategyLibrary:
    return StrategyLibrary(tmp_path / "strategies")


def test_add_and_select_strategy(tmp_path):
    lib = _library(tmp_path)
    lib.add_or_update(Strategy(
        task_type="analysis", name="verify-numerics",
        plan_hints=["verify_data"], success_rate=0.9, use_count=4,
    ))
    lib.add_or_update(Strategy(
        task_type="analysis", name="weak",
        plan_hints=[], success_rate=0.2, use_count=1,
    ))

    selected = lib.select("analysis", min_success_rate=0.5)
    assert [s.name for s in selected] == ["verify-numerics"]


def test_add_or_update_merges_same_task_and_name(tmp_path):
    lib = _library(tmp_path)
    first = lib.add_or_update(Strategy(
        task_type="analysis", name="s", plan_hints=["verify_data"],
    ))
    second = lib.add_or_update(Strategy(
        task_type="analysis", name="s", plan_hints=["expand_evidence"],
    ))
    assert first.strategy_id == second.strategy_id
    assert lib.count() == 1
    merged = lib.get(first.strategy_id)
    assert set(merged.plan_hints) == {"verify_data", "expand_evidence"}


def test_record_outcome_rolling_average(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(task_type="analysis", name="s", success_rate=1.0, use_count=1))
    lib.record_outcome(strat.strategy_id, success=False)

    updated = lib.get(strat.strategy_id)
    assert updated.use_count == 2
    assert abs(updated.success_rate - 0.5) < 1e-9


def test_record_outcome_unknown_id_returns_none(tmp_path):
    lib = _library(tmp_path)
    assert lib.record_outcome("missing", success=True) is None


def test_select_respects_task_type_and_limit(tmp_path):
    lib = _library(tmp_path)
    for i in range(5):
        lib.add_or_update(Strategy(
            task_type="analysis", name=f"s{i}", success_rate=0.9, use_count=i,
        ))
    lib.add_or_update(Strategy(task_type="ppt", name="other", success_rate=0.9))

    selected = lib.select("analysis", limit=3)
    assert len(selected) == 3
    assert all(s.task_type == "analysis" for s in selected)
    # use_count 高的排前面（同分）
    assert selected[0].use_count >= selected[-1].use_count


def test_learn_from_signals_only_major_or_critical(tmp_path):
    lib = _library(tmp_path)
    signals = [
        LearningSignal(signal_type=SignalType.HALLUCINATION, source="reviewer", severity="critical"),
        LearningSignal(signal_type=SignalType.QUALITY_GAP, source="reviewer", severity="major"),
        LearningSignal(signal_type=SignalType.QUALITY_GAP, source="reviewer", severity="minor"),
        LearningSignal(signal_type=SignalType.SUCCESS, source="reviewer", severity="info"),
    ]
    created = lib.learn_from_signals("analysis", signals)

    names = {s.name for s in created}
    assert "enforce-citations" in names
    assert "ensure-review" in names
    # minor / info 不生成策略
    assert lib.count() == 2


def test_persistence_roundtrip(tmp_path):
    lib = _library(tmp_path)
    strat = lib.add_or_update(Strategy(
        task_type="analysis", name="verify-numerics",
        plan_hints=["verify_data"], success_rate=0.8, use_count=3,
    ))

    reloaded = _library(tmp_path)
    restored = reloaded.get(strat.strategy_id)
    assert restored is not None
    assert restored.plan_hints == ["verify_data"]
    assert restored.use_count == 3
    assert abs(restored.success_rate - 0.8) < 1e-9
