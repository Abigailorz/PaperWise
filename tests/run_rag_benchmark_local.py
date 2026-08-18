#!/usr/bin/env python3
"""PaperWise RAG 本地基准测试（不依赖 arXiv 下载）。

使用仓库自带的测试论文 markdown 文件验证基础 RAG 召回能力。
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paperwise.memory.knowledge_base import KnowledgeBase

PROJECT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT / "workspace" / "benchmarks"

PAPERS = {
    "test_paper_simple": (PROJECT / "tests" / "test_data" / "papers" / "test_paper_simple.md",
                          PROJECT / "tests" / "test_data" / "expected" / "ground_truth.json"),
    "test_paper_cv": (PROJECT / "tests" / "test_data" / "papers" / "test_paper_cv.md",
                      PROJECT / "tests" / "test_data" / "expected" / "ground_truth_cv.json"),
}

QUESTIONS = [
    ("test_paper_simple", "What is the main contribution of EfficientGraph?",
     ["EfficientGraph", "hierarchical", "attention", "dynamic pruning"]),
    ("test_paper_simple", "What accuracy does EfficientGraph achieve on Cora?",
     ["87.2"]),
    ("test_paper_simple", "What is the claimed average accuracy from Table 1?",
     ["83.7"]),
    ("test_paper_cv", "What is the main contribution of SegNet-Lite?",
     ["SegNet-Lite", "depthwise separable", "Light Decoder", "real-time"]),
    ("test_paper_cv", "What mIoU and FPS does SegNet-Lite achieve on Cityscapes?",
     ["78.3", "42.1"]),
]


def evaluate_question(kb, question, keywords):
    chunks = kb.search_chunks(question, top_k=3)
    joined = " ".join(c.content.lower() for c in chunks)
    hits = [kw for kw in keywords if kw.lower() in joined]
    mrr = 0.0
    for i, c in enumerate(chunks):
        if any(kw.lower() in c.content.lower() for kw in keywords):
            mrr = 1.0 / (i + 1)
            break
    return {
        "question": question,
        "keywords": keywords,
        "hits": hits,
        "recall": len(hits) / max(len(keywords), 1),
        "precision": len(hits) / max(len(chunks), 1),
        "mrr": mrr,
    }


def evaluate_paper(paper_id, text_path, _):
    text = text_path.read_text(encoding="utf-8")
    kb_dir = PROJECT / "workspace" / "benchmark_kb_local" / paper_id
    import shutil
    if kb_dir.exists():
        shutil.rmtree(kb_dir)
    kb = KnowledgeBase(kb_dir, advanced_rag=False)
    kb.add(text, metadata={"paper_id": paper_id})

    results = []
    for q_paper, q, kw in QUESTIONS:
        if q_paper == paper_id:
            results.append(evaluate_question(kb, q, kw))
    if not results:
        return None

    recalls = [r["recall"] for r in results]
    return {
        "paper_id": paper_id,
        "text_chars": len(text),
        "question_count": len(results),
        "recall_at_3": round(sum(recalls) / len(recalls), 4),
        "precision_at_3": round(sum(r["precision"] for r in results) / len(results), 4),
        "mrr": round(sum(r["mrr"] for r in results) / len(results), 4),
        "questions": results,
    }


def main():
    t0 = time.time()
    paper_results = []
    for paper_id, (text_path, truth_path) in PAPERS.items():
        res = evaluate_paper(paper_id, text_path, truth_path)
        if res:
            paper_results.append(res)
            print(f"{paper_id}: recall@3={res['recall_at_3']:.1%} "
                  f"precision@3={res['precision_at_3']:.1%} mrr={res['mrr']:.3f}")

    overall_recall = sum(r["recall_at_3"] for r in paper_results) / len(paper_results)
    overall_precision = sum(r["precision_at_3"] for r in paper_results) / len(paper_results)
    overall_mrr = sum(r["mrr"] for r in paper_results) / len(paper_results)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "local_test_papers",
        "advanced_rag": False,
        "overall": {
            "recall_at_3": round(overall_recall, 4),
            "precision_at_3": round(overall_precision, 4),
            "mrr": round(overall_mrr, 4),
            "target_recall_at_3": 0.70,
            "target_met": overall_recall >= 0.70,
        },
        "papers": paper_results,
        "duration_seconds": round(time.time() - t0, 1),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"rag_local_{timestamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_DIR / "latest_rag.json"
    latest.write_text(json.dumps({"latest": str(path.relative_to(PROJECT)),
                                    "report": report}, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"\nOverall Recall@3: {overall_recall:.1%}")
    print(f"Overall Precision@3: {overall_precision:.1%}")
    print(f"Overall MRR: {overall_mrr:.3f}")
    print(f"Target recall@3 >= 70%: {'PASS' if overall_recall >= 0.70 else 'FAIL'}")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
