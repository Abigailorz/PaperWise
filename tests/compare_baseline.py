#!/usr/bin/env python3
"""对比当前测评结果与基线，检测回归。

用法：
    python tests/compare_baseline.py \
        --current workspace/benchmarks/latest_agent.json \
        --baseline tests/baselines/agent_baseline.json \
        [--tolerance 0.05]
"""

import argparse
import json
import sys
from pathlib import Path


def load_metrics(path: Path) -> dict:
    """从 benchmark JSON 中提取可对比数值指标。

    支持结构：
      - {report: {overall: {...}, key: val, ...}}
      - {overall: {...}, key: val, ...}
      - 扁平字典
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if "report" in data:
        data = data["report"]

    metrics = {}
    sources = [data]
    if "overall" in data and isinstance(data["overall"], dict):
        sources.append(data["overall"])

    for src in sources:
        for k, v in src.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                metrics[k] = float(v)
            elif isinstance(v, str):
                s = v.replace("%", "").strip()
                try:
                    metrics[k] = float(s)
                except ValueError:
                    pass
    return metrics


def compare(current: dict, baseline: dict, tolerance: float) -> list[dict]:
    regressions = []
    for key, base_val in baseline.items():
        cur_val = current.get(key)
        if cur_val is None:
            regressions.append({"metric": key, "baseline": base_val,
                                "current": None, "delta": None,
                                "status": "missing"})
            continue
        delta = cur_val - base_val
        # 默认越大越好；错误/幻觉率越小越好
        is_lower_better = "hallucination" in key or "error" in key or "false_positive" in key
        if is_lower_better:
            failed = cur_val > base_val + tolerance
        else:
            failed = cur_val < base_val - tolerance
        regressions.append({
            "metric": key,
            "baseline": round(base_val, 4),
            "current": round(cur_val, 4),
            "delta": round(delta, 4),
            "status": "FAIL" if failed else "PASS",
        })
    return regressions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--tolerance", type=float, default=0.05)
    args = ap.parse_args()

    current = load_metrics(args.current)
    baseline = load_metrics(args.baseline)
    results = compare(current, baseline, args.tolerance)

    fails = [r for r in results if r["status"] == "FAIL"]
    print(f"Compared {len(results)} metrics, {len(fails)} regressions")
    for r in results:
        def fmt(v):
            return "N/A" if v is None else str(v)
        print(f"  [{r['status']}] {r['metric']}: baseline={fmt(r['baseline'])}, "
              f"current={fmt(r['current'])}, delta={fmt(r['delta'])}")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
