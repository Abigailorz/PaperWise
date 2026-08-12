"""用户记忆测试 — 整合/清理/合并"""

from datetime import datetime

from paperwise.memory.user_memory import UserMemory, MemoryCard


def _card(cid, category, data, confidence=0.8, timestamp=None):
    return MemoryCard(
        card_id=cid, category=category, data=data, confidence=confidence,
        timestamp=timestamp or datetime.now().isoformat(),
    )


def test_consolidate_removes_low_confidence_and_merges_duplicates(tmp_path):
    mem = UserMemory(tmp_path / "mem")
    mem.cards["low"] = _card("low", "fact", {"x": "old"}, confidence=0.1)
    mem.cards["dup1"] = _card("dup1", "preference", {"lang": "zh"}, confidence=0.6)
    mem.cards["dup2"] = _card("dup2", "preference", {"lang": "zh"}, confidence=0.9)

    report = mem.consolidate(min_confidence=0.3)

    assert report["removed"] == 1
    assert report["merged"] == 1
    assert "low" not in mem.cards
    prefs = [c for c in mem.cards.values() if c.category == "preference"]
    assert len(prefs) == 1
    assert prefs[0].confidence == 0.9


def test_consolidate_caps_category_size(tmp_path):
    mem = UserMemory(tmp_path / "mem")
    for i in range(35):
        mem.cards[f"exp_{i}"] = _card(
            f"exp_{i}", "experience", {f"k{i}": "v"}, confidence=0.5 + i / 100,
        )

    report = mem.consolidate(max_per_category=20)

    assert report["demoted"] == 15
    assert len([c for c in mem.cards.values() if c.category == "experience"]) == 20


def test_maybe_consolidate_respects_interval(tmp_path):
    mem = UserMemory(tmp_path / "mem")

    first = mem.maybe_consolidate(interval_days=7)
    second = mem.maybe_consolidate(interval_days=7)

    assert first["skipped"] is False
    assert second["skipped"] is True
