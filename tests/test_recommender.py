"""主动论文推荐测试"""

import asyncio
from pathlib import Path

from paperwise.recommender import PaperRecommender
from paperwise.memory.user_memory import UserMemory


def _fake_papers(topics):
    return [
        {
            "arxiv_id": "2401.00001",
            "title": "Efficient 3D Gaussian Splatting for Real-Time Rendering",
            "summary": "we propose novel methods for 3D Gaussian Splatting rendering",
            "url": "https://arxiv.org/abs/2401.00001",
            "authors": ["A"], "published": "2026-08-01",
        },
        {
            "arxiv_id": "2401.00002",
            "title": "Unrelated Topic on Climate Modeling",
            "summary": "weather and climate modeling study",
            "url": "https://arxiv.org/abs/2401.00002",
            "authors": ["B"], "published": "2026-08-02",
        },
    ]


def test_score_paper_title_bonus():
    rec = PaperRecommender(Path("."))
    paper = {"title": "Gaussian Splatting Survey", "summary": "review of rendering"}
    s = rec.score_paper(paper, ["Gaussian Splatting", "rendering"])
    assert s["score"] > 0.6  # 标题命中权重更高
    assert "Gaussian Splatting" in s["matched"]


def test_score_paper_partial_token_match():
    rec = PaperRecommender(Path("."))
    paper = {"title": "Splatting Everything: A Survey of Radiance Fields",
             "summary": ""}
    s = rec.score_paper(paper, ["3D Gaussian Splatting"])
    assert s["score"] > 0  # "splatting" token 部分命中
    assert s["matched"] == ["3D Gaussian Splatting"]


def test_get_topics_from_memory(tmp_path):
    mem = UserMemory(tmp_path / "mem")
    mem.remember("preference", {"research_fields": "3D Gaussian Splatting"},
                 confidence=0.95)
    rec = PaperRecommender(tmp_path, memory=mem)
    topics = rec.get_research_topics()
    assert "3D Gaussian Splatting" in topics


def test_get_topics_parses_json_and_separators(tmp_path):
    mem = UserMemory(tmp_path / "mem")
    mem.remember("preference", {"research_fields": '["3DGS", "Agent"]'},
                 confidence=0.95)
    rec = PaperRecommender(tmp_path, memory=mem)
    assert set(rec.get_research_topics()) == {"3DGS", "Agent"}

    mem2 = UserMemory(tmp_path / "mem2")
    mem2.remember("fact", {"interests": "CV、NLP; Robotics"}, confidence=0.9)
    rec2 = PaperRecommender(tmp_path, memory=mem2)
    topics = rec2.get_research_topics()
    assert "Robotics" in topics and "NLP" in topics and "CV" in topics


async def test_recommend_ranks_and_caches(tmp_path, monkeypatch):
    mem = UserMemory(tmp_path / "mem")
    mem.remember("preference", {"research_fields": "3D Gaussian Splatting"},
                 confidence=0.95)
    rec = PaperRecommender(tmp_path, memory=mem)

    async def fake_fetch(topics, max_results=30, days=7):
        return _fake_papers(topics)

    monkeypatch.setattr(rec, "fetch_recent_papers", fake_fetch)

    result = await rec.recommend(limit=5)
    assert result["cached"] is False
    assert result["papers"]
    assert result["papers"][0]["arxiv_id"] == "2401.00001"  # 相关论文排前
    assert result["papers"][0]["score"] > 0

    # 第二次调用命中缓存，不再 fetch
    result2 = await rec.recommend(limit=5)
    assert result2["cached"] is True


def test_no_topics_returns_empty(tmp_path):
    rec = PaperRecommender(tmp_path)
    result = asyncio.run(rec.recommend())
    assert result["papers"] == []
    assert result["reason"] == "no_topics"
