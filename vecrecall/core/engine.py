"""
VecRecall — 改进版核心引擎

关键设计原则：
  检索路径  → 纯向量语义，不经过任何结构过滤
  组织路径  → Wing/Topic/Diary 分层，只用于人工浏览和 UI 展示
  AAAK      → 只生成摘要供 UI 展示，不参与检索
  四层栈    → L0/L1/L2/L3 保留，L2 触发改为语义阈值
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────

@dataclass
class Memory:
    """一条完整的记忆记录（逐字存储原文）"""
    id: str
    wing: str                   # 项目/人物标识（组织用，不参与检索过滤）
    topic: str                  # 话题标签（组织用，不参与检索过滤）
    content: str                # 原始全文，永不改写
    timestamp: float
    session_id: str
    importance: float = 0.5     # 0-1，由 LLM 或启发式评分
    ui_summary: str = ""        # AAAK 压缩摘要，仅供 UI 展示
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(cls, wing: str, topic: str, content: str,
               session_id: str = "", importance: float = 0.5,
               ui_summary: str = "", metadata: dict | None = None) -> "Memory":
        return cls(
            id=str(uuid.uuid4()),
            wing=wing,
            topic=topic,
            content=content,
            timestamp=time.time(),
            session_id=session_id or str(uuid.uuid4()),
            importance=importance,
            ui_summary=ui_summary,
            metadata=metadata or {},
        )


@dataclass
class RetrievalResult:
    memory: Memory
    score: float        # 余弦相似度，0-1
    layer: str          # L1 / L2 / L3


@dataclass
class ContextBundle:
    """四层记忆栈打包结果，交给 AI 直接用"""
    l0_identity: str            # ~50 tokens
    l1_key_moments: list[RetrievalResult]   # ~600 tokens
    l2_topic_context: list[RetrievalResult] # ~300 tokens，按需
    l3_deep_results: list[RetrievalResult]  # 按需触发
    total_tokens_estimate: int


# ─────────────────────────────────────────────
# 向量适配器（可插拔后端）
# ─────────────────────────────────────────────

class VectorBackend:
    """抽象接口，默认用轻量 numpy 实现；生产可替换为 ChromaDB"""

    def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
        raise NotImplementedError

    def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
        """返回 [(memory_id, score, payload), ...]"""
        raise NotImplementedError

    def delete(self, memory_id: str) -> None:
        raise NotImplementedError


class NumpyVectorBackend(VectorBackend):
    """纯 numpy 实现，零依赖，适合开发和测试"""

    def __init__(self):
        self._store: dict[str, tuple[list[float], dict]] = {}

    def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
        self._store[memory_id] = (vector, payload)

    def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
        import math
        results = []
        qv = vector
        qnorm = math.sqrt(sum(x * x for x in qv)) or 1e-9

        for mid, (v, payload) in self._store.items():
            vnorm = math.sqrt(sum(x * x for x in v)) or 1e-9
            dot = sum(a * b for a, b in zip(qv, v))
            score = dot / (qnorm * vnorm)
            if score >= min_score:
                results.append((mid, score, payload))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:n]

    def delete(self, memory_id: str) -> None:
        self._store.pop(memory_id, None)


try:
    import chromadb

    class ChromaVectorBackend(VectorBackend):
        """生产级 ChromaDB 后端"""

        def __init__(self, persist_dir: str, collection: str = "memories"):
            self._client = chromadb.PersistentClient(path=persist_dir)
            self._col = self._client.get_or_create_collection(
                name=collection,
                metadata={"hnsw:space": "cosine"},
            )

        def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
            self._col.upsert(
                ids=[memory_id],
                embeddings=[vector],
                metadatas=[{k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                            for k, v in payload.items()}],
            )

        def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
            res = self._col.query(query_embeddings=[vector], n_results=max(n, 1))
            out = []
            ids = res["ids"][0]
            dists = res["distances"][0]
            metas = res["metadatas"][0]
            for mid, dist, meta in zip(ids, dists, metas):
                score = 1.0 - dist   # cosine distance → similarity
                if score >= min_score:
                    out.append((mid, score, meta))
            return out[:n]

        def delete(self, memory_id: str) -> None:
            self._col.delete(ids=[memory_id])

except ImportError:
    ChromaVectorBackend = None  # type: ignore


# ─────────────────────────────────────────────
# 嵌入适配器（可插拔）
# ─────────────────────────────────────────────

class EmbeddingBackend:
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class HashEmbeddingBackend(EmbeddingBackend):
    """确定性哈希向量，仅供测试，无语义"""

    DIM = 128

    def embed(self, text: str) -> list[float]:
        import struct
        h = hashlib.sha256(text.encode()).digest()
        vals = struct.unpack("f" * (len(h) // 4), h)
        # 填充或裁剪到 DIM
        v = list(vals)
        while len(v) < self.DIM:
            v.extend(v)
        v = v[:self.DIM]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]


try:
    from sentence_transformers import SentenceTransformer

    class SentenceTransformerBackend(EmbeddingBackend):
        def __init__(self, model: str = "all-MiniLM-L6-v2"):
            self._model = SentenceTransformer(model)

        def embed(self, text: str) -> list[float]:
            return self._model.encode(text, normalize_embeddings=True).tolist()

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return self._model.encode(texts, normalize_embeddings=True).tolist()

except ImportError:
    SentenceTransformerBackend = None  # type: ignore


# ─────────────────────────────────────────────
# 知识图谱（SQLite）
# ─────────────────────────────────────────────

class KnowledgeGraph:
    """SQLite 存储记忆元数据和跨 wing 关联，不参与检索路径"""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._setup()

    def _setup(self):
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            wing TEXT NOT NULL,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            session_id TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            ui_summary TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_wing ON memories(wing);
        CREATE INDEX IF NOT EXISTS idx_topic ON memories(topic);
        CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance DESC);

        CREATE TABLE IF NOT EXISTS cross_links (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            link_type TEXT DEFAULT 'related',
            weight REAL DEFAULT 1.0,
            FOREIGN KEY(source_id) REFERENCES memories(id),
            FOREIGN KEY(target_id) REFERENCES memories(id)
        );
        CREATE INDEX IF NOT EXISTS idx_links_src ON cross_links(source_id);
        """)
        self._conn.commit()

    def save(self, memory: Memory) -> None:
        self._conn.execute("""
        INSERT OR REPLACE INTO memories
          (id, wing, topic, content, timestamp, session_id, importance, ui_summary, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory.id, memory.wing, memory.topic, memory.content,
              memory.timestamp, memory.session_id, memory.importance,
              memory.ui_summary, json.dumps(memory.metadata)))
        self._conn.commit()

    def get(self, memory_id: str) -> Optional[Memory]:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def get_many(self, ids: list[str]) -> dict[str, Memory]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {r[0]: self._row_to_memory(r) for r in rows}

    def top_by_importance(self, wing: str | None, n: int) -> list[Memory]:
        if wing:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE wing=? ORDER BY importance DESC LIMIT ?",
                (wing, n)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY importance DESC LIMIT ?", (n,)
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def list_wings(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT wing FROM memories ORDER BY wing"
        ).fetchall()
        return [r[0] for r in rows]

    def list_topics(self, wing: str | None = None) -> list[str]:
        if wing:
            rows = self._conn.execute(
                "SELECT DISTINCT topic FROM memories WHERE wing=? ORDER BY topic", (wing,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT DISTINCT topic FROM memories ORDER BY topic"
            ).fetchall()
        return [r[0] for r in rows]

    def add_link(self, source_id: str, target_id: str,
                 link_type: str = "related", weight: float = 1.0) -> None:
        self._conn.execute("""
        INSERT OR REPLACE INTO cross_links (id, source_id, target_id, link_type, weight)
        VALUES (?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), source_id, target_id, link_type, weight))
        self._conn.commit()

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        wings = len(self.list_wings())
        links = self._conn.execute("SELECT COUNT(*) FROM cross_links").fetchone()[0]
        return {"total_memories": total, "wings": wings, "cross_links": links}

    def _row_to_memory(self, row) -> Memory:
        return Memory(
            id=row[0], wing=row[1], topic=row[2], content=row[3],
            timestamp=row[4], session_id=row[5], importance=row[6],
            ui_summary=row[7], metadata=json.loads(row[8])
        )

    def close(self):
        self._conn.close()


# ─────────────────────────────────────────────
# 改进版四层记忆栈引擎
# ─────────────────────────────────────────────

class VecRecall:
    """
    改进版记忆引擎。

    核心改动：
      1. 检索路径完全去除结构过滤（不按 wing/topic 过滤向量查询）
      2. L2 触发改为语义相似度阈值，而非 Room 名称匹配
      3. AAAK（ui_summary）只写入 SQLite UI 层，不进入向量检索
      4. 组织层（Wing/Topic）只影响 KnowledgeGraph，不影响向量查询
    """

    # L2 触发：当前对话与历史话题的语义相似度 ≥ 此阈值时，加载该话题上下文
    L2_TRIGGER_THRESHOLD = 0.55
    # L1 固定加载数量
    L1_TOP_K = 15
    # L2 按需加载数量
    L2_TOP_K = 8
    # L3 深度检索数量
    L3_TOP_K = 20

    def __init__(
        self,
        base_dir: str,
        wing: str = "default",
        vector_backend: VectorBackend | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        identity_prompt: str = "",
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.wing = wing
        self.identity_prompt = identity_prompt

        # 存储后端
        self._vec = vector_backend or NumpyVectorBackend()
        self._emb = embedding_backend or HashEmbeddingBackend()
        self._kg = KnowledgeGraph(str(self.base_dir / "knowledge.db"))

    # ── 写入 ──────────────────────────────────

    def add(self, content: str, topic: str = "general",
            wing: str | None = None, importance: float | None = None,
            session_id: str = "", ui_summary: str = "",
            metadata: dict | None = None) -> Memory:
        """
        存入一条记忆。

        - content 原文逐字存入 KnowledgeGraph
        - 嵌入向量不携带 wing/topic 过滤信息，保持纯语义
        - ui_summary（AAAK）只写入 KG，不进入向量
        """
        w = wing or self.wing
        if importance is None:
            importance = self._heuristic_importance(content)

        mem = Memory.create(
            wing=w, topic=topic, content=content,
            session_id=session_id, importance=importance,
            ui_summary=ui_summary, metadata=metadata or {},
        )

        # 1. 向量索引：只索引原文，不含结构信息（这是关键改动）
        vec = self._emb.embed(content)
        self._vec.upsert(mem.id, vec, {"id": mem.id})

        # 2. 元数据 + 原文持久化到 SQLite
        self._kg.save(mem)

        return mem

    def add_batch(self, items: list[dict]) -> list[Memory]:
        """批量写入，更高效"""
        memories = []
        for item in items:
            m = self.add(**item)
            memories.append(m)
        return memories

    # ── 四层记忆栈 ───────────────────────────

    def build_context(
        self,
        current_query: str = "",
        load_l2: bool = True,
        load_l3: bool = False,
    ) -> ContextBundle:
        """
        构建完整的四层上下文 bundle。

        L0: 固定身份层
        L1: 全库按 importance 排序 top-15（不按 wing 过滤）
        L2: 当前查询语义触发，阈值 ≥ 0.55
        L3: 仅显式请求时触发，全量语义检索
        """

        # L0
        l0 = self.identity_prompt or f"[Wing: {self.wing}] AI 助手，服务于当前项目。"

        # L1：按 importance 取 top-K，不做向量过滤（保留原版设计）
        top_mems = self._kg.top_by_importance(wing=None, n=self.L1_TOP_K)
        l1_results = [
            RetrievalResult(memory=m, score=m.importance, layer="L1")
            for m in top_mems
        ]

        # L2：语义触发（核心改动：阈值判断，不是 Room 名称匹配）
        l2_results: list[RetrievalResult] = []
        if load_l2 and current_query.strip():
            l2_results = self._semantic_l2(current_query)

        # L3：全量语义检索
        l3_results: list[RetrievalResult] = []
        if load_l3 and current_query.strip():
            l3_results = self._deep_search(current_query, self.L3_TOP_K)

        # token 估算（粗略：1 token ≈ 4 字符）
        def count(results):
            return sum(len(r.memory.content) // 4 for r in results)

        total_tokens = 50 + count(l1_results) + count(l2_results) + count(l3_results)

        return ContextBundle(
            l0_identity=l0,
            l1_key_moments=l1_results,
            l2_topic_context=l2_results,
            l3_deep_results=l3_results,
            total_tokens_estimate=total_tokens,
        )

    def _semantic_l2(self, query: str) -> list[RetrievalResult]:
        """
        L2 语义触发：查询向量与历史记忆的余弦相似度 ≥ 阈值才返回。
        关键：不使用任何结构过滤条件（不按 wing/topic/room 限制）。
        """
        vec = self._emb.embed(query)
        raw = self._vec.query(vec, n=self.L2_TOP_K * 2, min_score=self.L2_TRIGGER_THRESHOLD)

        ids = [r[0] for r in raw]
        scores = {r[0]: r[1] for r in raw}
        mem_map = self._kg.get_many(ids)

        results = []
        for mid, score in scores.items():
            if mid in mem_map:
                results.append(RetrievalResult(
                    memory=mem_map[mid], score=score, layer="L2"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.L2_TOP_K]

    def _deep_search(self, query: str, n: int) -> list[RetrievalResult]:
        """
        L3 全量语义检索：直接命中向量库，无任何过滤条件。
        这正是 96.6% 召回率的来源。
        """
        vec = self._emb.embed(query)
        raw = self._vec.query(vec, n=n, min_score=0.0)

        ids = [r[0] for r in raw]
        scores = {r[0]: r[1] for r in raw}
        mem_map = self._kg.get_many(ids)

        results = []
        for mid, score in scores.items():
            if mid in mem_map:
                results.append(RetrievalResult(
                    memory=mem_map[mid], score=score, layer="L3"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ── 搜索 ──────────────────────────────────

    def search(self, query: str, n: int = 10, min_score: float = 0.0) -> list[RetrievalResult]:
        """直接语义搜索，不经过任何结构过滤"""
        return self._deep_search(query, n)

    # ── 组织层（只供 UI，不影响检索）─────────

    def list_wings(self) -> list[str]:
        return self._kg.list_wings()

    def list_topics(self, wing: str | None = None) -> list[str]:
        return self._kg.list_topics(wing)

    def stats(self) -> dict:
        return self._kg.stats()

    def link(self, source_id: str, target_id: str,
             link_type: str = "related", weight: float = 1.0) -> None:
        """手动建立跨 wing 关联（可选）"""
        self._kg.add_link(source_id, target_id, link_type, weight)

    # ── 辅助 ──────────────────────────────────

    def _heuristic_importance(self, content: str) -> float:
        """启发式重要性评分：长度 + 关键词"""
        score = min(len(content) / 2000, 0.5)
        keywords = ["决定", "架构", "bug", "部署", "重要", "critical",
                    "decision", "architecture", "deploy", "migration"]
        for kw in keywords:
            if kw.lower() in content.lower():
                score += 0.1
        return min(score, 1.0)

    def close(self):
        self._kg.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
