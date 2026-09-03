from pathlib import Path

from paperwise.evidence import EvidenceRetriever, section_chunks
from paperwise.memory.knowledge_base import KnowledgeBase


PAPER_TEXT = """Title: Test Paper

Introduction
This paper introduces a novel method for adaptive memory retrieval.
It improves answer quality while reducing context use.

Method
The method uses section-aware chunks and BM25 retrieval.
The adaptive memory router selects evidence before reasoning.

Results
The system reaches 92.3 accuracy with a 41 percent token reduction.

References
[1] Retrieval-grounded agents, 2026.
"""


def make_paper(tmp_path: Path, paper_id: str = "test-paper") -> Path:
    paper_dir = tmp_path / paper_id
    paper_dir.mkdir(parents=True)
    (paper_dir / "text.md").write_text(PAPER_TEXT, encoding="utf-8")
    (paper_dir / "references.json").write_text(
        '[{"id": 1, "text": "Retrieval-grounded agents, 2026."}]', encoding="utf-8"
    )
    figures = paper_dir / "figures"
    figures.mkdir()
    (figures / "index.json").write_text(
        '[{"index": 1, "page": 2, "caption": "Architecture of adaptive memory retrieval.", "path": "figures/figure_1.png"}]',
        encoding="utf-8",
    )
    return paper_dir


def test_section_chunks_preserve_lines_and_sections():
    chunks = section_chunks(PAPER_TEXT, chunk_size=120)
    assert {c["section"] for c in chunks} >= {"Introduction", "Method", "Results"}
    for chunk in chunks:
        assert chunk["start_line"] <= chunk["end_line"]
    assert any("92.3 accuracy" in c["content"] for c in chunks)


def test_evidence_retriever_current_paper(tmp_path):
    paper_dir = make_paper(tmp_path)
    retriever = EvidenceRetriever(KnowledgeBase(tmp_path / "kb"))
    counts = retriever.index_paper(paper_dir)
    assert counts["section"] >= 3
    assert counts["figure"] == 1
    assert counts["reference"] == 1

    pack = retriever.retrieve("adaptive memory retrieval", paper_dir=paper_dir, top_k=3)
    assert not pack.is_empty
    assert any("adaptive memory" in s.content.lower() for s in pack.snippets)
    assert all(s.paper_id == "test-paper" for s in pack.snippets)
    assert any(s.citation().startswith("[source: test-paper/text.md L") for s in pack.snippets)


def test_evidence_retriever_cross_paper_scope_with_library_alias(tmp_path):
    first = make_paper(tmp_path / "a")
    second = make_paper(tmp_path / "b", paper_id="second-paper")
    retriever = EvidenceRetriever(KnowledgeBase(tmp_path / "kb"))
    retriever.index_paper(first)
    retriever.index_paper(second)
    pack = retriever.retrieve("adaptive memory retrieval", scope="library", top_k=5)
    assert {s.paper_id for s in pack.snippets} == {"test-paper", "second-paper"}
    # ``library`` is a compatibility alias; the public API reports cross_paper.
    assert pack.scope == "cross_paper"


def test_retrieval_failure_marks_low_recall(tmp_path):
    paper_dir = make_paper(tmp_path)
    retriever = EvidenceRetriever(KnowledgeBase(tmp_path / "kb"))
    retriever.index_paper(paper_dir)
    pack = retriever.retrieve("nonexistent quantum banana", paper_dir=paper_dir, top_k=2)
    assert pack.low_recall or pack.is_empty or len(pack.retrieval_queries) > 1
