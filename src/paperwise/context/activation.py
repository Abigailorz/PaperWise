"""Selective activation for context sources (spec E1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ActivationScore:
    item: Any
    score: float
    text: str


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return " ".join(str(value) for value in item.values())
    if hasattr(item, "data") and isinstance(item.data, dict):
        return " ".join(str(value) for value in item.data.values())
    return str(item)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9\-_.]{1,}", text.lower()))


def score_item(query: str, item: Any) -> ActivationScore:
    text = _item_text(item)
    query_tokens = _tokens(query)
    item_tokens = _tokens(text)
    overlap = len(query_tokens & item_tokens)
    score = overlap / max(len(query_tokens), 1)
    if text.lower() in query.lower() or query.lower() in text.lower():
        score += 0.25
    score += float(getattr(item, "importance", 0.0) or 0.0) * 0.2
    score += float(getattr(item, "confidence", 0.0) or 0.0) * 0.1
    return ActivationScore(item=item, score=round(score, 6), text=text)


def select_items(
    query: str,
    items: Iterable[Any] | None,
    limit: int = 5,
    *,
    drop_unmatched: bool = True,
) -> list[Any]:
    """Rank by lexical activation and importance; keep output deterministic."""
    if not items:
        return []
    scored = [score_item(query, item) for item in items]
    scored.sort(key=lambda result: (-result.score, result.text))
    # A zero-overlap item is not activated by this query, even when the
    # caller's limit still has room.  This prevents unrelated preferences
    # from entering a paper-specific context.
    has_match = any(result.score > 0 for result in scored)
    keep_all = not drop_unmatched or not has_match
    selected = [
        result.item for result in scored
        if result.score > 0 or keep_all
    ][: max(limit, 0)]
    # Preserve caller order so selectors produce stable serialization.
    selected_set = {id(item) for item in selected}
    return [item for item in items if id(item) in selected_set]
