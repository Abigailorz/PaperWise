"""Proactive Engine: decide when and what to recommend to the user."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from paperwise.memory.research_state import ResearchState, KnowledgeGap
from paperwise.memory.episodic_memory import EpisodicMemory
from paperwise.memory.knowledge_base import KnowledgeBase
from paperwise.recommender import PaperRecommender


@dataclass
class Recommendation:
    """A single proactive recommendation."""
    arxiv_id: Optional[str] = None
    title: str = ""
    summary: str = ""
    url: str = ""
    score: float = 0.0
    reason: str = ""
    linked_gap: Optional[str] = None
    suggested_action: str = ""
    source: str = ""  # arxiv | knowledge_base | episode

    def to_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "score": self.score,
            "reason": self.reason,
            "linked_gap": self.linked_gap,
            "suggested_action": self.suggested_action,
            "source": self.source,
        }


@dataclass
class ProactivePolicy:
    """Policy for deciding whether to push a recommendation."""
    score_threshold: float = 0.35
    min_interval_seconds: float = 300.0
    max_per_push: int = 3
    quiet_hours: tuple[int, int] = (0, 7)
    focus_mode_block: bool = True

    def should_push(self, last_push_ts: float, score: float, focus_mode: bool = False) -> bool:
        if score < self.score_threshold:
            return False
        if focus_mode and self.focus_mode_block:
            return False
        if time.time() - last_push_ts < self.min_interval_seconds:
            return False
        return True


class ProactiveEngine:
    """Event-driven proactive paper recommendation."""

    def __init__(
        self,
        workspace: Path,
        user_id: str = "default",
        episodic_memory: Optional[EpisodicMemory] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
        recommender: Optional[PaperRecommender] = None,
        policy: Optional[ProactivePolicy] = None,
    ):
        self.workspace = workspace
        self.user_id = user_id
        self.episodic_memory = episodic_memory or EpisodicMemory(workspace / ".paperwise" / user_id / "episodes", user_id=user_id)
        self.knowledge_base = knowledge_base
        self.recommender = recommender or PaperRecommender(workspace)
        self.policy = policy or ProactivePolicy()
        self._last_push_ts: float = 0.0
        self._seen_ids: set[str] = set()

    async def decide(self, state: ResearchState, focus_mode: bool = False) -> list[Recommendation]:
        """Return recommendations if policy allows."""
        topics = self._extract_topics(state)
        if not topics:
            return []

        candidates: list[Recommendation] = []
        # Source 1: arXiv
        arxiv_results = await self._fetch_arxiv(topics)
        candidates.extend(arxiv_results)

        # Source 2: Knowledge base
        if self.knowledge_base:
            kb_results = self._fetch_from_kb(state, topics)
            candidates.extend(kb_results)

        # Source 3: historical episodes (related papers)
        ep_results = self._fetch_from_episodes(state)
        candidates.extend(ep_results)

        # Deduplicate and score
        scored = []
        for rec in candidates:
            if rec.arxiv_id and rec.arxiv_id in self._seen_ids:
                continue
            rec.score = self._score(rec, state, topics)
            scored.append(rec)

        scored.sort(key=lambda r: r.score, reverse=True)
        top = scored[: self.policy.max_per_push]

        best_score = top[0].score if top else 0.0
        if not self.policy.should_push(self._last_push_ts, best_score, focus_mode):
            return []

        self._last_push_ts = time.time()
        for rec in top:
            if rec.arxiv_id:
                self._seen_ids.add(rec.arxiv_id)
        return top


    def record_feedback(self, arxiv_id: str, helpful: bool, user_id: str = "default") -> None:
        """Update memory based on explicit user feedback on a recommendation.

        Positive feedback boosts confidence of related profile memories;
        negative feedback archives or demotes them.
        """
        delta = 0.1 if helpful else -0.15
        # Find profile memories that might have triggered this recommendation
        # (heuristic: any active preference/fact card)
        for card in list(self.recommender.memory.cards.values() if self.recommender.memory else []):
            if card.status != "active":
                continue
            if card.category in ("preference", "fact", "knowledge"):
                # Only adjust if card relates to paper keywords
                card_text = " ".join(str(v) for v in card.data.values()).lower()
                if arxiv_id.lower() in card_text or any(
                    t.lower() in card_text for t in self.recommender._clean_topics([arxiv_id])
                ):
                    self.recommender.memory.apply_feedback(card.card_id, delta)

        # Also update any episode that generated this candidate
        for ep in self.episodic_memory.episodes.values():
            if arxiv_id in ep.entities:
                if helpful:
                    ep.outcome = "recommended_approved"
                else:
                    ep.outcome = "recommended_rejected"
                self.episodic_memory.update_outcome(ep.episode_id, ep.outcome)
                break

    def _extract_topics(self, state: ResearchState) -> list[str]:
        topics = []
        # Long-term interests from recommender profile
        try:
            profile = self.recommender.build_interest_profile(self.user_id)
            topics.extend(p["topic"] for p in profile if p.get("topic"))
        except Exception:
            pass
        # Gaps are high-priority signals
        for gap in state.gaps:
            topics.extend(self.recommender._clean_topics([gap.description]))
        # Task text
        topics.extend(self.recommender._clean_topics([state.current_task]))
        return topics

    async def _fetch_arxiv(self, topics: list[str]) -> list[Recommendation]:
        try:
            result = await self.recommender.recommend(user_id=self.user_id, topics=topics, limit=10, use_cache=True)
        except Exception:
            return []
        papers = result.get("papers", [])
        out = []
        for p in papers:
            out.append(Recommendation(
                arxiv_id=p.get("arxiv_id"),
                title=p.get("title", ""),
                summary=p.get("summary", ""),
                url=p.get("url", ""),
                source="arxiv",
            ))
        return out

    def _fetch_from_kb(self, state: ResearchState, topics: list[str]) -> list[Recommendation]:
        if not self.knowledge_base or not state.current_paper:
            return []
        query = " ".join([state.current_task] + [g.description for g in state.gaps[:3]] + topics[:3])
        try:
            results = self.knowledge_base.search(query, top_k=5, search_chunks=True)
        except Exception:
            return []
        out = []
        for r in results:
            doc_id = r.get("doc_id", "")
            out.append(Recommendation(
                arxiv_id=doc_id,
                title=doc_id,
                summary=r.get("text", "")[:300],
                source="knowledge_base",
            ))
        return out

    def _fetch_from_episodes(self, state: ResearchState) -> list[Recommendation]:
        if not state.current_paper:
            return []
        episodes = self.episodic_memory.query(entity=state.current_paper, limit=5)
        out = []
        for ep in episodes:
            for paper in ep.entities:
                if paper and paper != state.current_paper:
                    out.append(Recommendation(
                        arxiv_id=paper,
                        title=paper,
                        source="episode",
                    ))
        return out

    def _score(self, rec: Recommendation, state: ResearchState, topics: list[str]) -> float:
        haystack = f"{rec.title} {rec.summary}".lower()
        title = rec.title.lower()

        # Relevance to research state
        state_score = 0.0
        for gap in state.gaps:
            tokens = self.recommender._clean_topics([gap.description])
            for t in tokens:
                if t in haystack:
                    state_score += 0.25 if t in title else 0.12

        # Relevance to profile/topics
        profile_score = 0.0
        for t in topics:
            tl = t.lower().strip()
            if tl in haystack:
                profile_score += 0.2 if tl in title else 0.1
            else:
                sub_tokens = [w for w in self.recommender._clean_topics([tl]) if len(w) >= 4]
                hits = [w for w in sub_tokens if w in haystack]
                if sub_tokens and len(hits) / len(sub_tokens) >= 0.5:
                    profile_score += 0.08 * (len(hits) / len(sub_tokens))

        # Novelty: penalize already seen papers in related_papers
        novelty = 1.0
        if rec.arxiv_id and any(rec.arxiv_id.lower() == p.lower() for p in state.related_papers):
            novelty = 0.3

        # Source bias: knowledge_base and episode get small boost for proven relevance
        source_bias = {"knowledge_base": 0.05, "episode": 0.05, "arxiv": 0.0}.get(rec.source, 0.0)

        score = state_score + profile_score + source_bias
        score *= novelty
        score = min(round(score, 2), 1.0)

        # Generate explanation
        rec.reason = self._explain(rec, state)
        rec.suggested_action = "Read abstract" if rec.source == "arxiv" else "Check related paper"
        if state.gaps:
            rec.linked_gap = state.gaps[0].gap_id
        return score

    def _explain(self, rec: Recommendation, state: ResearchState) -> str:
        if state.gaps:
            return f"May address knowledge gap: {state.gaps[0].description}"
        if rec.source == "arxiv":
            return "Matches your research interests and current task"
        if rec.source == "knowledge_base":
            return "Related content found in your local knowledge base"
        return "Previously appeared in a similar episode"
