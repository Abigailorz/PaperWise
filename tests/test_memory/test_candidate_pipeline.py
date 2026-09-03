import json

from paperwise.core.llm_client import LLMResponse
from paperwise.memory.user_memory import MemoryCard, UserMemory


class _MemoryLLM:
    def __init__(self, key: str = "topic", value: str = "CV", confidence: float = 0.85):
        self.key = key
        self.value = value
        self.confidence = confidence

    async def chat(self, *args, **kwargs):
        payload = {
            "research_fields": [],
            "memories": [{
                "category": "fact",
                "key": self.key,
                "value": self.value,
                "backstory": "user said it",
                "confidence": self.confidence,
            }],
        }
        return LLMResponse(content=json.dumps(payload, ensure_ascii=False))


async def test_candidate_pipeline_keeps_first_observation_pending(tmp_path):
    memory = UserMemory(
        tmp_path / "memory", user_id="alice",
        candidate_pipeline_enabled=True,
    )
    cards = await memory.extract_from_conversation(
        _MemoryLLM(), "My research topic is CV.", "Understood.",
        source_message_ids=["msg_user", "msg_agent"],
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.status == "candidate"
    assert card.observation_count == 1
    assert card.source_message_ids == ["msg_user", "msg_agent"]
    assert memory.query() == []
    assert [c.card_id for c in memory.pending_cards()] == [card.card_id]


async def test_second_observation_confirms_high_confidence_candidate(tmp_path):
    memory = UserMemory(
        tmp_path / "memory", user_id="alice",
        candidate_pipeline_enabled=True,
    )
    await memory.extract_from_conversation(
        _MemoryLLM(), "I work on CV.", "Noted."
    )
    cards = await memory.extract_from_conversation(
        _MemoryLLM(), "Again: CV is my research area.", "Got it."
    )

    card = cards[0]
    assert card.status == "active"
    assert card.observation_count == 2
    assert card.confidence >= 0.8
    assert card.stability > 0
    assert memory.query()[0].card_id == card.card_id
    assert memory.pending_cards() == []


async def test_low_confidence_candidate_requires_manual_decision(tmp_path):
    memory = UserMemory(
        tmp_path / "memory", user_id="alice",
        candidate_pipeline_enabled=True,
    )
    await memory.extract_from_conversation(
        _MemoryLLM(confidence=0.70), "I like concise summaries.", "Okay."
    )
    cards = await memory.extract_from_conversation(
        _MemoryLLM(confidence=0.70), "Use concise summaries.", "Okay."
    )
    card = cards[0]

    assert card.observation_count == 2
    assert card.status == "candidate"
    assert memory.update_status(card.card_id, "active")
    assert memory.query()[0].status == "active"

    assert memory.update_status(card.card_id, "dropped")
    assert memory.query() == []


async def test_pipeline_disabled_preserves_legacy_active_behavior(tmp_path):
    memory = UserMemory(tmp_path / "memory")
    cards = await memory.extract_from_conversation(
        _MemoryLLM(), "My research topic is CV.", "Understood.",
        source_message_ids=["msg_user"],
    )

    assert cards[0].status == "active"
    assert memory.pending_cards() == []
    assert memory.query()[0].source_message_ids == ["msg_user"]


def test_old_memory_card_without_new_fields_loads(tmp_path):
    old = {
        "card_id": "mem_fact_old",
        "category": "fact",
        "data": {"topic": "CV"},
        "confidence": 0.9,
        "timestamp": "2025-01-01T00:00:00",
    }
    card = MemoryCard.from_dict(old)

    assert card.importance == 0.5
    assert card.stability == 0.0
    assert card.observation_count == 1
    assert card.source_message_ids == []

    memory = UserMemory(tmp_path / "memory")
    memory.cards[card.card_id] = card
    memory._save()
    reloaded = UserMemory(tmp_path / "memory")
    assert reloaded.cards[card.card_id].observation_count == 1
