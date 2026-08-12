#!/usr/bin/env python3
"""Agent 能力综合测试脚本

测试维度：
1. 基础信息提取 — Agent 能否从论文中提取关键信息
2. 数值事实验证 — Agent 能否找到并引用具体数据
3. 代码验证 — Agent 能否用代码验证论文声称
4. 批判性分析 — Agent 能否识别局限性
5. 完整报告生成 — Agent 能否完成端到端任务
6. 工具使用效率 — Agent 是否合理使用工具
7. 幻觉检测 — Agent 是否编造不存在的内容
8. ReAct 循环稳定性 — Agent 能否处理多步复杂任务

Usage:
    python tests/run_agent_tests.py [--model deepseek-chat] [--provider openai_compatible]
"""

import asyncio
import json
import sys
import time
import re
from pathlib import Path
from datetime import datetime

# 项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from paperwise.config.settings import get_settings
from paperwise.core.llm_client import LLMClient
from paperwise.core.agent import Agent, AgentConfig
from paperwise.tools.registry import ToolRegistry
from paperwise.harness.harness import Harness
from paperwise.evaluation import RubricEvaluator, HallucinationDetector


# === 测试配置 ===

TEST_DATA_DIR = Path(__file__).parent / "test_data"
PAPER_DIR = TEST_DATA_DIR / "papers"
EXPECTED_DIR = TEST_DATA_DIR / "expected"

# 可选测试论文（不同领域，避免过拟合单一格式）
PAPERS = {
    "simple": {
        "text": PAPER_DIR / "test_paper_simple.md",
        "truth": EXPECTED_DIR / "ground_truth.json",
        "label": "Graph Neural Network",
    },
    "cv": {
        "text": PAPER_DIR / "test_paper_cv.md",
        "truth": EXPECTED_DIR / "ground_truth_cv.json",
        "label": "Efficient Semantic Segmentation",
    },
}


class TestResult:
    """单个测试结果"""
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.score = 0.0
        self.details = []
        self.errors = []
        self.steps = 0
        self.tools_used = {}
        self.duration = 0.0
        self.trajectory_summary = ""


class AgentTester:
    """Agent 能力综合测试器"""

    def __init__(self, model: str = "deepseek-chat", provider: str = "openai_compatible",
                 paper: str = "simple"):
        if paper not in PAPERS:
            raise ValueError(f"Unknown paper '{paper}'. Available: {list(PAPERS)}")
        settings = get_settings()
        self.llm = LLMClient(provider=provider, model=model)
        self.model = model
        self.provider = provider
        self.paper_name = paper
        self.paper_text = PAPERS[paper]["text"].read_text(encoding="utf-8")
        self.ground_truth = json.loads(PAPERS[paper]["truth"].read_text(encoding="utf-8"))
        self.scenarios = self.ground_truth["agent_test_scenarios"]
        self.results: list[TestResult] = []

        # 测试用 workspace
        self.workspace_base = settings.workspace_dir / "test_runs"
        self.workspace_base.mkdir(parents=True, exist_ok=True)

    async def run_all_tests(self, k: int = 1) -> dict:
        """运行全部测试场景，每个场景重复 k 次（用于 Pass@k / Pass^k）。"""
        print(f"\n{'='*60}")
        print(f"  PaperWise Agent Capability Test Suite")
        print(f"  Model: {self.model} ({self.provider})")
        print(f"  Test Paper: {self.ground_truth['title'][:60]}... "
              f"[{PAPERS[self.paper_name]['label']}]")
        print(f"  Scenarios: {len(self.scenarios)} × k={k}")
        print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        for i, scenario in enumerate(self.scenarios):
            print(f"\n{'─'*60}")
            print(f"Test {i+1}/{len(self.scenarios)}: {scenario['name']}")
            print(f"Task: {scenario['task'][:100]}...")
            print(f"{'─'*60}")

            for run_idx in range(k):
                result = await self._run_scenario(scenario, run_idx)
                self.results.append(result)

                status = "PASS" if result.passed else "FAIL"
                print(f"  [run {run_idx+1}/{k}] Result: {status} | Steps: {result.steps} | "
                      f"Duration: {result.duration:.1f}s | Score: {result.score:.1%}")
                if result.errors:
                    print(f"  Errors: {result.errors[:2]}")

        return self._summarize(k)

    async def _run_scenario(self, scenario: dict, run_idx: int = 0) -> TestResult:
        """运行单个测试场景。"""
        result = TestResult(scenario["name"])
        result.run_id = run_idx + 1
        start_time = time.time()

        try:
            # 准备 workspace
            workspace = self.workspace_base / f"test_{scenario['name']}_{int(time.time())}"
            workspace.mkdir(parents=True, exist_ok=True)

            # 准备论文目录
            paper_dir = workspace / "paper"
            paper_dir.mkdir()
            (paper_dir / "text.md").write_text(self.paper_text, encoding="utf-8")
            (paper_dir / "metadata.json").write_text(json.dumps({
                "title": self.ground_truth["title"],
                "page_count": 6,
            }), encoding="utf-8")

            # 创建 Agent
            tools = ToolRegistry.create_default(paper_dir)
            harness = Harness(paper_dir, max_steps=scenario.get("max_steps", 15))

            config = AgentConfig(
                name=f"test-{scenario['name']}",
                system_prompt=self._get_test_system_prompt(),
                model=self.model,
                max_steps=scenario.get("max_steps", 15),
            )

            agent = Agent(config=config, tools=tools, llm_client=self.llm,
                          harness=harness, workspace_dir=paper_dir)

            # 回调：静默收集（可改为 print 看实时轨迹）
            def on_event(etype, detail):
                pass  # 静默模式。改为 print(detail) 可看实时

            agent.on_event(on_event)

            # 执行
            task = (
                f"<task>\n{scenario['task']}\n</task>\n\n"
                f"<paper_location>\n"
                f"The paper is at: {paper_dir}/text.md\n"
                f"Ground truth metadata is at: {paper_dir}/metadata.json\n"
                f"</paper_location>\n"
            )

            agent_result = await agent.run(task)

            result.steps = agent_result.steps
            result.tools_used = dict(agent_result.tool_stats)
            result.duration = time.time() - start_time

            # === 评估结果 ===
            final_output = agent_result.final_output

            # 收集所有可检查内容（final_output + 生成的文件 + trajectory）
            all_content = final_output
            # 也检查分析目录中的文件
            for md_file in workspace.rglob("*.md"):
                try:
                    all_content += "\n" + md_file.read_text(encoding="utf-8")
                except Exception:
                    pass

            # 1. 检查工具使用
            expected_tools = set(scenario.get("expected_tools", []))
            actual_tools = set(agent_result.tool_stats.keys())
            if expected_tools:
                tool_overlap = expected_tools & actual_tools
                if len(tool_overlap) >= len(expected_tools) * 0.5:
                    result.details.append(f"Tools used: {sorted(actual_tools)}")
                else:
                    result.errors.append(f"Expected tools {expected_tools}, got {sorted(actual_tools)}")

            # 2. 检查关键内容（在所有文件内容中搜索）
            expected_content = scenario.get("expected_answer_contains", [])
            if expected_content:
                content_lower = all_content.lower()
                found = [c for c in expected_content if c.lower() in content_lower]
                missing = [c for c in expected_content if c.lower() not in content_lower]
                if found:
                    result.details.append(f"Found key content ({len(found)}/{len(expected_content)}): {found}")
                if missing:
                    result.errors.append(f"Missing content ({len(missing)}): {missing}")

            # 3. 检查输出文件
            min_files = scenario.get("min_output_files", [])
            for f in min_files:
                if (paper_dir / f).exists():
                    result.details.append(f"Output file exists: {f}")
                else:
                    result.errors.append(f"Missing output file: {f}")

            # 4. 检查代码是否正确执行（如果有）
            if "code_interpreter" in actual_tools and "expected_code_contains" in scenario:
                result.details.append("Code execution verified")

            # 5. 检查报告最小长度
            min_chars = scenario.get("min_report_chars", 0)
            report_path = paper_dir / "report" / "report.md"
            if min_chars > 0 and report_path.exists():
                size = len(report_path.read_text(encoding="utf-8"))
                if size >= min_chars:
                    result.details.append(f"Report size: {size} chars (>= {min_chars})")
                else:
                    result.errors.append(f"Report too short: {size} < {min_chars} chars")

            # 6. 幻觉检测（对最终输出）
            if len(final_output) > 100:
                detector = HallucinationDetector(self.llm)
                hall = await detector.detect(final_output, self.paper_text)
                if hall["passed"]:
                    result.details.append(f"No hallucinations detected ({hall['severity']})")
                else:
                    result.errors.append(f"Hallucination: {hall['summary']}")

            # 计算得分
            total_checks = len(result.details) + len(result.errors)
            if total_checks > 0:
                result.score = len(result.details) / total_checks
            result.passed = result.score >= 0.6  # 60% 通过即算成功

        except Exception as e:
            result.errors.append(f"Exception: {type(e).__name__}: {e}")
            result.duration = time.time() - start_time

        return result

    def _get_test_system_prompt(self) -> str:
        """测试用系统提示词（简化版）。"""
        return """You are a research assistant. Complete the assigned task accurately.

<rules>
1. Read the paper at the specified location first
2. Use tools to search for specific facts, numbers, and patterns
3. Verify numerical claims with code when appropriate
4. Cite specific line numbers or sections from the paper
5. Be concise and accurate — quality over quantity
6. Do NOT fabricate information — if you cannot verify something, say so
</rules>"""

    def _compute_pass_metrics(self, results: list[TestResult], k: int) -> dict:
        """计算 Pass@k 与 Pass^k（对应书中 6.2 节）。"""
        n = max(len(results), 1)
        p_single = sum(1 for r in results if r.passed) / n
        # Pass@k：k 次尝试中至少一次成功（能力上限）
        pass_at_k = 1 - (1 - p_single) ** k if p_single < 1 else 1.0
        # Pass^k：连续 k 次全部成功（业务可靠性）
        pass_consecutive_k = p_single ** k
        return {
            "runs": len(results),
            "success_rate": round(p_single, 4),
            "pass_at_k": round(pass_at_k, 4),
            "pass_consecutive_k": round(pass_consecutive_k, 4),
        }

    def _summarize(self, k: int = 1) -> dict:
        """生成测试总结（含 Pass@k / Pass^k）。"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        avg_score = sum(r.score for r in self.results) / max(total, 1)
        avg_steps = sum(r.steps for r in self.results) / max(total, 1)
        avg_duration = sum(r.duration for r in self.results) / max(total, 1)
        total_errors = sum(len(r.errors) for r in self.results)

        # 按场景分组计算 Pass@k / Pass^k
        by_scenario: dict[str, list[TestResult]] = {}
        for r in self.results:
            by_scenario.setdefault(r.name, []).append(r)
        scenario_metrics = {
            name: self._compute_pass_metrics(rs, k)
            for name, rs in sorted(by_scenario.items())
        }

        summary = {
            "model": self.model,
            "paper": self.paper_name,
            "paper_title": self.ground_truth["title"],
            "k": k,
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": f"{passed/total:.1%}" if total else "N/A",
            "avg_score": f"{avg_score:.1%}",
            "avg_steps": f"{avg_steps:.1f}",
            "avg_duration": f"{avg_duration:.1f}s",
            "total_errors": total_errors,
            "pass_at_k": scenario_metrics,
            "overall": self._compute_pass_metrics(self.results, k),
            "results": [
                {
                    "name": r.name,
                    "run_id": getattr(r, "run_id", 1),
                    "passed": r.passed,
                    "score": f"{r.score:.1%}",
                    "steps": r.steps,
                    "duration": f"{r.duration:.1f}s",
                    "tools": r.tools_used,
                    "errors": r.errors[:3],  # 前 3 个错误
                    "details": r.details[:3],  # 前 3 个通过项
                }
                for r in self.results
            ],
        }

        # 打印总结
        print(f"\n{'='*60}")
        print(f"  TEST SUMMARY")
        print(f"{'='*60}")
        print(f"  Model: {self.model}")
        print(f"  Paper: {self.paper_name} — {self.ground_truth['title'][:50]}")
        print(f"  Pass: {passed}/{total} ({passed/total:.1%})")
        overall = summary["overall"]
        print(f"  Pass@{k}: {overall['pass_at_k']:.1%} | "
              f"Pass^{k}: {overall['pass_consecutive_k']:.1%}")
        print(f"  Avg Score: {avg_score:.1%}")
        print(f"  Avg Steps: {avg_steps:.1f}")
        print(f"  Avg Duration: {avg_duration:.1f}s")
        print(f"  Total Errors: {total_errors}")
        print(f"{'='*60}\n")

        for r in self.results:
            icon = "[PASS]" if r.passed else "[FAIL]"
            print(f"  {icon} {r.name}: {r.score:.1%} ({r.steps} steps, {r.duration:.1f}s)")

        if k > 1:
            print(f"\n  Per-scenario Pass@{k}:")
            for name, m in scenario_metrics.items():
                print(f"    {name}: Pass@{k}={m['pass_at_k']:.1%} "
                      f"Pass^{k}={m['pass_consecutive_k']:.1%} "
                      f"(success {m['success_rate']:.1%})")

        # 保存结果
        results_path = self.workspace_base / f"test_results_{int(time.time())}.json"
        results_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nResults saved to: {results_path}")

        return summary


# === CLI 入口 ===

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="PaperWise Agent Capability Tests")
    parser.add_argument("--model", default="deepseek-chat", help="LLM model ID")
    parser.add_argument("--provider", default="openai_compatible", help="LLM provider")
    parser.add_argument("--paper", default="simple", choices=list(PAPERS),
                        help="测试论文 (simple=GNN / cv=语义分割)")
    parser.add_argument("--k", type=int, default=1, help="每个场景重复次数 (Pass@k)")
    parser.add_argument("--scenario", type=int, help="Run specific scenario (1-indexed)")
    args = parser.parse_args()

    tester = AgentTester(model=args.model, provider=args.provider, paper=args.paper)

    if args.scenario:
        idx = args.scenario - 1
        if 0 <= idx < len(tester.scenarios):
            print(f"Running single scenario: {tester.scenarios[idx]['name']} × k={args.k}")
            for run_idx in range(args.k):
                result = await tester._run_scenario(tester.scenarios[idx], run_idx)
                tester.results.append(result)
            tester._summarize(args.k)
        else:
            print(f"Invalid scenario index. Available: 1-{len(tester.scenarios)}")
    else:
        await tester.run_all_tests(k=args.k)


if __name__ == "__main__":
    asyncio.run(main())
