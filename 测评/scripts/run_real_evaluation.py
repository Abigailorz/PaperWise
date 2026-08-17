#!/usr/bin/env python3
"""PaperWise real-paper evaluation (真实环境测评).

Part A: deterministic safety/component tests (no LLM).
Part B: LLM agent tests on real papers (3DGS / LangSplat / Feature 3DGS),
        driven by human-built golden datasets.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

EVAL_DIR = PROJECT / "workspace" / "benchmarks"
PARSED_DIR = EVAL_DIR / "parsed"
GOLDEN_DIR = EVAL_DIR / "golden"
OUT_DIR = PROJECT / "workspace" / "benchmarks"

# 让 Agent 运行时的 workspace 落到可写目录，避免污染桌面项目。
EVAL_WS = EVAL_DIR / "eval_workspace"
os.environ.setdefault("PAPERWISE_WORKSPACE", str(EVAL_WS))

from paperwise.config.settings import get_settings  # noqa: E402
from paperwise.core.llm_client import LLMClient  # noqa: E402

# 复用现有测评框架（确定性 Part A 与场景运行器）。
sys.path.insert(0, str(EVAL_DIR))
from run_evaluation import run_part_a, ScenarioResult, _collect_text, _run_one  # noqa: E402

PAPERS = {
    "3dgs_2308.04079": "golden_3dgs_2308.04079.json",
    "langsplat_2312.16084": "golden_langsplat_2312.16084.json",
    "feature3dgs_2312.03203": "golden_feature3dgs_2312.03203.json",
    "gaussaingrouping_2312.00732": "golden_gaussaingrouping_2312.00732.json",
}


def _load_golden(paper_id: str) -> dict:
    return json.loads((GOLDEN_DIR / PAPERS[paper_id]).read_text(encoding="utf-8"))


async def run_part_b(paper_id: str, k: int, only_scenario: int,
                     model: str = "deepseek-chat") -> dict:
    golden = _load_golden(paper_id)
    paper_text = (PARSED_DIR / paper_id / "text.md").read_text(encoding="utf-8")
    title = golden.get("title", paper_id)
    scenarios = golden.get("agent_test_scenarios", [])
    if only_scenario is not None:
        scenarios = [scenarios[only_scenario - 1]]

    llm = LLMClient(provider="deepseek", model=model)
    all_runs = []
    for sc in scenarios:
        for i in range(k):
            r = await _run_one(paper_text, title, sc, i, llm, model)
            all_runs.append(r)
            print(
                f"  [{sc['name']} run{i + 1}] {'PASS' if r.passed else 'FAIL'} "
                f"score={r.score:.0%} steps={r.steps} legal={r.legal_rate:.0%} "
                f"rubric={r.rubric:.2f} hall={r.hallucination.get('severity')} "
                f"{r.duration:.0f}s",
                flush=True,
            )

    by_name = {}
    for r in all_runs:
        by_name.setdefault(r.name, []).append(r)
    per_scenario = {}
    for name, rs in sorted(by_name.items()):
        p = sum(1 for r in rs if r.passed) / len(rs)
        per_scenario[name] = {
            "runs": len(rs),
            "success_rate": round(p, 4),
            "pass_at_k": round(1 - (1 - p) ** k, 4),
            "pass_consecutive_k": round(p ** k, 4),
            "avg_steps": round(sum(r.steps for r in rs) / len(rs), 1),
            "avg_duration": round(sum(r.duration for r in rs) / len(rs), 1),
            "avg_legal_rate": round(sum(r.legal_rate for r in rs) / len(rs), 4),
            "avg_rubric": round(sum(r.rubric for r in rs) / len(rs), 2),
        }

    n = len(all_runs)
    passed = sum(1 for r in all_runs if r.passed)
    p = passed / n if n else 0
    return {
        "paper_id": paper_id,
        "title": title,
        "model": model,
        "k": k,
        "total_runs": n,
        "passed": passed,
        "success_rate": round(p, 4),
        "pass_at_k": round(1 - (1 - p) ** k, 4),
        "pass_consecutive_k": round(p ** k, 4),
        "avg_steps": round(sum(r.steps for r in all_runs) / n, 1) if n else 0,
        "avg_duration": round(sum(r.duration for r in all_runs) / n, 1) if n else 0,
        "avg_legal_rate": round(sum(r.legal_rate for r in all_runs) / n, 4) if n else 0,
        "avg_tokens": round(sum(r.tokens_used for r in all_runs) / n) if n else 0,
        "per_scenario": per_scenario,
        "runs": [
            {
                "name": r.name, "passed": r.passed, "score": round(r.score, 4),
                "steps": r.steps, "duration": round(r.duration, 1),
                "tokens": r.tokens_used, "legal_rate": round(r.legal_rate, 4),
                "rubric": r.rubric, "hallucination": r.hallucination.get("severity"),
                "errors": r.errors[:4], "details": r.details[:4],
            }
            for r in all_runs
        ],
    }


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all", choices=["a", "b", "all"])
    ap.add_argument("--paper", default=None, choices=list(PAPERS))
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--scenario", type=int)
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "part_a": None,
        "part_b": None,
    }

    if args.part in ("a", "all"):
        print("== Part A: deterministic safety/component ==", flush=True)
        a = run_part_a()
        for r in a:
            print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['name']}", flush=True)
        report["part_a"] = {
            "total": len(a),
            "passed": sum(r["passed"] for r in a),
            "results": a,
        }

    if args.part in ("b", "all"):
        print("\n== Part B: LLM agent on real papers ==", flush=True)
        targets = [args.paper] if args.paper else list(PAPERS)
        results = []
        for pid in targets:
            print(f"\n--- paper: {pid} (k={args.k}) ---", flush=True)
            results.append(await run_part_b(pid, args.k, args.scenario, args.model))
        report["part_b"] = {
            "papers": results,
            "overall_success_rate": round(
                sum(r["passed"] for r in results) / max(1, sum(r["total_runs"] for r in results)), 4
            ),
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"real_eval_{int(time.time())}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写入 latest.json 指针
    latest = OUT_DIR / "latest_real_eval.json"
    latest.write_text(json.dumps({"latest": str(path.relative_to(PROJECT)),
                                    "report": report}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nsaved: {path}\nlatest: {latest}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
