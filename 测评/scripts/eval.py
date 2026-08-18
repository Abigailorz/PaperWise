#!/usr/bin/env python3
"""PaperWise 统一四级评测入口。

对应 EVALUATION_FRAMEWORK.md 中的 Tier 1~4：
  Tier 1: 确定性安全/组件测试
  Tier 2: Mock-LLM Agent 控制逻辑测试
  Tier 3: 真实论文 Golden Dataset 能力测试
  Tier 4: 消融 / 横向对比
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def run_tier1() -> int:
    print("== Tier 1: deterministic safety/component tests ==")
    script = PROJECT / "测评" / "scripts" / "run_evaluation.py"
    return subprocess.run([PYTHON, str(script), "--part", "a"]).returncode


def run_tier2() -> int:
    print("== Tier 2: mock-LLM agent-loop integration tests ==")
    test_file = PROJECT / "tests" / "test_agent_loop.py"
    if not test_file.exists():
        print("  skipped: tests/test_agent_loop.py not found")
        return 0
    return subprocess.run([PYTHON, "-m", "pytest", str(test_file), "-v"]).returncode


def run_tier3(paper: str, k: int, scenario: int | None, model: str, config: str) -> int:
    print(f"== Tier 3: real-paper capability eval (paper={paper}, k={k}, config={config}) ==")
    script = PROJECT / "测评" / "scripts" / "run_real_evaluation.py"
    cmd = [PYTHON, str(script), "--part", "b", "--paper", paper, "--k", str(k), "--model", model, "--config", config]
    if scenario is not None:
        cmd.extend(["--scenario", str(scenario)])
    return subprocess.run(cmd).returncode


def run_tier4() -> int:
    print("== Tier 4: ablation study ==")
    script = PROJECT / "测评" / "scripts" / "run_ablation.py"
    return subprocess.run([PYTHON, str(script)]).returncode


def list_configs() -> None:
    from paperwise.evaluation.configs import ABLATON_CONFIGS
    print("Available evaluation configs:")
    for name, overrides in ABLATON_CONFIGS.items():
        print(f"  {name}: {overrides or 'full system'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="PaperWise unified evaluation harness")
    ap.add_argument("--tier", default="all",
                    choices=["1", "2", "3", "4", "all"],
                    help="Which tier to run")
    ap.add_argument("--paper", default="feature3dgs_2312.03203",
                    help="Paper id for Tier 3")
    ap.add_argument("--k", type=int, default=1, help="Repeat count for Tier 3")
    ap.add_argument("--scenario", type=int, default=None, help="Run only one scenario (1-6)")
    ap.add_argument("--model", default="deepseek-chat", help="LLM model for Tier 3")
    ap.add_argument("--config", default="full",
                    choices=["full", "no-plan", "no-budget", "no-judge", "no-memory", "baseline"],
                    help="Agent ablation config for Tier 3/4")
    ap.add_argument("--list-configs", action="store_true", help="Show available ablation configs")
    args = ap.parse_args()

    sys.path.insert(0, str(PROJECT / "src"))

    if args.list_configs:
        list_configs()
        return 0

    tiers = [args.tier] if args.tier != "all" else ["1", "2", "3", "4"]
    exit_codes = []
    for tier in tiers:
        if tier == "1":
            exit_codes.append(run_tier1())
        elif tier == "2":
            exit_codes.append(run_tier2())
        elif tier == "3":
            exit_codes.append(run_tier3(args.paper, args.k, args.scenario, args.model, args.config))
        elif tier == "4":
            exit_codes.append(run_tier4())
    return max(exit_codes)


if __name__ == "__main__":
    sys.exit(main())
