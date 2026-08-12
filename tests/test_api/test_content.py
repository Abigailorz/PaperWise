"""内容管理 API 测试 — 记忆 / 章节编辑 / 评估结果 / arXiv 校验"""


def test_memory_list_and_delete(client, tmp_path):
    import paperwise.config.settings as settings_mod
    from paperwise.memory.user_memory import UserMemory
    ws = settings_mod.get_settings().workspace_dir

    mem = UserMemory(ws / ".paperwise" / "default" / "memory")
    card = mem.remember("preference", {"lang": "zh"}, confidence=0.9)

    r = client.get("/api/memory")
    assert any(c["card_id"] == card.card_id for c in r.json()["cards"])

    r = client.delete(f"/api/memory/{card.card_id}")
    assert r.json()["deleted"] is True

    r = client.get("/api/memory")
    assert not any(c["card_id"] == card.card_id for c in r.json()["cards"])


def test_memory_user_isolation(client, tmp_path):
    import paperwise.config.settings as settings_mod
    from paperwise.memory.user_memory import UserMemory
    ws = settings_mod.get_settings().workspace_dir

    mem = UserMemory(ws / ".paperwise" / "alice" / "memory")
    card = mem.remember("fact", {"topic": "NLP"}, confidence=0.9)

    r = client.get("/api/memory", headers={"X-User-Id": "bob"})
    assert not any(c["card_id"] == card.card_id for c in r.json()["cards"])

    r = client.get("/api/memory", headers={"X-User-Id": "alice"})
    assert any(c["card_id"] == card.card_id for c in r.json()["cards"])


def test_sections_save_and_get(client, tmp_path):
    import paperwise.config.settings as settings_mod
    pd = settings_mod.get_settings().workspace_dir / "paper_test"
    (pd / "report" / "sections").mkdir(parents=True)
    (pd / "report" / "sections" / "overview.md").write_text("old", encoding="utf-8")

    r = client.get("/api/paper/sections", params={"paper_dir": str(pd)})
    assert r.json()["sections"]["overview"] == "old"

    r = client.post("/api/paper/sections", json={
        "paper_dir": str(pd), "section": "overview", "content": "new content",
    })
    assert r.json()["saved"] is True
    assert (pd / "report" / "sections" / "overview.md").read_text(encoding="utf-8") == "new content"

    # 非法章节名被拒绝
    r = client.post("/api/paper/sections", json={
        "paper_dir": str(pd), "section": "../evil", "content": "x",
    })
    assert r.status_code == 400


def test_eval_results_empty(client, tmp_path):
    r = client.get("/api/eval/results")
    assert r.json() == {"runs": []}


def test_arxiv_endpoint_rejects_invalid_url(client):
    r = client.post("/api/sessions/any/arxiv", json={"url": "https://github.com/foo/bar"})
    assert r.status_code == 400
