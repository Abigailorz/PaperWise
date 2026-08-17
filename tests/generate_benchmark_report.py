#!/usr/bin/env python3
"""根据最新的 benchmark 结果生成 Markdown 报告。

用法：
    python tests/generate_benchmark_report.py

输出：workspace/benchmarks/report_YYYYMMDD_HHMMSS.md
"""

import json
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BENCH_DIR = PROJECT / "workspace" / "benchmarks"


def load_latest(name: str) -> dict:
    latest = BENCH_DIR / name
    if not latest.exists():
        return {}
    data = json.loads(latest.read_text(encoding="utf-8"))
    return data.get("report", data)


def fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, str):
        val = val.replace("%", "").strip()
    return f"{float(val):.1%}"


def fmt_num(val) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):.3f}"


def main():
    BENCH_DIR.mkdir(parents=True, exist_ok=True)

    agent = load_latest("latest_agent.json")
    rag = load_latest("latest_rag.json")
    real = load_latest("latest_real_eval.json")
    ablation = load_latest("latest_ablation.json")

    lines = []
    lines.append("# PaperWise 基准测评报告\n")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    # Agent 能力
    if agent:
        overall = agent.get("overall", {})
        lines.append("## Agent 能力测评\n")
        lines.append(f"- 单次成功率：{fmt_pct(overall.get('success_rate'))}\n")
        lines.append(f"- Pass@k：{fmt_pct(overall.get('pass_at_k'))}\n")
        lines.append(f"- Pass^k：{fmt_pct(overall.get('pass_consecutive_k'))}\n")
        lines.append(f"- 平均分数：{agent.get('avg_score', 'N/A')}\n")
        lines.append(f"- 平均步数：{agent.get('avg_steps', 'N/A')}\n")
        lines.append(f"- 平均耗时：{agent.get('avg_duration', 'N/A')}\n")
        lines.append(f"- 总错误数：{agent.get('total_errors', 'N/A')}\n")
        per = agent.get("pass_at_k", {})
        if per:
            lines.append("\n### 各场景\n")
            for name, m in per.items():
                lines.append(f"- **{name}**: success_rate={fmt_pct(m.get('success_rate'))}, "
                             f"Pass@k={fmt_pct(m.get('pass_at_k'))}, "
                             f"Pass^k={fmt_pct(m.get('pass_consecutive_k'))}\n")
        lines.append("\n")

    # RAG
    if rag:
        overall = rag.get("overall", {})
        lines.append("## RAG 检索测评\n")
        lines.append(f"- Recall@3：{fmt_pct(overall.get('recall_at_3'))} (目标 >= 70%)\n")
        lines.append(f"- Precision@3：{fmt_pct(overall.get('precision_at_3'))}\n")
        lines.append(f"- MRR：{fmt_num(overall.get('mrr'))}\n")
        lines.append(f"- 目标达成：{'PASS' if overall.get('target_met') else 'FAIL'}\n\n")
        lines.append("### 单篇论文\n")
        for p in rag.get("papers", []):
            lines.append(f"- **{p.get('paper_id')}**: Recall@3={fmt_pct(p.get('recall_at_3'))}, "
                         f"MRR={fmt_num(p.get('mrr'))}\n")
        lines.append("\n")

    # 真实论文测评
    if real:
        part_b = real.get("part_b", {})
        lines.append("## 真实论文测评\n")
        lines.append(f"- 总体成功率：{fmt_pct(part_b.get('overall_success_rate'))}\n\n")
        for p in part_b.get("papers", []):
            lines.append(f"- **{p.get('paper_id')}**: {p.get('total_runs')} runs, "
                         f"success_rate={fmt_pct(p.get('success_rate'))}\n")
        lines.append("\n")

    # 消融
    if ablation:
        safety = ablation.get("ablation_1_safety", {})
        lines.append("## 消融实验\n")
        lines.append(f"- 安全层拦截率：{fmt_pct(safety.get('block_rate'))}\n")
        lines.append(f"- 误报率：{fmt_pct(safety.get('false_positive_rate'))}\n\n")

    path = BENCH_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved: {path}")


if __name__ == "__main__":
    main()
