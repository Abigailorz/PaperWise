#!/usr/bin/env python3
"""Model swap experiment for PaperWise evaluation.

Runs the same evaluation scenarios with different models to determine whether
bottlenecks are in the model or in the Harness, following Chapter 6 of
"深入理解 AI Agent".

Usage:
    python 测评/scripts/run_model_swap.py --paper feature3dgs_2312.03203 --k 3 \
        --models deepseek-v4-flash claude-sonnet-4-20250514 gpt-4o
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.evaluation.stats import summarize_runs, is_significant


async def run_model_on_paper(
    model: str,
    paper_id: str,
    k: int,
    config_name: str,
    scenario: int | None,
) -> dict:
    """Run a single model on a paper."""
    sys.path.insert(0, str(PROJECT / "测评" / "scripts"))
    from run_real_evaluation import run_part_b

    print(f"\n  [{model}] {paper_id} (k={k})...")
    result = await run_part_b(
        paper_id, k, scenario, model=model, config_name=config_name
    )
    result["stats"] = summarize_runs(result.get("runs", []), k=k)
    return result


async def run_model_swap(
    models: list[str],
    papers: list[str],
    k: int,
    config_name: str,
    scenario: int | None,
) -> dict:
    """Run model swap experiment."""
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": config_name,
        "k": k,
        "models": models,
        "papers": {},
    }

    for paper_id in papers:
        print(f"\n{'='*60}")
        print(f"Paper: {paper_id}")
        print(f"{'='*60}")

        paper_results = {}
        for model in models:
            result = await run_model_on_paper(
                model, paper_id, k, config_name, scenario
            )
            paper_results[model] = result

        # Pairwise comparisons
        model_names = list(paper_results.keys())
        comparisons = {}
        for i, m1 in enumerate(model_names):
            for m2 in model_names[i + 1:]:
                r1 = paper_results[m1]
                r2 = paper_results[m2]
                key = f"{m1} vs {m2}"
                comparisons[key] = {
                    "success_rate_diff": r1.get("success_rate", 0) - r2.get("success_rate", 0),
                    "avg_steps_diff": r1.get("avg_steps", 0) - r2.get("avg_steps", 0),
                    "avg_tokens_diff": r1.get("avg_tokens", 0) - r2.get("avg_tokens", 0),
                    "is_significant": is_significant(
                        r1.get("success_rate", 0), r1.get("total_runs", 1),
                        r2.get("success_rate", 0), r2.get("total_runs", 1),
                    ),
                }

        paper_results["_comparisons"] = comparisons
        report["papers"][paper_id] = paper_results

    return report


def write_markdown_report(report: dict, out_path: Path) -> None:
    lines = [
        "# PaperWise Model Swap Experiment Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Config: {report['config']}",
        f"k: {report['k']}",
        "",
        "## Models Compared",
        "",
        "| Model |",
        "|-------|",
    ]
    for m in report["models"]:
        lines.append(f"| {m} |")
    lines.append("")

    for paper_id, paper_data in report["papers"].items():
        lines.append(f"## Paper: {paper_id}")
        lines.append("")
        lines.append("| Model | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration |")
        lines.append("|-------|--------------|--------|--------|-----------|------------|--------------|")
        for model, result in paper_data.items():
            if model.startswith("_"):
                continue
            lines.append(
                f"| {model} | {result.get('success_rate', 0):.1%} | "
                f"{result.get('pass_at_k', 0):.1%} | {result.get('pass_consecutive_k', 0):.1%} | "
                f"{result.get('avg_steps', 0):.1f} | {result.get('avg_tokens', 0):.0f} | "
                f"{result.get('avg_duration', 0):.1f}s |"
            )
        lines.append("")

        comps = paper_data.get("_comparisons", {})
        if comps:
            lines.append("### Pairwise Differences")
            lines.append("")
            lines.append("| Comparison | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ | Significant |")
            lines.append("|------------|----------------|-------------|--------------|-------------|")
            for key, comp in comps.items():
                sig = "Yes" if comp["is_significant"] else "No"
                lines.append(
                    f"| {key} | {comp['success_rate_diff']:+.1%} | "
                    f"{comp['avg_steps_diff']:+.1f} | {comp['avg_tokens_diff']:+.0f} | {sig} |"
                )
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


async def main():
    ap = argparse.ArgumentParser(description="PaperWise model swap experiment")
    ap.add_argument("--paper", default="feature3dgs_2312.03203",
                    help="Paper id or 'all'")
    ap.add_argument("--k", type=int, default=3, help="Repeat count per scenario")
    ap.add_argument("--models", nargs="+",
                    default=["deepseek-v4-flash"],
                    help="Models to compare (e.g., deepseek-v4-flash claude-sonnet-4-20250514 gpt-4o)")
    ap.add_argument("--config", default="full",
                    choices=["full", "no-plan", "no-budget", "no-judge", "no-memory", "baseline"])
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

    report = await run_model_swap(
        args.models, papers, args.k, args.config, args.scenario
    )

    out_dir = PROJECT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"model_swap_{int(time.time())}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out_dir / f"model_swap_{int(time.time())}.md"
    write_markdown_report(report, md_path)

    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
