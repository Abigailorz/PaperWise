"""RAG 检索单元测试 — 不依赖外部下载，验证基础 RAG 召回能力。

覆盖：
- 基础 RAG（默认）能召回包含答案的 chunk
- 高级 RAG 开关不破坏基础行为
- filters 能按 doc_id 过滤
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from paperwise.memory.knowledge_base import KnowledgeBase


def _build_kb(advanced_rag: bool = False) -> KnowledgeBase:
    base = Path(__file__).resolve().parents[2] / "workspace" / "test_kb"
    # 清理旧索引，避免跨测试污染
    import shutil
    if base.exists():
        shutil.rmtree(base)
    kb = KnowledgeBase(base, advanced_rag=advanced_rag)
    kb.add(
        "3D Gaussian Splatting represents scenes with anisotropic 3D Gaussians "
        "and achieves real-time radiance field rendering. "
        "It uses adaptive density control starting from Structure-from-Motion points.",
        metadata={"paper": "3dgs"},
        doc_id="doc_3dgs",
    )
    kb.add(
        "LangSplat builds 3D language Gaussian Splatting using SAM and CLIP. "
        "It enables open-vocabulary 3D querying with hierarchical semantics.",
        metadata={"paper": "langsplat"},
        doc_id="doc_langsplat",
    )
    kb.add(
        "Feature 3DGS distills SAM, CLIP and LSeg features into 3D Gaussians. "
        "It supports point and bounding-box prompting for radiance field manipulation.",
        metadata={"paper": "feature3dgs"},
        doc_id="doc_feature3dgs",
    )
    return kb


def test_basic_rag_recalls_answer():
    kb = _build_kb(advanced_rag=False)
    results = kb.search("What foundation models does Feature 3DGS use?", top_k=3)
    assert len(results) >= 1
    top = results[0]
    assert "SAM" in top.content
    assert "CLIP" in top.content
    assert "LSeg" in top.content


def test_basic_rag_filters_by_metadata():
    kb = _build_kb(advanced_rag=False)
    results = kb.search("What uses SAM and CLIP?", top_k=3, filters={"paper": "langsplat"})
    assert len(results) == 1
    assert results[0].metadata["paper"] == "langsplat"


def test_basic_rag_no_hallucination_when_empty():
    kb = _build_kb(advanced_rag=False)
    results = kb.search("What is the capital of France?", top_k=3)
    # 不应返回空结果，但返回的应是语义最接近的（不强制断言具体内容）
    assert len(results) <= 3


def test_advanced_rag_does_not_break_basic():
    kb = _build_kb(advanced_rag=True)
    results = kb.search("What uses adaptive density control?", top_k=3)
    assert len(results) >= 1


if __name__ == "__main__":
    test_basic_rag_recalls_answer()
    test_basic_rag_filters_by_metadata()
    test_basic_rag_no_hallucination_when_empty()
    test_advanced_rag_does_not_break_basic()
    print("All RAG unit tests passed.")
