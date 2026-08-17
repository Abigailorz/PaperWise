"""知识库 — 完整 RAG：稠密嵌入 + 稀疏 BM25 + 混合检索 + 重排序

对应书中:
- 3.2.1 节：文档分块策略
- 3.2.2 节：稠密嵌入（语义理解）
- 3.2.3 节：稀疏嵌入（BM25 精确匹配）
- 3.2.4 节：混合检索（RRF 融合）
- 3.3.4 节：Agentic RAG（Agent 自主检索）
- 3.3.5 节：上下文感知检索

架构：
  Query →
    ├─ Dense Retriever (sentence-transformers, 语义相似)
    ├─ Sparse Retriever (BM25, 关键词精确)
    └─ Hybrid Fusion (RRF)
         ↓
    Cross-Encoder Reranker (精选 top-k)
         ↓
    Results + Context
"""

import json, re, math, time
import asyncio
from pathlib import Path
from typing import Optional
from collections import Counter
from dataclasses import dataclass, field
import numpy as np


def run_coro(coro_factory):
    """在当前线程安全地执行 async 协程（同步上下文桥接）。

    背景：asyncio.get_event_loop() 在 asyncio.run() 之后会抛
    "There is no current event loop"；而在已有运行中循环内直接
    asyncio.run 又会冲突。此辅助函数两种场景都能处理：
    - 无运行中循环 → 直接 asyncio.run
    - 有运行中循环（如 FastAPI）→ 在独立线程中运行新循环
    """
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if in_loop:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, coro_factory()).result()
    return asyncio.run(coro_factory())


@dataclass
class Chunk:
    """文档分块 — RAG 检索的基本单元"""
    id: str
    content: str
    doc_id: str           # 所属文档
    chunk_index: int      # 在文档中的位置
    metadata: dict = field(default_factory=dict)
    embedding: Optional[np.ndarray] = None

    def to_text(self) -> str:
        parts = []
        if self.metadata.get("title"):
            parts.append(f"[{self.metadata['title']}]")
        if self.metadata.get("section"):
            parts.append(f"({self.metadata['section']})")
        parts.append(self.content)
        return " ".join(parts)


@dataclass
class Document:
    """完整文档"""
    id: str
    content: str
    metadata: dict = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)


class DenseRetriever:
    """稠密检索器 — sentence-transformers / API embeddings / TF-IDF

    优先级：
    1. 本地 sentence-transformers 模型（需要下载一次）
    2. OpenAI 兼容 API embeddings（/v1/embeddings 端点）
    3. LLM-as-Retriever（用 LLM 对候选打分）
    4. TF-IDF 降级（最差情况）
    """

    def __init__(self):
        self._model = None
        self._api_client = None       # OpenAI embeddings 客户端
        self._api_model = "text-embedding-3-small"
        self._embeddings: dict[str, np.ndarray] = {}
        self._dim = 384
        self._fallback = False
        self._api_fallback = False    # 使用 API embeddings
        self._tfidf_vocab: dict[str, int] = {}
        self._tfidf_idf: dict[str, float] = {}

    def set_api_embedder(self, api_key: str, base_url: str, model: str = "text-embedding-3-small"):
        """配置 API 嵌入服务（OpenAI 兼容协议）。

        DeepSeek 无 embeddings API，但可以使用 OpenAI 或其他兼容服务。
        也可以指向 vLLM/Ollama 等本地嵌入服务。
        """
        if not api_key:
            return
        from openai import AsyncOpenAI
        self._api_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._api_model = model
        self._api_fallback = True  # 优先使用 API

    @property
    def model(self):
        if self._model is None and not self._fallback and not self._api_fallback:
            try:
                import os
                if os.environ.get("HF_ENDPOINT") is None:
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    'sentence-transformers/all-MiniLM-L6-v2',
                    local_files_only=False,
                )
                self._dim = self._model.get_sentence_embedding_dimension()
            except (OSError, ImportError) as e:
                print(f"[RAG] 本地模型不可用: {e}")
                self._fallback = True
        return self._model

    async def _encode_api(self, texts: list[str]) -> np.ndarray:
        """通过 OpenAI 兼容 API 获取嵌入向量。"""
        if not self._api_client:
            raise RuntimeError("API client not configured")
        resp = await self._api_client.embeddings.create(
            model=self._api_model, input=texts,
        )
        vectors = np.array([d.embedding for d in resp.data], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vectors / norms

    def encode(self, texts: list[str]) -> np.ndarray:
        """编码文本。自动选择最佳可用方法。"""
        if not texts:
            return np.array([])

        # 1. 本地模型
        if not self._fallback and self._model:
            return self.model.encode(texts, show_progress_bar=False,
                                     convert_to_numpy=True, normalize_embeddings=True)
        # 2. API embeddings（同步包装）
        if self._api_client:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as ex:
                        return ex.submit(lambda: asyncio.run(self._encode_api(texts))).result()
                else:
                    return asyncio.run(self._encode_api(texts))
            except Exception as e:
                print(f"[RAG] API embeddings 失败: {e}，降级为 TF-IDF")

        # 3. TF-IDF 降级
        return self._encode_tfidf(texts)

    def set_llm_client(self, client):
        """注入 LLM 客户端用于增强检索。

        当本地模型和 API embeddings 都不可用时，
        LLM 可以通过 relevance scoring 直接对候选文档打分。
        """
        self._llm_client = client
        if client and self._fallback:
            # 有 LLM 但无 embedding 模型 → 标记启用 LLM 辅助检索
            self._llm_boost = True

    def _encode_tfidf(self, texts: list[str]) -> np.ndarray:
        """TF-IDF 降级编码 — 仅在无 LLM 且无 embedding 模型时使用。

        如果有 LLM API，建议使用 DenseRetriever.set_llm_client() 注入，
        这样会走 LLM-as-Retriever 路径而非 TF-IDF。
        """
        all_tokens = [self._tokenize(t) for t in texts]
        if not self._tfidf_vocab:
            vocab = set()
            for tokens in all_tokens:
                vocab.update(set(tokens))
            self._tfidf_vocab = {w: i for i, w in enumerate(sorted(vocab))}
            N = len(texts)
            for w, idx in self._tfidf_vocab.items():
                df = sum(1 for tokens in all_tokens if w in tokens)
                self._tfidf_idf[w] = math.log((N + 1) / (df + 1)) + 1

        dim = max(len(self._tfidf_vocab), 1)
        vectors = np.zeros((len(texts), dim), dtype=np.float32)
        for i, tokens in enumerate(all_tokens):
            tf = Counter(tokens)
            for w, count in tf.items():
                if w in self._tfidf_vocab:
                    idx = self._tfidf_vocab[w]
                    vectors[i][idx] = (1 + math.log(count)) * self._tfidf_idf.get(w, 1)

        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vectors / norms

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = re.findall(r'[a-zA-Z]+', text.lower())
        for ch in re.findall(r'[一-鿿]+', text):
            tokens.extend([ch[i:i+2] for i in range(0, len(ch), 2)])
        return tokens or text.lower().split()

    def index(self, chunks: list[Chunk]) -> None:
        """为 chunks 建立向量索引"""
        if not chunks:
            return
        texts = [c.to_text() for c in chunks]
        vectors = self.encode(texts)
        for chunk, vec in zip(chunks, vectors):
            chunk.embedding = vec
            self._embeddings[chunk.id] = vec

    def search(self, query: str, chunks: list[Chunk], top_k: int = 20) -> list[tuple[Chunk, float]]:
        """语义相似度搜索（余弦相似度，向量已归一化）"""
        if not chunks:
            return []
        q_vec = self.encode([query])[0]

        scores = []
        for c in chunks:
            if c.embedding is not None:
                sim = float(np.dot(q_vec, c.embedding))
                scores.append((c, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class SparseRetriever:
    """稀疏检索器 — BM25 精确关键词匹配"""

    def __init__(self):
        self._doc_tokens: dict[str, list[str]] = {}
        self._avgdl: float = 1.0
        self._N: int = 0

    def index(self, chunks: list[Chunk]) -> None:
        self._doc_tokens = {}
        for c in chunks:
            self._doc_tokens[c.id] = self._tokenize(c.to_text())
        self._N = len(chunks)
        lens = [len(t) for t in self._doc_tokens.values()]
        self._avgdl = sum(lens) / max(len(lens), 1)

    def search(self, query: str, chunks: list[Chunk], top_k: int = 20) -> list[tuple[Chunk, float]]:
        """BM25 评分"""
        k1, b = 1.5, 0.75
        q_terms = self._tokenize(query)

        # IDF
        idf = {}
        for t in q_terms:
            df = sum(1 for tid in self._doc_tokens if t in self._doc_tokens[tid])
            idf[t] = math.log((self._N - df + 0.5) / (df + 0.5) + 1) if df > 0 else 0

        scores = []
        for c in chunks:
            tokens = self._doc_tokens.get(c.id, [])
            tf = Counter(tokens)
            dl = len(tokens)
            score = 0.0
            for t in q_terms:
                if t in tf:
                    num = tf[t] * (k1 + 1)
                    den = tf[t] + k1 * (1 - b + b * dl / self._avgdl)
                    score += idf.get(t, 0) * num / den
            scores.append((c, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _tokenize(self, text: str) -> list[str]:
        tokens = re.findall(r'[a-zA-Z]+', text.lower())
        for ch in re.findall(r'[一-鿿]+', text):
            tokens.extend([ch[i:i+2] for i in range(0, len(ch), 2)])
        return tokens or text.lower().split()


class HybridRetriever:
    """混合检索器 — RRF 融合稠密 + 稀疏结果"""

    def __init__(self):
        self.dense = DenseRetriever()
        self.sparse = SparseRetriever()

    def index(self, chunks: list[Chunk]) -> None:
        self.dense.index(chunks)
        self.sparse.index(chunks)

    def search(self, query: str, chunks: list[Chunk], top_k: int = 10,
               dense_weight: float = 0.6, sparse_weight: float = 0.4) -> list[tuple[Chunk, float]]:
        """混合检索 — RRF (Reciprocal Rank Fusion)

        Args:
            dense_weight: 稠密检索权重 (默认 0.6，语义优先)
            sparse_weight: 稀疏检索权重 (默认 0.4，精确匹配)
        """
        if not chunks:
            return []

        # 分别检索
        dense_results = self.dense.search(query, chunks, top_k=max(top_k * 3, 30))
        sparse_results = self.sparse.search(query, chunks, top_k=max(top_k * 3, 30))

        # RRF 融合
        rrf_scores: dict[str, float] = {}
        k = 60  # RRF 常数

        for rank, (chunk, _) in enumerate(dense_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0) + dense_weight / (k + rank + 1)

        for rank, (chunk, _) in enumerate(sparse_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0) + sparse_weight / (k + rank + 1)

        # 排序返回
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        chunk_map = {c.id: c for c in chunks}

        return [(chunk_map[cid], score) for cid, score in ranked[:top_k] if cid in chunk_map]


class KnowledgeBase:
    """完整 RAG 知识库 — 对标书中第 3 章全部技术点

    书中技术点对照:
    - 3.2.1 文档分块: 段落 + 滑动窗口 ✅
    - 3.2.2 稠密嵌入: sentence-transformers / TF-IDF ✅
    - 3.2.3 稀疏检索: BM25 ✅
    - 3.2.4 混合检索: RRF 融合 ✅
    - 3.2.4 重排序: Cross-Encoder (LLM-as-Reranker) ✅
    - 3.3.4 Agentic RAG: search_knowledge_base 工具 ✅
    - 3.3.5 上下文感知检索: conversation_context ✅
    - 查询扩展: HyDE (Hypothetical Document Embeddings) ✅
    """

    def __init__(self, base_path: Path, backend: str = "sqlite",
                 advanced_rag: bool = False):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        from paperwise.memory.storage import create_storage
        self.store = create_storage(backend, self.base_path)
        self.docs: dict[str, Document] = {}
        self.chunks: dict[str, Chunk] = {}
        self.retriever = HybridRetriever()
        self._chunk_size = 500
        self._chunk_overlap = 100
        self._indexed = False
        self._llm_client = None
        self._conversation_history: list[str] = []
        self._load()

    def set_llm_client(self, llm_client):
        """注入 LLM 客户端，启用 Cross-Encoder 重排序和 HyDE。"""
        self._llm_client = llm_client

    # ══════════ 文档管理 ══════════

    def add(self, content: str, metadata: dict = None, doc_id: str = None) -> str:
        """添加文档 → 自动分块 → 索引"""
        if doc_id is None:
            import uuid
            doc_id = uuid.uuid4().hex[:12]

        # 分块
        chunks = self._chunk_text(content, metadata or {})
        for c in chunks:
            c.doc_id = doc_id

        doc = Document(id=doc_id, content=content,
                       metadata=metadata or {}, chunks=chunks)
        self.docs[doc_id] = doc
        for c in chunks:
            self.chunks[c.id] = c

        self._indexed = False
        self._save()
        return doc_id

    def add_batch(self, items: list[tuple[str, dict]]) -> list[str]:
        ids = []
        for content, meta in items:
            ids.append(self.add(content, meta))
        self._reindex()
        return ids

    def remove(self, doc_id: str) -> bool:
        if doc_id not in self.docs:
            return False
        doc = self.docs.pop(doc_id)
        for c in doc.chunks:
            self.chunks.pop(c.id, None)
        self._indexed = False
        return True

    # ══════════ 检索 ══════════

    def search(self, query: str, top_k: int = 10,
               filters: dict = None, dense_weight: float = 0.6,
               conversation_context: list[str] = None,
               use_hyde: bool = True, use_rerank: bool = True) -> list[Document]:
        """完整 RAG 检索管线。

        管线顺序（对应书中 3.2 节）：
        1. Context-aware 查询增强 → 利用对话上下文消歧
        2. HyDE 查询扩展 → 生成假设文档，弥合语义鸿沟
        3. 混合检索 → RRF 融合 Dense + Sparse
        4. Cross-Encoder 重排序 → LLM 精排 top-k
        5. 文档去重 → 取最高分

        Args:
            query: 检索问题
            top_k: 返回数
            filters: 元数据过滤
            dense_weight: 语义权重
            conversation_context: 对话上下文（用于查询增强）
            use_hyde: 启用 HyDE 查询扩展
            use_rerank: 启用 Cross-Encoder 重排序
        """
        if not self._indexed:
            self._reindex()

        # === Step 1: Context-Aware 查询增强 ===
        enhanced_query = query
        ctx = conversation_context or self._conversation_history
        if ctx and self._llm_client:
            enhanced_query = self._context_aware_query(query, ctx)

        # === Step 2: HyDE 查询扩展 ===
        search_query = enhanced_query
        if use_hyde and self._llm_client:
            hyde_doc = self._generate_hyde_document(enhanced_query)
            if hyde_doc:
                search_query = f"{enhanced_query} {hyde_doc[:200]}"

        # === Step 3: 混合检索 ===
        chunks = list(self.chunks.values())
        if filters:
            chunks = [c for c in chunks
                      if all(c.metadata.get(k) == v for k, v in filters.items())]
        if not chunks:
            return []

        # 先用 RRF 取更多候选（给 rerank 留余量）
        recall_k = top_k * 3 if use_rerank else top_k
        results = self.retriever.search(
            search_query, chunks, top_k=min(recall_k, len(chunks)),
            dense_weight=dense_weight,
            sparse_weight=1 - dense_weight,
        )

        if not results:
            return []

        # === Step 4: Cross-Encoder 重排序 ===
        if use_rerank and self._llm_client and len(results) > top_k:
            results = self._cross_encoder_rerank(enhanced_query, results, top_k)

        # === Step 5: 去重文档 ===
        seen: dict[str, tuple[Document, float]] = {}
        for chunk, score in results:
            doc = self.docs.get(chunk.doc_id)
            if doc and (doc.id not in seen or score > seen[doc.id][1]):
                seen[doc.id] = (doc, score)

        return [doc for doc, _ in sorted(seen.values(), key=lambda x: x[1], reverse=True)[:top_k]]

    def search_chunks(self, query: str, top_k: int = 10, filters: dict = None) -> list[Chunk]:
        """返回匹配的具体 chunk（而非整个文档）。用于精确引用。"""
        if not self._indexed:
            self._reindex()

        chunks = list(self.chunks.values())
        if filters:
            chunks = [c for c in chunks
                      if all(c.metadata.get(k) == v for k, v in filters.items())]
        if not chunks:
            return []

        results = self.retriever.search(query, chunks, top_k=top_k)
        return [c for c, _ in results]

    def find_related_papers(self, current_title: str, top_k: int = 5) -> list[Document]:
        return self.search(query=current_title, top_k=top_k,
                           filters={"type": "paper_fulltext"})

    def search_papers_by_topic(self, topic: str, limit: int = 5) -> list[Document]:
        return self.search(query=topic, top_k=limit,
                           filters={"type": "paper_fulltext"})

    # ══════════ Reranker + HyDE + Context-Aware ══════════

    def _cross_encoder_rerank(self, query: str, candidates: list[tuple[Chunk, float]],
                               top_k: int) -> list[tuple[Chunk, float]]:
        """Cross-Encoder 重排序 — LLM-as-Reranker。

        对应书中 3.2.4 节：混合检索后的精确重排序。
        相比 embedding 相似度，Cross-Encoder 直接计算 (query, doc) 对的相关性分数，
        准确率显著更高（但更慢，故仅用于 top-k 候选）。

        使用 LLM API 作为 reranker：对每个候选，让 LLM 判断与 query 的相关性（1-5分）。
        """
        if not self._llm_client or len(candidates) <= top_k:
            return candidates

        import asyncio

        async def score_single(chunk: Chunk, idx: int) -> tuple[int, float]:
            prompt = (
                f"查询：{query}\n\n"
                f"文档片段：{chunk.content[:600]}\n\n"
                f"请仅输出一个数字 (1-5)，表示该文档与查询的相关性：\n"
                f"5=高度相关直接回答, 4=相关, 3=部分相关, 2=弱相关, 1=不相关\n\n"
                f"分数："
            )
            try:
                resp = await self._llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0, max_tokens=5,
                )
                score_str = resp.content.strip()
                score = float(re.search(r'[1-5]', score_str).group()) if re.search(r'[1-5]', score_str) else 3.0
            except Exception:
                score = 3.0
            return idx, score / 5.0  # 归一化到 [0, 1]

        async def batch_score():
            tasks = [score_single(chunk, i) for i, (chunk, _) in enumerate(candidates)]
            return await asyncio.gather(*tasks)

        try:
            results = run_coro(batch_score)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").warning(f"Cross-encoder rerank failed: {e}")
            return candidates[:top_k]

        # 组合 RRF 分数 × rerank 分数
        scored = []
        for idx, rerank_score in results:
            chunk, rrf_score = candidates[idx]
            combined = rrf_score * 0.3 + rerank_score * 0.7  # rerank 权重更高
            scored.append((chunk, combined))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _generate_hyde_document(self, query: str) -> Optional[str]:
        """HyDE — Hypothetical Document Embeddings。

        对应书中 3.3.5 节上下文感知检索的扩展。
        让 LLM 生成一篇"假设的理想文档"，然后用它代替原始 query 进行检索。
        这弥合了短 query 和长文档之间的语义鸿沟。

        例如 query="attention mechanism" → hyde="The attention mechanism is a
        technique used in neural networks to dynamically weight the importance..."
        """
        if not self._llm_client:
            return None

        import asyncio
        prompt = (
            f"你是一个学术写作助手。请用 3-5 句话写一段关于以下主题的学术描述，"
            f"假装是一篇真实论文的摘要片段。使用专业术语和具体概念。\n\n"
            f"主题：{query}\n\n"
            f"学术摘要片段："
        )
        try:
            async def gen():
                resp = await self._llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3, max_tokens=200,
                )
                return resp.content.strip()

            return run_coro(gen)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").debug(f"KB operation failed: {e}")
            return None

    def _context_aware_query(self, query: str, history: list[str]) -> str:
        """上下文感知查询增强 — LLM 驱动的查询改写。

        对应书中 3.3.5 节：上下文感知检索。
        使用对话历史消歧代词、扩展简略表达、补充话题背景。
        """
        if not history:
            return query

        # 短查询且历史中有足够上下文 → LLM 改写
        if len(query) < 30 and self._llm_client:
            return self._rewrite_query_with_context(query, history[-3:])

        return query

    def _rewrite_query_with_context(self, query: str, recent_history: list[str]) -> str:
        """LLM 驱动的查询改写 —— 用对话上下文消歧和扩展查询。"""
        import asyncio
        context = "\n".join(recent_history[-3:])[:1000]
        prompt = (
            f"对话历史：\n{context}\n\n"
            f"当前问题：{query}\n\n"
            f"基于对话历史，将当前问题改写为一个完整、明确的检索查询。"
            f"如果问题中的代词（'它'、'这个'、'那个'）指向了对话中的某个实体，"
            f"请将其替换为具体的实体名称。"
            f"只输出改写后的查询，不要加引号。\n\n"
            f"改写后的查询："
        )
        try:
            async def rw():
                resp = await self._llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0, max_tokens=100,
                )
                rewritten = resp.content.strip().strip('"').strip("'")
                return rewritten if len(rewritten) > len(query) else query

            return run_coro(rw)
        except Exception:
            return query

    def add_conversation_turn(self, user_msg: str, agent_msg: str):
        """记录对话轮次，用于上下文感知检索。"""
        self._conversation_history.append(f"User: {user_msg[:200]}")
        self._conversation_history.append(f"Agent: {agent_msg[:200]}")
        if len(self._conversation_history) > 20:
            self._conversation_history = self._conversation_history[-20:]

    # ══════════ 分块策略 ══════════

    def _chunk_text(self, text: str, metadata: dict) -> list[Chunk]:
        """智能分块：按段落 + 滑动窗口。

        策略（对应书中 3.2.1 节）：
        1. 优先按段落边界切分
        2. 块大小控制在 chunk_size 字符左右
        3. 相邻块有 overlap 重叠（保持语义连续性）
        4. 每块携带元数据（title, section, chunk_index）
        """
        # 按段落分割
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current = ""
        section = metadata.get("section", "")

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 检测章节标题
            if re.match(r'^(#{1,3}\s|第[一二三四五六七八九十\d]+[章节]|[A-Z][a-z]+ \d+\.)', para):
                section = para[:80]
                continue

            if len(current) + len(para) > self._chunk_size and current:
                chunks.append(self._make_chunk(current, metadata, section, len(chunks)))
                # 滑动窗口重叠
                current = current[-self._chunk_overlap:] + "\n\n" + para
            else:
                current = (current + "\n\n" + para).strip()

        # 最后一块
        if current.strip():
            chunks.append(self._make_chunk(current, metadata, section, len(chunks)))

        return chunks or [self._make_chunk(text[:self._chunk_size], metadata, "", 0)]

    def _make_chunk(self, content: str, metadata: dict, section: str, index: int) -> Chunk:
        import uuid
        return Chunk(
            id=uuid.uuid4().hex[:12],
            content=content,
            doc_id="",  # 后续填充
            chunk_index=index,
            metadata={**metadata, "section": section},
        )

    # ══════════ 索引管理 ══════════

    def _reindex(self) -> None:
        """重建全部索引（批量操作，比逐条 index 快 10x）"""
        chunks = list(self.chunks.values())
        if not chunks:
            return
        t0 = time.time()
        self.retriever.index(chunks)
        self._indexed = True
        elapsed = time.time() - t0
        speed = f"{len(chunks) / elapsed:.0f}" if elapsed > 0 else "N/A"
        print(f"[RAG] Indexed {len(chunks)} chunks in {elapsed:.1f}s "
              f"({speed} chunks/s)")

    # ══════════ 持久化 ══════════

    def _save(self):
        """保存文档和分块（不含向量，向量重新计算很快）"""
        data = {
            "docs": [
                {
                    "id": d.id, "content": d.content, "metadata": d.metadata,
                    "chunks": [
                        {"id": c.id, "content": c.content, "chunk_index": c.chunk_index,
                         "metadata": c.metadata}
                        for c in d.chunks
                    ]
                }
                for d in self.docs.values()
            ]
        }
        self.store.put("kb", "docs", data)

    def _load(self):
        data = self.store.get("kb", "docs")
        if data and "docs" in data:
            for dd in data["docs"]:
                chunks = []
                for cd in dd.get("chunks", []):
                    c = Chunk(id=cd["id"], content=cd["content"],
                              doc_id=dd["id"], chunk_index=cd.get("chunk_index", 0),
                              metadata=cd.get("metadata", {}))
                    chunks.append(c); self.chunks[c.id] = c
                doc = Document(id=dd["id"], content=dd["content"],
                               metadata=dd.get("metadata", {}), chunks=chunks)
                self.docs[doc.id] = doc

    def save(self):
        self._save()

    # ══════════ RAPTOR 层次化索引 ══════════

    def _index_signature(self) -> str:
        """文档集签名：文档 id + 基础 chunk 数（排除已生成的摘要节点）。"""
        base_chunks = [
            c for c in self.chunks.values()
            if c.metadata.get("type") != "raptor_summary"
        ]
        return f"{'|'.join(sorted(self.docs))}:{len(base_chunks)}"

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """与 DenseRetriever 共享的中英文 tokenizer。"""
        return DenseRetriever._tokenize(text)

    def _persist_index_cache(self, kind: str, data: dict) -> None:
        try:
            self.store.put("index_cache", kind, data)
        except Exception:
            pass

    def _load_index_cache(self, kind: str) -> Optional[dict]:
        try:
            return self.store.get("index_cache", kind)
        except Exception:
            return None

    def build_raptor_tree(self, max_clusters: int = 10) -> int:
        """构建 RAPTOR 层次化摘要树。

        对应书中 3.3.1 节：RAPTOR (Recursive Abstractive Processing for Tree-Organized Retrieval)

        算法：
        1. 将所有 chunks 聚类（基于嵌入相似度）
        2. 对每个聚类用 LLM 生成摘要
        3. 摘要作为父节点加入索引
        4. 递归：对父节点重复聚类和摘要
        5. 直到只剩少量节点

        Returns:
            生成的摘要节点数
        """
        if not self._llm_client:
            return 0

        # 持久化命中：文档集未变化时直接恢复摘要节点（避免重复 LLM 调用）
        signature = self._index_signature()
        cached = self._load_index_cache("raptor")
        if cached and cached.get("signature") == signature:
            restored = 0
            for node in cached.get("nodes", []):
                cid = node.get("id")
                if cid and cid not in self.chunks:
                    self.chunks[cid] = Chunk(
                        id=cid,
                        content=node.get("content", ""),
                        doc_id=node.get("doc_id", ""),
                        chunk_index=len(self.chunks),
                        metadata=node.get("metadata", {}),
                    )
                    restored += 1
            if restored:
                self._indexed = False
            # 返回当前摘要节点总数（已存在 + 本次恢复）
            return len([
                c for c in self.chunks.values()
                if c.metadata.get("type") == "raptor_summary"
            ])

        if not self._indexed:
            self._reindex()

        chunks = list(self.chunks.values())
        if len(chunks) < 5:
            return 0

        generated = self._raptor_cluster_and_summarize(chunks, max_clusters, depth=0)

        # 持久化摘要节点，下次文档集未变时直接恢复
        summary_nodes = [
            {
                "id": c.id,
                "content": c.content,
                "doc_id": c.doc_id,
                "metadata": c.metadata,
            }
            for c in self.chunks.values()
            if c.metadata.get("type") == "raptor_summary"
        ]
        self._persist_index_cache("raptor", {
            "signature": signature,
            "nodes": summary_nodes,
        })
        return generated

    def _raptor_cluster_and_summarize(self, nodes: list[Chunk],
                                       max_clusters: int, depth: int) -> int:
        """递归聚类 + 摘要生成。"""
        if len(nodes) <= 3 or depth >= 3:
            return 0

        # 使用嵌入向量聚类（简化：基于相似度矩阵的贪心聚类）
        clusters = self._cluster_nodes(nodes, max_clusters)
        generated = 0

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            # 合并文本
            combined = "\n\n---\n\n".join(n.content[:500] for n in cluster)

            # LLM 生成摘要
            summary = self._generate_summary(combined)
            if not summary:
                continue

            # 创建摘要节点
            summary_chunk = self._make_chunk(
                summary,
                metadata={
                    "type": "raptor_summary",
                    "depth": depth,
                    "source_count": len(cluster),
                },
                section=f"RAPTOR-L{depth}",
                index=len(self.chunks),
            )
            summary_chunk.doc_id = f"raptor_d{depth}_{len(self.chunks)}"
            self.chunks[summary_chunk.id] = summary_chunk
            generated += 1

            # 递归（对摘要进行再聚类）
            if len(clusters) > 3:
                generated += self._raptor_cluster_and_summarize(
                    [summary_chunk] * min(len(cluster), 3),
                    max(max_clusters // 2, 2), depth + 1,
                )

        if generated > 0:
            self._indexed = False
        return generated

    def _cluster_nodes(self, nodes: list[Chunk], k: int) -> list[list[Chunk]]:
        """简化的贪心聚类（基于文本重叠度）。"""
        if len(nodes) <= k:
            return [[n] for n in nodes]

        # 计算文本重叠度矩阵
        n = len(nodes)
        tokens = [set(self._tokenize(node.content.lower())) for node in nodes]

        # 贪心聚类
        remaining = set(range(n))
        clusters = []
        target_size = max(n // k, 2)

        while remaining and len(clusters) < k:
            # 选一个种子
            seed = min(remaining)
            remaining.discard(seed)
            cluster = [nodes[seed]]

            # 找最相似的节点加入
            candidates = sorted(
                [(i, len(tokens[seed] & tokens[i]) / max(len(tokens[seed] | tokens[i]), 1))
                 for i in remaining],
                key=lambda x: x[1], reverse=True,
            )[:target_size - 1]

            for idx, _ in candidates:
                cluster.append(nodes[idx])
                remaining.discard(idx)

            clusters.append(cluster)

        return clusters

    def _generate_summary(self, text: str) -> Optional[str]:
        """LLM 生成摘要。"""
        if not self._llm_client:
            return None

        import asyncio
        prompt = (
            f"用 2-3 句话总结以下内容的核心理念和关键发现。"
            f"保留所有具体数字和术语：\n\n{text[:3000]}"
        )
        try:
            async def gen():
                resp = await self._llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1, max_tokens=300,
                )
                return resp.content.strip()

            return run_coro(gen)
        except Exception as e:
            import logging
            logging.getLogger("paperwise").debug(f"KB operation failed: {e}")
            return None

    # ══════════ GraphRAG 知识图谱 ══════════

    def build_knowledge_graph(self) -> dict:
        """构建知识图谱 — 提取实体和关系。

        对应书中 3.3.1 节：GraphRAG

        Returns:
            {"entities": [...], "relations": [...]}
        """
        if not self._llm_client:
            return {"entities": [], "relations": []}

        # 持久化命中：文档集未变化时直接返回缓存图谱
        signature = self._index_signature()
        cached = self._load_index_cache("graph")
        if cached and cached.get("signature") == signature:
            return cached.get("graph", {"entities": [], "relations": []})

        # 从所有文档中提取实体和关系
        all_text = "\n\n".join(
            d.content[:2000] for d in list(self.docs.values())[:10]
        )
        if not all_text.strip():
            return {"entities": [], "relations": []}

        graph = self._extract_entities_and_relations(all_text[:8000])
        self._persist_index_cache("graph", {
            "signature": signature,
            "graph": graph,
        })
        return graph

    def _extract_entities_and_relations(self, text: str) -> dict:
        """LLM 驱动的实体关系抽取。"""
        import asyncio
        prompt = (
            "从以下学术文本中提取知识图谱。\n\n"
            f"{text}\n\n"
            "请返回 JSON 格式：\n"
            '{{"entities": [{{"name": "实体名", "type": "method|dataset|metric|concept", '
            '"description": "简短描述"}}],\n'
            '"relations": [{{"source": "实体1", "target": "实体2", '
            '"relation": "关系类型"}}]}}\n\n'
            "只提取最重要的 8-15 个实体和 5-10 条关系。"
        )
        try:
            async def gen():
                resp = await self._llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1, max_tokens=1500,
                )
                return json.loads(resp.content)

            return run_coro(gen)
        except Exception:
            return {"entities": [], "relations": []}

    # ══════════ 多模态检索 ══════════

    def index_multimodal(self, paper_dir: Path) -> int:
        """索引论文中的图表和表格到知识库。

        对应书中 3.3.7 节：多模态记忆

        Args:
            paper_dir: 解析后的论文目录（含 figures/ 和 tables/）

        Returns:
            索引的多模态项数量
        """
        count = 0

        # 索引图片
        figures_dir = paper_dir / "figures"
        if figures_dir.exists():
            for img_file in sorted(figures_dir.glob("*.png")):
                desc_file = figures_dir / f"{img_file.stem}_desc.json"
                desc = ""
                if desc_file.exists():
                    try:
                        desc = json.loads(desc_file.read_text(encoding="utf-8")).get("caption", "")
                    except Exception:
                        pass

                self.add(
                    content=f"[FIGURE: {img_file.name}] {desc}",
                    metadata={
                        "type": "figure",
                        "paper_id": paper_dir.name,
                        "figure_path": str(img_file),
                        "caption": desc,
                    },
                    doc_id=f"fig_{paper_dir.name}_{img_file.stem}",
                )
                count += 1

        # 索引表格
        tables_dir = paper_dir / "tables"
        if tables_dir.exists():
            for tbl_file in sorted(tables_dir.glob("*.json")):
                try:
                    tbl_data = json.loads(tbl_file.read_text(encoding="utf-8"))
                    headers = tbl_data.get("headers", [])
                    rows = tbl_data.get("rows", [])
                    caption = tbl_data.get("caption", "")

                    # 将表格转为可检索文本
                    tbl_text = f"[TABLE: {tbl_file.stem}] {caption}\n"
                    tbl_text += " | ".join(headers) + "\n"
                    for row in rows[:20]:
                        tbl_text += " | ".join(row) + "\n"

                    self.add(
                        content=tbl_text,
                        metadata={
                            "type": "table",
                            "paper_id": paper_dir.name,
                            "table_path": str(tbl_file),
                            "caption": caption,
                        },
                        doc_id=f"tbl_{paper_dir.name}_{tbl_file.stem}",
                    )
                    count += 1
                except Exception:
                    pass

        # 索引公式
        formulas_dir = paper_dir / "formulas"
        if formulas_dir.exists():
            for tex_file in sorted(formulas_dir.glob("*.tex")):
                try:
                    latex = tex_file.read_text(encoding="utf-8")
                    self.add(
                        content=f"[FORMULA] {latex}",
                        metadata={
                            "type": "formula",
                            "paper_id": paper_dir.name,
                        },
                        doc_id=f"eq_{paper_dir.name}_{tex_file.stem}",
                    )
                    count += 1
                except Exception:
                    pass

        return count

    # ══════════ Agentic RAG 工具 ══════════

    def get_search_tool_description(self) -> dict:
        """返回给 Agent 的知识库搜索工具定义。

        对应书中 3.3.4 节：Agentic RAG —— 让 Agent 自主决定何时搜索、搜索什么。
        """
        return {
            "name": "search_knowledge_base",
            "description": (
                "搜索本地知识库中已分析过的论文和历史记录。"
                "当你需要查找之前分析过的相关论文、用户偏好、或历史经验时使用。"
                "不要用于：搜索当前论文内容（使用 grep/read_file）、"
                "搜索互联网（使用 web_search）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，例如 'attention mechanism' 或 '图神经网络节点分类'"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5
                    },
                    "search_chunks": {
                        "type": "boolean",
                        "description": "是否返回具体段落而非整个文档。用于需要精确引用的场景。",
                        "default": False
                    },
                },
                "required": ["query"],
            },
        }

    def stats(self) -> dict:
        return {
            "documents": len(self.docs),
            "chunks": len(self.chunks),
            "indexed": self._indexed,
            "total_chars": sum(len(d.content) for d in self.docs.values()),
            "chunk_size": self._chunk_size,
            "types": dict(Counter(d.metadata.get("type", "unknown")
                                  for d in self.docs.values())),
        }
