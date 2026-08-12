"""知识库测试 — RAPTOR / GraphRAG 索引持久化（避免重复 LLM 调用）"""

from paperwise.memory.knowledge_base import KnowledgeBase


class MockLLM:
    """模拟 LLM：摘要返回文本，图谱返回 JSON。"""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages=None, **kwargs):
        self.calls += 1
        content = messages[0]["content"] if messages else ""
        if "JSON" in content or "实体" in content:
            payload = (
                '{"entities":[{"name":"GNN","type":"concept","description":"d"}],'
                '"relations":[{"source":"GNN","target":"Attention","relation":"uses"}]}'
            )
        else:
            payload = "cluster summary about graph neural networks"
        return type("Resp", (), {"content": payload})()


def test_raptor_and_graph_are_persisted(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    llm = MockLLM()
    kb.set_llm_client(llm)
    for i in range(12):
        kb.add(
            f"Paragraph {i}: graph neural networks use attention mechanisms "
            f"and achieve 83.7 percent accuracy on node classification.",
            {"title": f"doc{i}", "type": "paper_fulltext"},
        )

    n1 = kb.build_raptor_tree()
    g1 = kb.build_knowledge_graph()
    calls_after_first = llm.calls
    assert n1 > 0
    assert g1.get("entities")

    n2 = kb.build_raptor_tree()
    g2 = kb.build_knowledge_graph()

    assert n2 > 0
    assert g2 == g1
    assert llm.calls == calls_after_first, "第二次构建不应再调用 LLM（命中持久化缓存）"
