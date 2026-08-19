#!/usr/bin/env python3
"""Ablation study runner for PaperWise evaluation.

Runs all ablation configs (full / no-plan / no-budget / no-judge / no-memory / baseline)
on golden paper datasets and produces a comparison report with statistical significance.

Usage:
    python 测评/scripts/run_ablation_study.py --paper feature3dgs_2312.03203 --k 3
    python 测评/scripts/run_ablation_study.py --paper all --k 3
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.evaluation.stats import (
    summarize_runs, is_significant, paired_bootstrap,
    pass_at_k, pass_consecutive_k,
)
from paperwise.evaluation.configs import ABLATON_CONFIGS


async def run_config_on_paper(
    config_name: str,
    paper_id: str,
    k: int,
    model: str,
    scenario: int | None,
) -> dict:
    """Run a single ablation config on a paper."""
    # Import here to avoid circular imports and to reuse the updated evaluation logic
    sys.path.insert(0, str(PROJECT / "测评" / "scripts"))
    from run_real_evaluation import run_part_b, _load_golden

    golden = _load_golden(paper_id)
    paper_text = (PROJECT / "workspace" / "benchmarks" / "parsed" / paper_id / "text.md").read_text(encoding="utf-8")
    title = golden.get("title", paper_id)

    print(f"\n  [{config_name}] {paper_id} (k={k})...")

    result = await run_part_b(
        paper_id, k, scenario, model=model, config_name=config_name
    )

    # Add statistical summary
    result["stats"] = summarize_runs(result.get("runs", []), k=k)

    return result


async def run_ablation(
    papers: list[str],
    k: int,
    model: str,
    scenario: int | None,
) -> dict:
    """Run all ablation configs on the specified papers."""
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "k": k,
        "papers": {},
        "configs": list(ABLATON_CONFIGS.keys()),
    }

    for paper_id in papers:
        print(f"\n{'='*60}")
        print(f"Paper: {paper_id}")
        print(f"{'='*60}")

        paper_results = {}
        for config_name in ABLATON_CONFIGS:
            result = await run_config_on_paper(
                config_name, paper_id, k, model, scenario
            )
            paper_results[config_name] = result

        # Compute pairwise significance vs baseline
        baseline = paper_results.get("baseline", {})
        full = paper_results.get("full", {})

        comparisons = {}
        for config_name, result in paper_results.items():
            if config_name == "baseline":
                continue
            if not baseline or not result:
                continue
            comparisons[config_name] = {
                "success_rate_diff": result.get("success_rate", 0) - baseline.get("success_rate", 0),
                "avg_steps_diff": result.get("avg_steps", 0) - baseline.get("avg_steps", 0),
                "avg_tokens_diff": result.get("avg_tokens", 0) - baseline.get("avg_tokens", 0),
                "is_significant": is_significant(
                    result.get("success_rate", 0), result.get("total_runs", 1),
                    baseline.get("success_rate", 0), baseline.get("total_runs", 1),
                ),
            }

        paper_results["_comparisons"] = comparisons
        report["papers"][paper_id] = paper_results

    return report


def write_markdown_report(report: dict, out_path: Path) -> None:
    """Write a human-readable markdown report."""
    lines = [
        "# PaperWise Ablation Study Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Model: {report['model']}",
        f"k: {report['k']}",
        "",
        "## Configurations",
        "",
        "| Config | Description |",
        "|--------|-------------|",
        "| full | Plan + Budget + Judge + HierarchicalMemory |",
        "| no-plan | Remove explicit Plan |",
        "| no-budget | Remove budget-aware guidance |",
        "| no-judge | Remove Judge review |",
        "| no-memory | Remove HierarchicalMemory compression |",
        "| baseline | Basic ReAct without plan/budget/judge/memory |",
        "",
    ]

    for paper_id, paper_data in report["papers"].items():
        lines.append(f"## Paper: {paper_id}")
        lines.append("")

        # Summary table
        lines.append("| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |")
        lines.append("|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|")
        for config_name, result in paper_data.items():
            if config_name.startswith("_"):
                continue
            sr = result.get("success_rate", 0)
            pass_at = result.get("pass_at_k", 0)
            pass_con = result.get("pass_consecutive_k", 0)
            steps = result.get("avg_steps", 0)
            tokens = result.get("avg_tokens", 0)
            duration = result.get("avg_duration", 0)
            sig = "Yes" if paper_data.get("_comparisons", {}).get(config_name, {}).get("is_significant") else "No"
            lines.append(
                f"| {config_name} | {sr:.1%} | {pass_at:.1%} | {pass_con:.1%} | "
                f"{steps:.1f} | {tokens:.0f} | {duration:.1f}s | {sig} |"
            )
        lines.append("")

        # Comparisons vs baseline
        comps = paper_data.get("_comparisons", {})
        if comps:
            lines.append("### Differences vs Baseline")
            lines.append("")
            lines.append("| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |")
            lines.append("|--------|----------------|-------------|--------------|")
            for config_name, comp in comps.items():
                lines.append(
                    f"| {config_name} | {comp['success_rate_diff']:+.1%} | "
                    f"{comp['avg_steps_diff']:+.1f} | {comp['avg_tokens_diff']:+.0f} |"
                )
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


async def main():
    ap = argparse.ArgumentParser(description="PaperWise ablation study runner")
    ap.add_argument("--paper", default="feature3dgs_2312.03203",
                    help="Paper id or 'all'")
    ap.add_argument("--k", type=int, default=3, help="Repeat count per scenario")
    ap.add_argument("--model", default="deepseek-v4-flash", help="LLM model")
    ap.add_argument("--scenario", type=int, default=None, help="Run only one scenario (1-6)")
    args = ap.parse_args()

    all_papers = [
        "feature3dgs_2312.03203",
        "langsplat_2312.16084",
        "gaussaingrouping_2312.00732",
        "mipsplatting_2311.16493",
        "gaussianeditor_2311.14521",
    ]
    papers = all_papers if args.paper == "all" else [args.paper]

    report = await run_ablation(papers, args.k, args.model, args.scenario)

    out_dir = PROJECT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"ablation_study_{int(time.time())}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out_dir / f"ablation_study_{int(time.time())}.md"
    write_markdown_report(report, md_path)

    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
