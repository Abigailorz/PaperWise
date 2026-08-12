"""Pass@k / Pass^k 评估框架

对应书中 6.2 节：
- Pass@k：k 次尝试中至少一次成功（技术奇观/能力上限）
- Pass^k：k 次尝试全部成功（业务可靠性）
- 过程指标：工具调用有效率、路径效率、幻觉率
"""

import json
import time
import asyncio
import math
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class EvalRun:
    """单次评估运行结果"""
    run_id: int
    success: bool
    final_output: str
    steps: int
    tool_stats: dict[str, int]
    duration: float
    rubric_scores: dict = field(default_factory=dict)
    hallucination_count: int = 0
    error: str = ""


@dataclass
class BenchmarkResult:
    """基准测试完整结果"""
    task_name: str
    k: int
    runs: list[EvalRun]
    pass_at_k: float        # 至少一次成功
    pass_consecutive_k: float  # 连续 k 次全部成功
    avg_steps: float
    avg_duration: float
    avg_rubric: float
    tool_efficiency: float   # 工具调用有效率
    hallucination_rate: float
    success_rate: float      # 单次成功率


class PassKEvaluator:
    """Pass@k / Pass^k 评估器。

    使用方式：
        evaluator = PassKEvaluator(k=5)
        result = await evaluator.evaluate(
            task_name="paper_analysis",
            run_fn=lambda: agent.run(task),
            rubric_fn=lambda output: evaluator.score(output, paper),
        )
    """

    def __init__(self, k: int = 5):
        self.k = k

    async def evaluate(
        self,
        task_name: str,
        run_fn,           # async () → AgentResult
        rubric_fn,        # async (output) → dict {score, hallucination_count}
    ) -> BenchmarkResult:
        """运行 k 次并计算 Pass@k 和 Pass^k。"""
        runs: list[EvalRun] = []

        for i in range(self.k):
            t0 = time.time()
            try:
                result = await run_fn()
                score = await rubric_fn(result.final_output)
                runs.append(EvalRun(
                    run_id=i + 1,
                    success=result.success,
                    final_output=result.final_output[:500],
                    steps=result.steps,
                    tool_stats=dict(result.tool_stats),
                    duration=time.time() - t0,
                    rubric_scores=score.get("scores", {}),
                    hallucination_count=score.get("hallucinations", 0),
                ))
            except Exception as e:
                runs.append(EvalRun(
                    run_id=i + 1, success=False,
                    final_output="", steps=0, tool_stats={},
                    duration=time.time() - t0, error=str(e),
                ))

        # 计算指标
        successes = [r for r in runs if r.success]
        p_single = len(successes) / max(self.k, 1)

        # Pass@k: 至少一次成功
        pass_at_k = 1 - (1 - p_single) ** self.k if p_single < 1 else 1.0

        # Pass^k: 连续全部成功
        pass_consecutive_k = p_single ** self.k

        # 平均值
        avg_steps = sum(r.steps for r in runs) / max(len(runs), 1)
        avg_duration = sum(r.duration for r in runs) / max(len(runs), 1)
        avg_rubric = (
            sum(sum(r.rubric_scores.values()) / max(len(r.rubric_scores), 1) for r in runs)
            / max(len(runs), 1)
        )
        hall_rate = sum(r.hallucination_count for r in runs) / max(len(runs), 1)

        # 工具效率
        valid_calls = sum(r.tool_stats.get("grep", 0) + r.tool_stats.get("read_file", 0)
                         for r in runs)
        total_calls = sum(sum(r.tool_stats.values()) for r in runs)
        tool_eff = valid_calls / max(total_calls, 1)

        return BenchmarkResult(
            task_name=task_name, k=self.k, runs=runs,
            pass_at_k=pass_at_k, pass_consecutive_k=pass_consecutive_k,
            avg_steps=avg_steps, avg_duration=avg_duration,
            avg_rubric=avg_rubric, tool_efficiency=tool_eff,
            hallucination_rate=hall_rate, success_rate=p_single,
        )

    def format_report(self, result: BenchmarkResult) -> str:
        """格式化评估报告。"""
        return (
            f"=== {result.task_name} (k={result.k}) ===\n"
            f"  Pass@{result.k}:  {result.pass_at_k:.1%}  (至少一次成功)\n"
            f"  Pass^{result.k}:  {result.pass_consecutive_k:.1%}  (连续全部成功)\n"
            f"  单次成功率:     {result.success_rate:.1%}\n"
            f"  平均步数:       {result.avg_steps:.1f}\n"
            f"  平均耗时:       {result.avg_duration:.1f}s\n"
            f"  Rubric 均分:    {result.avg_rubric:.2f}/4.0\n"
            f"  工具有效率:     {result.tool_efficiency:.1%}\n"
            f"  幻觉率:         {result.hallucination_rate:.2f}/次\n"
            f"{'  ✅ 达到生产级可靠性' if result.pass_consecutive_k >= 0.8 else '  ⚠️ 可靠性不足'}"
        )


class AblationTester:
    """消融实验框架 — 逐一关闭组件测量贡献。

    对应书中 6.10.1 节。
    """

    def __init__(self, base_config: dict):
        self.base = base_config
        self.results: dict[str, float] = {}

    async def run_ablation(
        self,
        components: dict[str, bool],  # {组件名: 是否启用}
        run_fn_factory,  # (config) → Agent
        eval_fn,         # (Agent) → float score
    ) -> dict[str, float]:
        """运行消融实验。每个组件单独开关，测量对 score 的影响。"""
        # Baseline: 全部启用
        baseline = await eval_fn(await run_fn_factory({**self.base, **components}))

        for comp_name in components:
            # 关闭一个组件
            ablated = {**components, comp_name: False}
            agent = await run_fn_factory({**self.base, **ablated})
            score = await eval_fn(agent)
            self.results[comp_name] = baseline - score

        return self.results

    def format_report(self) -> str:
        """格式化消融报告。"""
        sorted_components = sorted(self.results.items(), key=lambda x: x[1], reverse=True)
        lines = ["=== 消融实验结果 ==="]
        lines.append(f"{'组件':<30} {'贡献分数':>10}")
        lines.append("-" * 42)
        for name, impact in sorted_components:
            lines.append(f"{name:<30} {impact:>+10.4f}")
        return "\n".join(lines)
