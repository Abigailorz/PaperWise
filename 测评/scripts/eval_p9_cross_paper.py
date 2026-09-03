#!/usr/bin/env python3
"""P9 — Cross-paper research intelligence benchmark.

Runs the four P9 benchmark categories over a workspace of parsed papers:

  1. Retrieval coverage — cross-paper retrieval reaches every indexed paper
  2. Method comparison  — deterministic CrossPaperMethodComparisonRule output
  3. Contradiction      — deterministic CrossPaperContradictionRule output
  4. Complementarity    — deterministic CrossPaperComplementarityRule output

plus citation precision over retrieved snippets (P9.5 evidence benchmark).
No LLM is required; all rules are deterministic, so repeated runs on the
same workspace produce identical results.

Papers must be parsed under <workspace>/<paper_id>/text.md. Results are
persisted as JSON for regression tracking.

Usage:
    python 测评/scripts/eval_p9_cross_paper.py [--workspace workspace]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from paperwise.evidence.retriever import EvidenceRetriever
from paperwise.memory.knowledge_base import KnowledgeBase
from paperwise.memory.research_state import ResearchState
from paperwise.opportunity.rules import (
    CrossPaperComplementarityRule,
    CrossPaperContradictionRule,
    CrossPaperMethodComparisonRule,
)

DEFAULT_QUERIES = [
    "method accuracy improvement",
    "experiment results comparison",
    "limitations future work",
]
MIN_CLAIM_LEN = 40
MAX_CLAIMS_PER_PAPER = 10


def _sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"#{1,6}\s*", "", text)
    raw = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [s.strip() for s in raw if MIN_CLAIM_LEN <= len(s.strip()) <= 300]


def _state_for_paper(paper_id: str, text: str, all_paper_ids: list[str]) -> ResearchState:
    state = ResearchState(state_id=f"p9eval_{paper_id}", user_id="p9eval")
    state.current_paper = paper_id
    state.current_task = f"compare methods across {', '.join(all_paper_ids)}"
    state.related_papers = [p for p in all_paper_ids if p != paper_id]
    for i, sentence in enumerate(_sentences(text)[:MAX_CLAIMS_PER_PAPER]):
        node_id = f"method:{paper_id}:{i}" if i % 2 == 0 else f"claim:{paper_id}:{i}"
        state.add_finding_from_node(node_id=node_id, claim=sentence, evidence=sentence, confidence=0.8)
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="P9 cross-paper benchmark")
    ap.add_argument("--workspace", default=str(PROJECT / "workspace"))
    ap.add_argument("--output", default=None,
                    help="defaults to <workspace>/test_runs/p9_cross_paper_eval.json")
    ap.add_argument("--top-k", type=int, default=6)
    args = ap.parse_args()

    workspace = Path(args.workspace).resolve()
    output = Path(args.output) if args.output else workspace / "test_runs" / "p9_cross_paper_eval.json"
    papers = sorted(
        d for d in workspace.iterdir()
        if d.is_dir() and (d / "text.md").exists()
    ) if workspace.exists() else []

    result = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": str(workspace),
            "num_papers": len(papers),
            "papers": [p.name for p in papers],
            "top_k": args.top_k,
            "queries": DEFAULT_QUERIES,
        },
    }

    if len(papers) < 2:
        result["status"] = "insufficient_papers"
        result["detail"] = "Need at least 2 parsed papers (<workspace>/<paper_id>/text.md)."
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[P9 eval] {result['detail']} Wrote {output}")
        return 1

    kb_dir = workspace / ".p9_eval_kb"
    retriever = EvidenceRetriever(KnowledgeBase(kb_dir))
    for paper in papers:
        retriever.index_paper(paper)

    # Category 1: retrieval coverage + citation precision.
    papers_covered: set[str] = set()
    snippets_all = []
    for query in DEFAULT_QUERIES:
        pack = retriever.retrieve(query, scope="cross_paper", top_k=args.top_k)
        papers_covered.update(pack.papers_covered)
        snippets_all.extend(pack.snippets)
    citable = [
        s for s in snippets_all
        if (s.start_line and s.end_line) or s.location
    ]
    precision = len(citable) / len(snippets_all) if snippets_all else 0.0
    result["retrieval"] = {
        "papers_covered": sorted(papers_covered),
        "coverage_ratio": round(len(papers_covered) / len(papers), 3),
        "snippet_count": len(snippets_all),
    }
    result["citation_precision"] = {
        "citable": len(citable),
        "total": len(snippets_all),
        "precision": round(precision, 3),
    }

    # Categories 2-4: deterministic cross-paper rules, one state per paper.
    texts = {p.name: (p / "text.md").read_text(encoding="utf-8", errors="replace") for p in papers}
    paper_ids = [p.name for p in papers]
    rule_counts = {"method_comparison": [], "contradiction": [], "complementarity": []}
    for paper_id in paper_ids:
        state = _state_for_paper(paper_id, texts[paper_id], paper_ids)
        rule_counts["method_comparison"].extend(CrossPaperMethodComparisonRule().apply(state, None))
        rule_counts["contradiction"].extend(CrossPaperContradictionRule().apply(state, None))
        rule_counts["complementarity"].extend(CrossPaperComplementarityRule().apply(state, None))
    result["rules"] = {
        name: {
            "count": len(opps),
            "titles": [o.title for o in opps[:10]],
        }
        for name, opps in rule_counts.items()
    }

    result["status"] = "ok"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[P9 eval] papers: {len(papers)} | coverage: {result['retrieval']['coverage_ratio']}")
    print(f"[P9 eval] citation precision: {result['citation_precision']['precision']}")
    for name, data in result["rules"].items():
        print(f"[P9 eval] {name}: {data['count']}")
    print(f"[P9 eval] results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
