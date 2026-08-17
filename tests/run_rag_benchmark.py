#!/usr/bin/env python3
"""PaperWise RAG 检索基准测试（真实论文）。

下载 4 篇固定论文 PDF，解析为文本，建立 KnowledgeBase，
从 golden 文件生成问题，检索 top-3 chunk，统计 recall@3 / precision@3 / MRR。

用法：
    python tests/run_rag_benchmark.py

结果保存到 workspace/benchmarks/rag_YYYYMMDD_HHMMSS.json。
"""

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paperwise.memory.knowledge_base import KnowledgeBase

PROJECT = Path(__file__).resolve().parents[1]
PDF_DIR = PROJECT / "tests" / "test_data" / "real_papers"
GOLDEN_DIR = PROJECT / "测评" / "results" / "golden"
OUT_DIR = PROJECT / "workspace" / "benchmarks"

PAPERS = {
    "3dgs_2308.04079": "2308.04079",
    "langsplat_2312.16084": "2312.16084",
    "feature3dgs_2312.03203": "2312.03203",
    "gaussaingrouping_2312.00732": "2312.00732",
}


async def download_pdf(name: str, arxiv_id: str) -> Path:
    """下载 PDF；已存在则跳过。失败时重试一次。"""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    target = PDF_DIR / f"{name}.pdf"
    if target.exists() and target.stat().st_size > 1000:
        return target

    import httpx
    url = f"https://export.arxiv.org/pdf/{arxiv_id}"
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, trust_env=False) as client:
        for attempt in range(2):
            try:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(target, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                if target.stat().st_size > 1000:
                    return target
            except Exception as e:
                if attempt == 1:
                    raise
                print(f"  [retry] {name}: {type(e).__name__}")
                await asyncio.sleep(2)
    return target


def parse_pdf(path: Path) -> str:
    """用 PyMuPDF 解析 PDF 为纯文本。"""
    import fitz
    doc = fitz.open(str(path))
    parts = []
    for page in doc:
        parts.append(page.get_text())
    return "\n".join(parts)


def generate_questions(golden: dict) -> list[dict]:
    """从 golden 文件提取问题与期望关键词。"""
    questions = []
    findings = golden.get("expected_findings", {})
    criteria = golden.get("evaluation_criteria", {})

    title = golden.get("title", "this paper")

    if "main_contribution" in findings:
        questions.append({
            "id": f"{golden['paper_id']}_contribution",
            "question": f"What is the main contribution of {title}?",
            "keywords": _extract_keywords(findings["main_contribution"]),
        })

    if "key_improvement" in findings:
        questions.append({
            "id": f"{golden['paper_id']}_improvement",
            "question": f"What is the key improvement of {title}?",
            "keywords": _extract_keywords(findings["key_improvement"]),
        })

    if "foundation_models" in findings:
        models = findings["foundation_models"]
        questions.append({
            "id": f"{golden['paper_id']}_models",
            "question": f"What foundation models does {title} use?",
            "keywords": [m.lower() for m in models],
        })

    if "applications" in findings:
        apps = findings["applications"]
        questions.append({
            "id": f"{golden['paper_id']}_applications",
            "question": f"What applications or editing tasks does {title} support?",
            "keywords": [a.lower() for a in apps],
        })

    if "supervision" in findings:
        questions.append({
            "id": f"{golden['paper_id']}_supervision",
            "question": f"How is the method of {title} supervised or trained?",
            "keywords": _extract_keywords(findings["supervision"]),
        })

    # 从 evaluation criteria 中追加关键词要求
    for kw in criteria.get("required_citations", [])[:5]:
        if len(kw) > 2 and not any(kw.lower() in q["keywords"] for q in questions):
            questions.append({
                "id": f"{golden['paper_id']}_kw_{kw}",
                "question": f"What does {title} say about '{kw}'?",
                "keywords": [kw.lower()],
            })

    return questions


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取 2-20 个字母/数字的关键词，过滤常见停用词。"""
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "by",
        "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
        "those", "from", "at", "it", "its", "as", "into", "through", "using", "based",
    }
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-_.]{1,20}", text)
    return [w.lower() for w in words if w.lower() not in stop and len(w) > 2][:10]


def evaluate_question(kb: KnowledgeBase, q: dict) -> dict:
    """对单个问题检索 top-3 chunk，统计关键词命中。"""
    chunks = kb.search_chunks(q["question"], top_k=3)
    joined = " ".join(c.content.lower() for c in chunks)
    hits = [kw for kw in q["keywords"] if kw in joined]

    # MRR：第一个包含任意关键词的 chunk 排名
    mrr = 0.0
    for i, c in enumerate(chunks):
        if any(kw in c.content.lower() for kw in q["keywords"]):
            mrr = 1.0 / (i + 1)
            break

    return {
        "id": q["id"],
        "question": q["question"],
        "keywords": q["keywords"],
        "hits": hits,
        "recall": len(hits) / max(len(q["keywords"]), 1),
        "precision": len(hits) / max(len(chunks), 1),
        "mrr": mrr,
        "retrieved_chars": len(joined),
    }


async def evaluate_paper(name: str, arxiv_id: str) -> dict:
    """评估单篇论文。"""
    golden_path = GOLDEN_DIR / f"golden_{name}.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    pdf = await download_pdf(name, arxiv_id)
    text = parse_pdf(pdf)

    kb_dir = PROJECT / "workspace" / "benchmark_kb" / name
    import shutil
    if kb_dir.exists():
        shutil.rmtree(kb_dir)
    kb = KnowledgeBase(kb_dir, advanced_rag=False)
    kb.add(text, metadata={"paper_id": name, "title": golden.get("title", "")})

    questions = generate_questions(golden)
    results = [evaluate_question(kb, q) for q in questions]

    recalls = [r["recall"] for r in results]
    precisions = [r["precision"] for r in results]
    mrrs = [r["mrr"] for r in results]

    return {
        "paper_id": name,
        "arxiv_id": arxiv_id,
        "title": golden.get("title", ""),
        "text_chars": len(text),
        "question_count": len(questions),
        "recall_at_3": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "precision_at_3": round(sum(precisions) / len(precisions), 4) if precisions else 0.0,
        "mrr": round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0,
        "questions": results,
    }


async def main():
    t0 = time.time()
    # 先顺序下载所有 PDF，避免并发压垮 export.arxiv.org
    for name, aid in PAPERS.items():
        print(f"Downloading {name}...")
        await download_pdf(name, aid)

    tasks = [evaluate_paper(name, aid) for name, aid in PAPERS.items()]
    paper_results = await asyncio.gather(*tasks)

    overall_recall = sum(r["recall_at_3"] for r in paper_results) / len(paper_results)
    overall_precision = sum(r["precision_at_3"] for r in paper_results) / len(paper_results)
    overall_mrr = sum(r["mrr"] for r in paper_results) / len(paper_results)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    out_path = OUT_DIR / f"rag_{timestamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同时写入 latest.json
    latest_path = OUT_DIR / "latest.json"
    latest_path.write_text(json.dumps({"latest": str(out_path.relative_to(PROJECT)),
                                         "report": report}, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    print(f"RAG Benchmark Recall@3: {report['overall']['recall_at_3']:.1%}")
    print(f"Precision@3: {report['overall']['precision_at_3']:.1%}")
    print(f"MRR: {report['overall']['mrr']:.3f}")
    print(f"Target recall@3 >= 70%: {'PASS' if report['overall']['target_met'] else 'FAIL'}")
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
