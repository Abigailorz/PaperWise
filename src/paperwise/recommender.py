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

    def get_research_topics(self, user_id: str = "default",
                            extra: list[str] = None) -> list[str]:
        """从记忆卡 / 环境变量 / 显式参数提取研究方向。"""
        topics: list[str] = []
        if extra:
            topics.extend(extra)

        env = os.environ.get("PAPERWISE_RESEARCH_FIELDS", "")
        if env:
            topics.extend(t.strip() for t in env.split(",") if t.strip())

        if self.memory:
            for card in self.memory.query(limit=100):
                if card.category in ("preference", "fact", "knowledge"):
                    for value in card.data.values():
                        s = str(value)
                        # 支持 JSON 数组（如 ["3DGS", "Agent"]）与分隔符拼接
                        if s.startswith("["):
                            try:
                                topics.extend(
                                    str(t) for t in json.loads(s) if str(t).strip()
                                )
                                continue
                            except (json.JSONDecodeError, TypeError):
                                pass
                        for piece in re.split(r"[、，,;；/]", s):
                            piece = piece.strip()
                            if 2 <= len(piece) <= 80:
                                topics.append(piece)

        seen: set[str] = set()
        result: list[str] = []
        for t in topics:
            t = t.strip()
            low = t.lower()
            if t and low not in seen:
                seen.add(low)
                result.append(t)
        return result[:10]

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
        """相关性评分：整词命中权重高于部分 token 命中，标题权重高于摘要。"""
        haystack = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
        title = paper.get("title", "").lower()
        matched = []
        score = 0.0
        for topic in topics:
            tl = topic.lower().strip()
            if not tl:
                continue
            if tl in haystack:
                matched.append(topic)
                score += 0.6 if tl in title else 0.35
            else:
                # 部分匹配：方向中的关键词 token 命中（如 "Splatting"）
                tokens = [
                    w for w in re.split(r"[^a-z0-9]+", tl)
                    if len(w) >= 3 and w not in STOPWORDS
                ]
                hits = [w for w in tokens if w in haystack]
                if hits:
                    matched.append(topic)
                    score += 0.25 if any(w in title for w in hits) else 0.15
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
        topics = self.get_research_topics(user_id, topics)

        cache = self._load_cache(user_id)
        if (use_cache and cache and cache.get("papers")
                and time.time() - cache.get("ts", 0) < self.CACHE_TTL_SECONDS):
            return {
                "topics": topics,
                "papers": cache.get("papers", [])[:limit],
                "cached": True,
                "ts": cache.get("ts"),
            }

        if not topics:
            return {
                "topics": [], "papers": [], "cached": False,
                "reason": "no_topics",
            }

        try:
            papers = await self.fetch_recent_papers(topics, max_results=max_results, days=days)
        except Exception as e:
            logging.getLogger("paperwise").warning(f"arXiv fetch failed: {e}")
            return {"topics": topics, "papers": [], "cached": False,
                    "reason": f"fetch_error: {type(e).__name__}"}

        scored = []
        for paper in papers:
            s = self.score_paper(paper, topics)
            if s["score"] > 0 and paper.get("arxiv_id"):
                paper.update(s)
                scored.append(paper)
        scored.sort(key=lambda x: x["score"], reverse=True)

        if scored:
            self._save_cache(user_id, {"ts": time.time(), "papers": scored})
        return {
            "topics": topics,
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
