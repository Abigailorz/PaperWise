"""Tests for learning integration in OrchestratorMemoryAdapter (P3)."""

from paperwise.core.plan import Plan
from paperwise.learning.strategy_library import Strategy
from paperwise.learning.signals import SignalType
from paperwise.orchestration.memory_adapter import OrchestratorMemoryAdapter


def _adapter(tmp_path) -> OrchestratorMemoryAdapter:
    return OrchestratorMemoryAdapter(workspace=tmp_path / "ws", user_id="test")


def _base_plan() -> Plan:
    plan = Plan()
    plan.add("Read paper and extract facts", task_id="read_paper")
    plan.add("Analyze methodology", task_id="analyze_method", depends_on=["read_paper"])
    return plan


def test_learn_procedure_actually_persists_pattern(tmp_path):
    """回归测试：旧实现传了不存在的 signature 参数，TypeError 被静默吞掉。"""
    adapter = _adapter(tmp_path)
    adapter.learn_procedure("analysis", _base_plan(), success=True)
    assert len(adapter.procedural_memory.patterns) == 1

    pattern = next(iter(adapter.procedural_memory.patterns.values()))
    assert pattern.task_type == "analysis"
    assert pattern.preferred_steps == ["read_paper", "analyze_method"]
    assert pattern.context_signature.get("plan_signature") == "read_paper|analyze_method"


def test_learn_from_review_populates_strategy_library(tmp_path):
    adapter = _adapter(tmp_path)
    signals = adapter.learn_from_review(
        "analysis",
        {"verdict": "REJECT", "critical": 1, "major": 1, "minor": 0},
    )

    assert any(s.signal_type == SignalType.HALLUCINATION for s in signals)
    names = {s.name for s in adapter.strategy_library.all()}
    assert "enforce-citations" in names
    assert "ensure-review" in names


def test_learn_from_review_clean_pass_creates_no_strategy(tmp_path):
    adapter = _adapter(tmp_path)
    signals = adapter.learn_from_review(
        "analysis",
        {"verdict": "PASS", "critical": 0, "major": 0, "minor": 0},
    )
    assert [s.signal_type for s in signals] == [SignalType.SUCCESS]
    assert adapter.strategy_library.count() == 0


def test_apply_strategies_to_plan_inserts_hinted_node(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.strategy_library.add_or_update(Strategy(
        task_type="analysis", name="verify-numerics",
        plan_hints=["verify_data"], success_rate=0.9, use_count=2,
    ))

    plan = adapter.apply_strategies_to_plan(_base_plan(), "analysis")
    ids = [t.id for t in plan.tasks]
    assert "verify_data" in ids
    verify = next(t for t in plan.tasks if t.id == "verify_data")
    assert verify.depends_on == ["read_paper"]  # 依赖必须指向已存在节点


def test_apply_strategies_to_plan_is_conservative(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.strategy_library.add_or_update(Strategy(
        task_type="analysis", name="s",
        # unknown_node 不在白名单 -> 忽略；analyze_method 已存在 -> 不重复
        plan_hints=["unknown_node", "analyze_method"],
        success_rate=0.9, use_count=1,
    ))

    plan = adapter.apply_strategies_to_plan(_base_plan(), "analysis")
    ids = [t.id for t in plan.tasks]
    assert "unknown_node" not in ids
    assert ids.count("analyze_method") == 1
    # 拓扑合法性保持
    from paperwise.orchestration.dynamic_planner import DynamicDAGPlanner
    assert DynamicDAGPlanner.is_topologically_valid(plan)
