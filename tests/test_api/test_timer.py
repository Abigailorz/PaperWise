"""主动定时器 API 测试"""


def test_timer_endpoint(client):
    r = client.post("/api/sessions")
    sid = r.json()["session_id"]

    r = client.post(f"/api/sessions/{sid}/timer",
                    json={"seconds": 60, "message": "检查进度"})
    d = r.json()

    assert d["timer_id"]
    assert d["seconds"] == 60
    assert "检查进度" in d["message"]
