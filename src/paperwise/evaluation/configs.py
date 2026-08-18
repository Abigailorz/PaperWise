"""Agent 配置变体，用于消融 / 横向对比。

对应 EVALUATION_FRAMEWORK.md 中的 ablation 设计。
"""

from dataclasses import replace

from paperwise.core.types import AgentConfig


ABLATON_CONFIGS: dict[str, dict] = {
    "full": {},
    "no-plan": {"enable_plan": False},
    "no-budget": {"enable_budget_note": False},
    "no-judge": {"enable_judge_review": False},
    "no-memory": {"enable_hierarchical_memory": False},
    "baseline": {
        "enable_plan": False,
        "enable_budget_note": False,
        "enable_judge_review": False,
        "enable_hierarchical_memory": False,
    },
}


def apply_config(base: AgentConfig, name: str) -> AgentConfig:
    """返回应用了 ablation 开关后的 AgentConfig 副本。"""
    if name not in ABLATON_CONFIGS:
        raise ValueError(f"Unknown eval config: {name}. Choose from {list(ABLATON_CONFIGS)}")
    return replace(base, **ABLATON_CONFIGS[name])


__all__ = ["ABLATON_CONFIGS", "apply_config"]
