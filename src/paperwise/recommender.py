"""主动论文推荐 — arXiv 检索 + 相关性评分 + 缓存。

对应 spec S6.4：检测到新论文时，自动评估与用户的关联度，
高关联度论文主动推送。方向来源：用户记忆卡 / 环境变量 / 显式传入。
"""

import asyncio
import html
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from paperwise.parsers.arxiv import extract_arxiv_id


ARXIV_API = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_CATEGORIES = "cs.CV,cs.LG,cs.AI,cs.CL,cs.MA,cs.RO,stat.ML"

STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "with",
    "to", "from", "using", "based", "towards", "toward", "via", "into",
    "over", "under", "by", "at", "is", "are", "we", "our", "their",
    "this", "that", "new", "novel", "paper", "method", "model",
}

TOPIC_STOPWORDS = STOPWORDS | {
    "using", "based", "towards", "toward", "novel", "method", "methods",
    "approach", "framework", "paper", "model", "models", "learning",
    "deep", "neural", "network", "networks", "training", "efficient",
    "effective", "large", "proposed", "propose", "different", "various",
    "several", "results", "show", "shows", "state", "art", "high", "low",
    "without", "can", "use", "used", "one", "two", "three",
}

SCORE_GENERIC_TERMS = STOPWORDS | {
    "language", "structure", "field", "motion", "model", "models",
    "method", "methods", "approach", "framework", "system", "systems",
    "data", "image", "images", "video", "text", "real", "time",
    "learning", "deep", "neural", "network", "networks", "training",
    "based", "using", "via", "toward", "towards", "efficient", "effective",
    "large", "proposed", "different", "various", "several", "results",
    "show", "shows", "state", "art", "high", "low", "without", "can",
    "use", "used", "new", "novel", "paper", "one", "two", "three",
    "recognition", "understanding", "prediction", "analysis", "survey",
    "review", "generation", "representation", "detection", "classification",
    "render", "rendering", "segmentation", "feature", "features",
}


def _topic_tokens(text: str) -> list[str]:
    """Lowercase, keep alnum/hyphen tokens >=3 chars, drop stopwords/digits."""
    out: list[str] = []
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", str(text or "").lower()):
        if w in TOPIC_STOPWORDS or w.isdigit():
            continue
        out.append(w)
    return out


def extract_paper_topics(title: str = "", abstract: str = "",
                         keywords: str = "") -> list[str]:
    """Extract research topic phrases from a paper (keywords + title bigrams +
    abstract terms). Used to infer user interests without manual input."""
    topics: list[str] = []
    for kw in re.split(r"[;,\n]", keywords or ""):
        kw = kw.strip()
        if 2 <= len(kw) <= 60:
            topics.append(kw)

    toks = _topic_tokens(title)
    for a, b in zip(toks, toks[1:]):
        topics.append(f"{a} {b}")
    topics.extend(toks)

    if abstract:
        from collections import Counter
        freq = Counter(_topic_tokens(abstract[:3000]))
        for w, _ in freq.most_common(8):
            topics.append(w)

    return PaperRecommender._clean_topics(topics)


def _clean(text: Optional[str]) -> str:
    """清理 XML 文本中的换行与空白。"""
    if not text:
        return ""
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


class PaperRecommender:
    """论文推荐器。"""

    CACHE_TTL_SECONDS = 6 * 3600  # 6 小时缓存

    def __init__(self, workspace: Path, memory=None, llm_client=None):
        self.workspace = Path(workspace)
        self.memory = memory
        self.llm = llm_client
        self._cache_dir = self.workspace / ".paperwise" / "recommend"

    # ══════════ 研究方向提取 ══════════

    # 研究方向相关的记忆 key 信号（用于从记忆自动推断，而非要求手动填写）
    RESEARCH_KEY_HINTS = (
        "研究方向", "研究领域", "研究兴趣", "研究课题",
        "research_field", "research_fields", "research_interest", "research_topic",
        "field", "domain", "interest", "topic",
    )

    def get_research_topics(self, user_id: str = "default",
                            extra: list[str] = None) -> list[str]:
        """从记忆自动提取研究方向（优先结构化字段，其次关键词信号）。"""
        topics: list[str] = []
        if extra:
            topics.extend(str(t).strip() for t in extra if str(t).strip())

        # 环境变量仅作为兜底，不再要求用户手动填写
        env = os.environ.get("PAPERWISE_RESEARCH_FIELDS", "")
        if env:
            topics.extend(t.strip() for t in env.split(",") if t.strip())

        if self.memory:
            for card in self.memory.query(limit=200):
                if card.category not in ("preference", "fact", "knowledge", "experience"):
                    continue
                for key, value in card.data.items():
                    low_key = str(key).lower()
                    if low_key in ("research_fields", "research_field",
                                   "research_interest", "research_topic") \
                            or any(h in low_key for h in self.RESEARCH_KEY_HINTS):
                        topics.extend(self._split_topics(value))
        return self._clean_topics(topics)

    @staticmethod
    def _split_topics(s) -> list[str]:
        """把单个值拆成若干方向（支持 JSON 数组与常见分隔符）。"""
        s = str(s or "").strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                items = json.loads(s)
                if isinstance(items, list):
                    return [str(t).strip() for t in items if str(t).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
        return [p.strip() for p in re.split(r"[、，,;；/|\n]", s) if p.strip()]

    @staticmethod
    def _clean_topics(topics) -> list[str]:
        """去重、去噪、去掉“研究方向是/为”等前缀。"""
        seen: set[str] = set()
        stop = {"研究", "方向", "领域", "research", "field", "interest", "topic"}
        result: list[str] = []
        for t in topics:
            t = re.sub(
                r"^(?:我的)?(?:研究(?:方向|领域|兴趣)|research (?:direction|field|interest))?"
                r"(?:是|为|:：)?\s*", "", str(t).strip(), flags=re.I,
            )
            t = t.strip(" ：:，,、")
            low = t.lower()
            if not t or len(t) < 2 or len(t) > 80 or low in seen or low in stop:
                continue
            seen.add(low)
            result.append(t)
        return result[:10]

    @staticmethod
    def _card_timestamp(card) -> float:
        """Safely parse a MemoryCard timestamp into a unix timestamp."""
        try:
            return datetime.fromisoformat(card.timestamp).timestamp()
        except (ValueError, TypeError):
            return 0.0

    def build_interest_profile(self, user_id: str = "default",
                               limit: int = 12) -> list[dict]:
        """Aggregate weighted research-interest signals from memory.

        Sources and weights:
        - declared     (preference research_fields): 1.0
        - paper        (knowledge cards tagged paper/interest_signal): 0.8
        - conversation (fact/experience research hints): 0.55

        Paper/conversation signals decay with a 30-day half-life so recent
        activity dominates. No manual research-direction input is required.
        """
        now = time.time()
        acc: dict[str, dict] = {}

        def add(topic, weight, source, confidence, ts):
            topic = str(topic or "").strip()
            if not topic or len(topic) < 2:
                return
            key = topic.lower()
            e = acc.setdefault(key, {
                "topic": topic, "weight": 0.0, "confidence": 0.0,
                "sources": set(), "count": 0, "last_seen": 0.0,
            })
            age_days = max(0.0, (now - ts) / 86400) if ts else 0.0
            decay = 0.5 ** (age_days / 30.0)
            e["weight"] += weight * decay
            e["confidence"] = max(e["confidence"], float(confidence or 0.0))
            e["sources"].add(source)
            e["count"] += 1
            e["last_seen"] = max(e["last_seen"], ts or 0.0)

        # Environment fallback (server-side config, not user manual input).
        for t in self._split_topics(os.environ.get("PAPERWISE_RESEARCH_FIELDS", "")):
            add(t, 1.0, "declared", 0.9, now)

        if not self.memory:
            return []

        for card in self.memory.query(limit=300):
            ts = self._card_timestamp(card)
            cat = card.category
            tags = set(card.tags or [])
            data = card.data or {}

            # Paper interest signals.
            if (cat == "knowledge"
                    and ("paper" in tags or "interest_signal" in tags
                         or "topics" in data or "keywords" in data)):
                for key in ("topics", "keywords"):
                    for t in self._split_topics(data.get(key, "")):
                        add(t, 0.8, "paper", card.confidence, ts)
                continue

            # Declared + conversation research fields.
            for key, value in data.items():
                low = str(key).lower()
                if not (low in ("research_fields", "research_field",
                                "research_interest", "research_topic")
                        or any(h in low for h in self.RESEARCH_KEY_HINTS)):
                    continue
                source = "declared" if cat == "preference" else "conversation"
                weight = 1.0 if source == "declared" else 0.55
                for t in self._split_topics(value):
                    add(t, weight, source, card.confidence, ts)

        ranked = sorted(
            acc.values(),
            key=lambda x: (x["weight"], x["count"], x["last_seen"]),
            reverse=True,
        )
        max_w = max((x["weight"] for x in ranked), default=1.0) or 1.0
        out: list[dict] = []
        for e in ranked[:limit]:
            out.append({
                "topic": e["topic"],
                "weight": round(e["weight"] / max_w, 3),
                "confidence": round(e["confidence"], 2),
                "sources": sorted(e["sources"]),
                "count": e["count"],
                "last_seen": (
                    datetime.fromtimestamp(e["last_seen"]).isoformat()
                    if e["last_seen"] else ""
                ),
            })
        return out

    def remember_paper(self, title: str = "", abstract: str = "",
                       keywords: str = "", arxiv_id: str = "") -> list[str]:
        """Store a paper's topics into memory as an interest signal."""
        topics = extract_paper_topics(title, abstract, keywords)
        if not topics:
            return []
        if self.memory:
            try:
                self.memory.remember(
                    category="knowledge",
                    data={
                        "title": title or "",
                        "arxiv_id": arxiv_id or "",
                        "topics": json.dumps(topics, ensure_ascii=False),
                    },
                    backstory=f"用户解读了论文《{title}》，用于自动学习研究兴趣",
                    confidence=0.7,
                    tags=["paper", "interest_signal"],
                )
            except Exception:
                pass
        return topics

    # ══════════ arXiv 检索 ══════════

    async def fetch_recent_papers(self, topics: list[str],
                                  max_results: int = 30,
                                  days: int = 7) -> list[dict]:
        """拉取近期论文：arXiv API → arXiv 列表页 → Semantic Scholar。"""
        try:
            return await self._fetch_arxiv(topics, max_results, days)
        except Exception as e:
            logging.getLogger("paperwise").warning(
                f"arXiv API failed ({type(e).__name__}: {e}); "
                f"falling back to arXiv listing pages")
        try:
            return await self._fetch_arxiv_listing(topics, max_results)
        except Exception as e:
            logging.getLogger("paperwise").warning(
                f"arXiv listing failed ({type(e).__name__}: {e}); "
                f"falling back to Semantic Scholar")
        return await self._fetch_semanticscholar(topics, max_results, days)

    async def _fetch_arxiv_listing(self, topics: list[str],
                                   max_results: int = 30) -> list[dict]:
        """从 arXiv recent 列表页抓取新提交论文（含标题，无摘要）。"""
        import httpx
        categories = [
            c.strip() for c in os.environ.get(
                "PAPERWISE_ARXIV_CATEGORIES", DEFAULT_CATEGORIES,
            ).split(",") if c.strip()
        ]
        papers: list[dict] = []
        seen: set[str] = set()
        title_re = re.compile(
            r"<div class=['\"]list-title[^>]*>.*?Title:</span>\s*(.*?)</div>",
            re.S,
        )
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            for cat in categories:
                if len(papers) >= max_results:
                    break
                try:
                    resp = await client.get(
                        f"https://arxiv.org/list/{cat}/recent", timeout=25)
                    if resp.status_code == 429:
                        await asyncio.sleep(5)
                        resp = await client.get(
                            f"https://arxiv.org/list/{cat}/recent", timeout=25)
                    if resp.status_code != 200:
                        continue
                except Exception:
                    continue
                for chunk in resp.text.split("<dt>")[1:]:
                    id_match = re.search(
                        r'href\s*=\s*"/abs/(\d{4}\.\d{4,5}(?:v\d+)?)"', chunk)
                    if not id_match:
                        continue
                    arxiv_id = id_match.group(1)
                    if arxiv_id in seen:
                        continue
                    seen.add(arxiv_id)
                    title_match = title_re.search(chunk)
                    title = _clean(
                        re.sub(r"<[^>]+>", "", title_match.group(1))
                    ) if title_match else ""
                    if not title:
                        continue
                    papers.append({
                        "arxiv_id": arxiv_id,
                        "title": title,
                        "summary": "",
                        "url": f"https://arxiv.org/abs/{arxiv_id}",
                        "authors": [],
                        "published": "",
                    })
                await asyncio.sleep(1.5)  # 尊重 arXiv 站点限流
        return papers[:max_results]

    async def _fetch_arxiv(self, topics: list[str],
                           max_results: int = 30,
                           days: int = 7) -> list[dict]:
        """从 arXiv API 拉取近期论文。"""
        if not topics:
            return []
        query = " OR ".join(f'all:"{t}"' for t in topics[:5])

        import httpx
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            params = {
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            }
            resp = await client.get(ARXIV_API, params=params)
            # arXiv 对共享出口 IP 限流（429），快速重试一次后交给回退源
            for attempt in range(2):
                if resp.status_code not in (429, 500, 502, 503):
                    break
                try:
                    delay = float(resp.headers.get("Retry-After", 0)) or 5 + attempt * 3
                except (TypeError, ValueError):
                    delay = 5 + attempt * 3
                logging.getLogger("paperwise").warning(
                    f"arXiv rate limited ({resp.status_code}), retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                resp = await client.get(ARXIV_API, params=params)
            resp.raise_for_status()

        root = ET.fromstring(resp.text)
        cutoff = datetime.utcnow() - timedelta(days=days)
        papers = []
        for entry in root.findall("atom:entry", NS):
            published = _clean(entry.findtext("atom:published", "", NS))
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                pub_dt = datetime.utcnow()
            if pub_dt.replace(tzinfo=None) < cutoff:
                continue
            url = _clean(entry.findtext("atom:id", "", NS))
            papers.append({
                "arxiv_id": extract_arxiv_id(url) or "",
                "title": _clean(entry.findtext("atom:title", "", NS)),
                "summary": _clean(entry.findtext("atom:summary", "", NS))[:600],
                "url": url,
                "authors": [
                    _clean(a.findtext("atom:name", "", NS))
                    for a in entry.findall("atom:author", NS)
                ][:5],
                "published": published[:10],
            })
        return papers

    async def _fetch_semanticscholar(self, topics: list[str],
                                     max_results: int = 30,
                                     days: int = 7) -> list[dict]:
        """从 Semantic Scholar API 拉取近期论文（arXiv 限流时的回退源）。"""
        if not topics:
            return []

        import httpx
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        api = "https://api.semanticscholar.org/graph/v1/paper/search"

        async def query(client: httpx.AsyncClient, topic: str):
            params = {
                "query": topic,
                "fields": "title,abstract,externalIds,year,publicationDate,authors,url",
                "limit": 10,
            }
            resp = await client.get(api, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(3)
                resp = await client.get(api, params=params)
            if resp.status_code != 200:
                return []
            return resp.json().get("data", [])

        papers: list[dict] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            for topic in topics[:5]:
                try:
                    items = await query(client, topic)
                except Exception:
                    items = []
                for p in items:
                    pub = p.get("publicationDate") or ""
                    if pub and pub < cutoff:
                        continue
                    arxiv_id = (p.get("externalIds") or {}).get("ArXiv") or ""
                    key = arxiv_id or p.get("paperId") or p.get("title", "")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    papers.append({
                        "arxiv_id": arxiv_id,
                        "title": p.get("title", ""),
                        "summary": (p.get("abstract") or "")[:600],
                        "url": (f"https://arxiv.org/abs/{arxiv_id}"
                                if arxiv_id else p.get("url", "")),
                        "authors": [
                            a.get("name", "") for a in (p.get("authors") or [])
                        ][:5],
                        "published": pub,
                    })
                await asyncio.sleep(1.0)  # 尊重未认证限流
        return papers[:max_results]

    # ══════════ 相关性评分 ══════════

    def score_paper(self, paper: dict, topics: list[str]) -> dict:
        """相关性评分：整词/短语命中权重高于部分 token 命中，标题权重高于摘要。

        部分 token 匹配只对「具体词」生效，且要求多数 token 命中，避免
        language / structure 这类泛化词造成跨领域误推。
        """
        haystack = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
        title = paper.get("title", "").lower()
        matched = []
        score = 0.0
        for topic in topics:
            tl = topic.lower().strip()
            if not tl:
                continue

            # 整词/整短语命中（权重最高）；单 token 用词边界，避免 "sam" 命中 "sample"
            if " " in tl:
                whole_hit = tl in haystack
                title_hit = tl in title
            else:
                whole_hit = re.search(rf"\b{re.escape(tl)}\b", haystack) is not None
                title_hit = re.search(rf"\b{re.escape(tl)}\b", title) is not None

            if whole_hit:
                matched.append(topic)
                score += 0.6 if title_hit else 0.35
            else:
                tokens = [
                    w for w in re.split(r"[^a-z0-9]+", tl)
                    if len(w) >= 4 and w not in SCORE_GENERIC_TERMS
                ]
                if not tokens:
                    continue
                hits = [w for w in tokens if w in haystack]
                ratio = len(hits) / len(tokens)
                if hits and ratio >= 0.5:
                    matched.append(topic)
                    score += (0.25 if any(w in title for w in hits) else 0.15) * ratio
        score = min(round(score, 2), 1.0)
        return {
            "score": score,
            "matched": matched[:5],
            "reason": f"匹配方向：{'、'.join(matched[:3])}" if matched else "无显式方向匹配",
        }

    # ══════════ 推荐主流程 ══════════

    async def recommend(self, user_id: str = "default",
                        topics: list[str] = None, limit: int = 5,
                        days: int = 7, max_results: int = 30,
                        use_cache: bool = True) -> dict:
        """返回按相关性排序的推荐论文。"""
        profile = self.build_interest_profile(user_id)
        extra = self._clean_topics(topics or [])
        topics = [p["topic"] for p in profile]
        seen = {t.lower() for t in topics}
        for t in extra:
            if t.lower() not in seen:
                topics.append(t)
                seen.add(t.lower())
        profile_key = "|".join(sorted({t.lower() for t in topics}))

        cache = self._load_cache(user_id)
        if (use_cache and cache and cache.get("papers")
                and cache.get("profile_key") == profile_key
                and time.time() - cache.get("ts", 0) < self.CACHE_TTL_SECONDS):
            return {
                "topics": topics,
                "profile": profile,
                "papers": cache.get("papers", [])[:limit],
                "cached": True,
                "ts": cache.get("ts"),
            }

        if not topics:
            return {
                "topics": [], "profile": profile,
                "papers": [], "cached": False,
                "reason": "no_topics",
            }

        try:
            papers = await self.fetch_recent_papers(topics, max_results=max_results, days=days)
        except Exception as e:
            logging.getLogger("paperwise").warning(f"arXiv fetch failed: {e}")
            return {"topics": topics, "profile": profile,
                    "papers": [], "cached": False,
                    "reason": f"fetch_error: {type(e).__name__}"}

        scored = []
        for paper in papers:
            s = self.score_paper(paper, topics)
            if s["score"] > 0 and paper.get("arxiv_id"):
                paper.update(s)
                scored.append(paper)
        scored.sort(key=lambda x: x["score"], reverse=True)

        if scored:
            self._save_cache(user_id, {
                "ts": time.time(), "papers": scored,
                "profile_key": profile_key,
            })
        return {
            "topics": topics,
            "profile": profile,
            "papers": scored[:limit],
            "cached": False,
            "ts": time.time(),
        }

    # ══════════ 缓存 ══════════

    def _cache_path(self, user_id: str) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir / f"{user_id}.json"

    def _load_cache(self, user_id: str) -> Optional[dict]:
        try:
            return json.loads(self._cache_path(user_id).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_cache(self, user_id: str, data: dict) -> None:
        try:
            self._cache_path(user_id).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
