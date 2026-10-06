"""
VecRecall — 改进版核心引擎

关键设计原则：
  检索路径  → 纯向量语义，不经过任何结构过滤
  组织路径  → Wing/Topic/Diary 分层，只用于人工浏览和 UI 展示
  AAAK      → 只生成摘要供 UI 展示，不参与检索
  四层栈    → L0/L1/L2/L3 保留，L2 触发改为语义阈值

v1.1 升级：
  SqliteVectorBackend        零依赖持久化向量后端（默认，重启不丢索引）
  BagOfWordsEmbeddingBackend 零依赖词法语义嵌入（默认，替代纯哈希）
  update / delete / prune    记忆生命周期管理，向量与 SQLite 同步
  reindex                   从原文重建向量索引（嵌入后端迁移）
  hybrid 检索               相似度 + 重要性 + 时间衰减融合重排（opt-in）
  use_blockchain             写入时同步生成哈希链区块，verify_integrity 防篡改
  estimate_tokens           中英混合 token 估算

v2.0 升级（认知层）：
  MemoryExtractor           可插拔记忆抽取器（Heuristic 零依赖 / LLM Ollama+API）
  extractor 集成            add 时自动抽取 topic/importance/摘要/实体/关系
  dedup                     语义去重合并：相似度 ≥ dedup_threshold 合并到既有记忆
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator, Optional

from vecrecall.core.extractor import (
    Extraction, MemoryExtractor, heuristic_importance, heuristic_summary,
)
from vecrecall.core.crypto import Cipher
from vecrecall.core.forgetting import ForgettingCurve
from vecrecall.core.temporal_graph import TemporalGraph


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    更准确的 token 估算（中英混合）：
      中文 ≈ 0.7 token/字，英文/其他 ≈ 4 字符/token
    """
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    other = len(text) - cjk
    return int(cjk * 0.7 + other / 4) + 1


def memory_to_dict(m, full: bool = False) -> dict:
    """将 Memory 序列化为 dict（MCP / REST / 导出统一使用）。"""
    d = {
        "id": m.id,
        "wing": m.wing,
        "topic": m.topic,
        "importance": round(m.importance, 3),
        "timestamp": m.timestamp,
        "ui_summary": m.ui_summary,
        "content_preview": m.content[:200],
    }
    if full:
        d["content"] = m.content
        d["session_id"] = m.session_id
        d["metadata"] = m.metadata
    return d


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

    def count(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


class SqliteVectorBackend(VectorBackend):
    """
    零依赖持久化向量后端（SQLite 存储，重启不丢数据）。

    与 NumpyVectorBackend 的区别：
      - NumpyVectorBackend 纯内存，进程退出即丢失全部向量索引
      - 本后端把向量以二进制 BLOB 存入 SQLite，默认安装下记忆可跨会话检索
    """

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL,
            payload TEXT DEFAULT '{}'
        )""")
        self._conn.commit()

    def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
        blob = struct.pack(f"<{len(vector)}d", *vector)
        self._conn.execute(
            "INSERT OR REPLACE INTO vectors (id, dim, vec, payload) VALUES (?, ?, ?, ?)",
            (memory_id, len(vector), blob,
             json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
        qnorm = math.sqrt(sum(x * x for x in vector)) or 1e-9
        results = []
        for mid, dim, blob, payload in self._conn.execute(
            "SELECT id, dim, vec, payload FROM vectors"
        ):
            v = struct.unpack(f"<{dim}d", blob)
            vnorm = math.sqrt(sum(x * x for x in v)) or 1e-9
            score = sum(a * b for a, b in zip(vector, v)) / (qnorm * vnorm)
            if score >= min_score:
                results.append((mid, score, json.loads(payload)))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:n]

    def delete(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM vectors WHERE id=?", (memory_id,))
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM vectors")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


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


class BagOfWordsEmbeddingBackend(EmbeddingBackend):
    """
    零依赖语义嵌入（特征哈希 / hashing trick，含 subword 增强）。

    中文拆成单字 + 相邻二元组 + 三元组；英文/数字按整词，
    并额外拆分字符 3-gram / 4-gram（subword），以捕获词形变化
    与拼写变体（如 running/run、database/databases、颜色/色彩）。
    哈希到固定维度并带正负号，余弦相似度 ≈ 词面 + 子词重合度，
    是零依赖（无 sentence-transformers / Ollama / API）下最强的
    语义召回，且无状态、确定性、无外部依赖。
    """

    DIM = 512

    _TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        return '\u4e00' <= ch <= '\u9fff'

    def _tokens(self, text: str) -> list[str]:
        raw = self._TOKEN_RE.findall(text.lower())
        out: list[str] = []
        for i, t in enumerate(raw):
            if len(t) == 1 and self._is_cjk(t):
                # 单字
                out.append(t)
                # 相邻中文二元组 / 三元组（捕获词序信息）
                if i + 1 < len(raw) and len(raw[i + 1]) == 1 \
                        and self._is_cjk(raw[i + 1]):
                    out.append(t + raw[i + 1])
                    if i + 2 < len(raw) and len(raw[i + 2]) == 1 \
                            and self._is_cjk(raw[i + 2]):
                        out.append(t + raw[i + 1] + raw[i + 2])
            else:
                # 整词
                out.append(t)
                # 英文字符 3-gram / 4-gram（subword，捕获词形与拼写变体）
                if len(t) >= 4:
                    for j in range(len(t) - 2):
                        out.append("#" + t[j:j + 3])
                if len(t) >= 6:
                    for j in range(len(t) - 3):
                        out.append("#" + t[j:j + 4])
        return out

    def embed(self, text: str) -> list[float]:
        tf: dict[str, int] = {}
        for t in self._tokens(text):
            tf[t] = tf.get(t, 0) + 1

        v = [0.0] * self.DIM
        for token, freq in tf.items():
            weight = 1.0 + math.log(freq)
            h = hashlib.sha256(token.encode()).digest()
            idx = ((h[0] << 8) | h[1]) % self.DIM
            sign = 1.0 if h[2] & 1 else -1.0
            v[idx] += sign * weight

        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


# 默认本地语义模型：多语言 MiniLM（384 维，中英等 50+ 语言友好）
DEFAULT_SENTENCE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

try:
    from sentence_transformers import SentenceTransformer

    class SentenceTransformerBackend(EmbeddingBackend):
        def __init__(self, model: str = DEFAULT_SENTENCE_MODEL):
            self._model = SentenceTransformer(model)

        def embed(self, text: str) -> list[float]:
            return self._model.encode(text, normalize_embeddings=True).tolist()

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return self._model.encode(texts, normalize_embeddings=True).tolist()

except ImportError:
    SentenceTransformerBackend = None  # type: ignore


class OllamaEmbeddingBackend(EmbeddingBackend):
    """
    本地 Ollama 嵌入后端（零 API Key，数据不出本机）。

    依赖本地运行的 Ollama 服务（默认 http://localhost:11434），
    推荐模型：nomic-embed-text / bge-m3（中文）。

    优先走新版 /api/embed（支持批量），旧版 Ollama 自动回退到
    /api/embeddings（单条）。
    """

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434",
                 timeout: float = 60.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 新版 /api/embed 支持批量
        try:
            data = self._post("/api/embed", {"model": self.model, "input": texts})
            if "embeddings" in data:
                return [list(v) for v in data["embeddings"]]
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass  # 旧版 Ollama，回退单条接口

        # 回退旧版 /api/embeddings（仅单条）
        out: list[list[float]] = []
        for t in texts:
            data = self._post("/api/embeddings", {"model": self.model, "prompt": t})
            out.append(list(data["embedding"]))
        return out


class APIEmbeddingBackend(EmbeddingBackend):
    """
    OpenAI 兼容远程嵌入 API 后端。

    可指向 OpenAI、或任意 OpenAI 兼容的嵌入服务
    （硅基流动 / DeepSeek / 通义 / 本地 vLLM 等），
    只需调整 base_url / api_key / model。
    """

    def __init__(self, model: str = "text-embedding-3-small",
                 api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 60.0):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._post({"model": self.model, "input": texts})
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        return [list(item["embedding"]) for item in items]


def build_embedding_backend(kind: str = "auto", **kwargs) -> EmbeddingBackend:
    """
    按名称构造嵌入后端（供 CLI / MCP / REST / Python API 统一使用）。

    kind 取值：
      auto     → 优先 sentence-transformers，否则词法 BagOfWords（零依赖）
      bow      → BagOfWordsEmbeddingBackend（零依赖词法语义）
      sentence → SentenceTransformerBackend（本地模型）
      ollama   → OllamaEmbeddingBackend（本地 Ollama，零 API Key）
      api      → APIEmbeddingBackend（OpenAI 兼容远程 API）

    通用 kwargs：model / base_url / api_key
    """
    kind = (kind or "auto").lower()
    if kind == "bow":
        return BagOfWordsEmbeddingBackend()
    if kind == "sentence":
        if SentenceTransformerBackend is None:
            raise RuntimeError(
                "sentence-transformers 未安装，请 `pip install sentence-transformers`")
        return SentenceTransformerBackend(kwargs.get("model") or DEFAULT_SENTENCE_MODEL)
    if kind == "ollama":
        return OllamaEmbeddingBackend(
            model=kwargs.get("model") or "nomic-embed-text",
            base_url=kwargs.get("base_url") or kwargs.get("url") or "http://localhost:11434",
            timeout=kwargs.get("timeout") or 60.0,
        )
    if kind == "api":
        return APIEmbeddingBackend(
            model=kwargs.get("model") or "text-embedding-3-small",
            api_key=kwargs.get("api_key"),
            base_url=kwargs.get("base_url") or kwargs.get("url") or "https://api.openai.com/v1",
            timeout=kwargs.get("timeout") or 60.0,
        )
    # auto
    if SentenceTransformerBackend is not None:
        return SentenceTransformerBackend()
    return BagOfWordsEmbeddingBackend()


# ─────────────────────────────────────────────
# 知识图谱（SQLite）
# ─────────────────────────────────────────────

class KnowledgeGraph:
    """SQLite 存储记忆元数据和跨 wing 关联，不参与检索路径"""

    def __init__(self, db_path: str, cipher: Cipher | None = None):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        # 静态加密：非 None 时 content/ui_summary/metadata 加密落盘
        self._cipher = cipher
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
        content, ui_summary = memory.content, memory.ui_summary
        meta_raw = json.dumps(memory.metadata)
        if self._cipher is not None:
            content = self._cipher.encrypt(content)
            ui_summary = self._cipher.encrypt(ui_summary)
            meta_raw = self._cipher.encrypt(meta_raw)
        self._conn.execute("""
        INSERT OR REPLACE INTO memories
          (id, wing, topic, content, timestamp, session_id, importance, ui_summary, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory.id, memory.wing, memory.topic, content,
              memory.timestamp, memory.session_id, memory.importance,
              ui_summary, meta_raw))
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

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆及其关联，返回是否删除成功"""
        cur = self._conn.execute(
            "DELETE FROM memories WHERE id=?", (memory_id,)
        )
        deleted = cur.rowcount > 0
        if deleted:
            self._conn.execute(
                "DELETE FROM cross_links WHERE source_id=? OR target_id=?",
                (memory_id, memory_id)
            )
        self._conn.commit()
        return deleted

    def iter_all(self, wing: str | None = None) -> Iterator[Memory]:
        """遍历全部记忆（按写入顺序）"""
        if wing:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE wing=? ORDER BY timestamp", (wing,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY timestamp"
            ).fetchall()
        for r in rows:
            yield self._row_to_memory(r)

    def count(self, wing: str | None = None) -> int:
        if wing:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE wing=?", (wing,)
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        wings = len(self.list_wings())
        links = self._conn.execute("SELECT COUNT(*) FROM cross_links").fetchone()[0]
        return {"total_memories": total, "wings": wings, "cross_links": links}

    def query_memories(self, wing: str | None = None, topic: str | None = None,
                       limit: int = 50, offset: int = 0,
                       sort: str = "importance") -> tuple[list[Memory], int]:
        """
        分页查询记忆，返回 (memories, total)。
        sort: "importance"（默认，按重要性降序）或 "recent"（按时间降序）。
        """
        where: list[str] = []
        params: list = []
        if wing:
            where.append("wing=?")
            params.append(wing)
        if topic:
            where.append("topic=?")
            params.append(topic)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM memories{wsql}", params).fetchone()[0]

        if sort == "recent":
            order = "timestamp DESC"
        else:
            order = "importance DESC, timestamp DESC"
        rows = self._conn.execute(
            f"SELECT * FROM memories{wsql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_memory(r) for r in rows], total

    def _row_to_memory(self, row) -> Memory:
        content, ui_summary, meta_raw = row[3], row[7], row[8]
        if self._cipher is not None:
            content = self._cipher.decrypt(content)
            ui_summary = self._cipher.decrypt(ui_summary)
            meta_raw = self._cipher.decrypt(meta_raw)
        return Memory(
            id=row[0], wing=row[1], topic=row[2], content=content,
            timestamp=row[4], session_id=row[5], importance=row[6],
            ui_summary=ui_summary, metadata=json.loads(meta_raw)
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
    # 混合评分：时间衰减半衰期（天）
    RECENCY_HALF_LIFE_DAYS = 30.0

    def __init__(
        self,
        base_dir: str,
        wing: str = "default",
        vector_backend: VectorBackend | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        identity_prompt: str = "",
        use_blockchain: bool = False,
        extractor: MemoryExtractor | None = None,
        dedup: bool = True,
        dedup_threshold: float = 0.92,
        encryption_key: str | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.wing = wing
        self.identity_prompt = identity_prompt

        # 认知层：写入时自动抽取结构化信息（None=关闭，保持词法级零依赖）
        self._extractor = extractor
        # 语义去重：相似度 ≥ 阈值时合并到既有记忆（避免重复）
        self.dedup = dedup
        self.dedup_threshold = dedup_threshold

        # 存储后端：自动检测可用的最佳后端
        if embedding_backend is not None:
            self._emb = embedding_backend
        elif SentenceTransformerBackend is not None:
            self._emb = SentenceTransformerBackend()
        else:
            # 零依赖默认：词法语义嵌入（余弦相似度 ≈ 词面重合度）
            self._emb = BagOfWordsEmbeddingBackend()

        if vector_backend is not None:
            self._vec = vector_backend
        elif ChromaVectorBackend is not None:
            self._vec = ChromaVectorBackend(str(self.base_dir / "chroma"))
        else:
            # 零依赖默认：SQLite 持久化，重启不丢向量索引
            self._vec = SqliteVectorBackend(str(self.base_dir / "vectors.db"))

        # 静态加密：提供 encryption_key 时，记忆内容（content/摘要/元数据）加密落盘
        self._cipher = None
        if encryption_key:
            self._cipher = Cipher.resolve(encryption_key, self.base_dir)
        self._kg = KnowledgeGraph(
            str(self.base_dir / "knowledge.db"), cipher=self._cipher)

        # 时序知识图谱：写入记忆时自动记录实体/关系/状态演化
        self._graph = TemporalGraph(str(self.base_dir / "graph.db"))

        # 遗忘曲线：记忆强度随时间衰减（艾宾浩斯曲线），供检索加权与遗忘清理
        self._forgetting = ForgettingCurve()

        # 可选哈希链：写入记忆时同步生成防篡改区块
        self._chain = None
        if use_blockchain:
            from vecrecall.blockchain import BlockChain
            self._chain = BlockChain(str(self.base_dir / "chain.db"))

    # ── 写入 ──────────────────────────────────

    def add(self, content: str, topic: str = "general",
            wing: str | None = None, importance: float | None = None,
            session_id: str = "", ui_summary: str = "",
            metadata: dict | None = None, dedup: bool | None = None) -> Memory:
        """
        存入一条记忆。

        - content 原文逐字存入 KnowledgeGraph
        - 嵌入向量不携带 wing/topic 过滤信息，保持纯语义
        - ui_summary（AAAK）只写入 KG，不进入向量
        - 配置了 extractor 时，自动抽取 topic/importance/摘要/实体/关系
        - 语义去重：相似度 ≥ dedup_threshold 时合并到既有记忆（occurrences+1）
        """
        w = wing or self.wing

        # 认知层抽取（仅在未显式提供时填充）
        ext: Extraction | None = None
        if self._extractor is not None:
            ext = self._extractor.extract(content)

        if ext is not None:
            if topic == "general" and ext.topic != "general":
                topic = ext.topic
            if importance is None:
                importance = ext.importance
            if not ui_summary:
                ui_summary = ext.ui_summary

        if importance is None:
            importance = self._heuristic_importance(content)

        meta = dict(metadata or {})
        if ext is not None:
            if ext.entities:
                meta.setdefault("entities", ext.entities)
            if ext.relations:
                meta.setdefault("relations", ext.relations)

        # 语义去重合并（相似度过高时不再新增）
        do_dedup = self.dedup if dedup is None else dedup
        if do_dedup:
            existing = self._dedup_candidate(content)
            if existing is not None:
                existing.importance = max(existing.importance, importance)
                existing.metadata["occurrences"] = \
                    existing.metadata.get("occurrences", 1) + 1
                self._kg.save(existing)
                self._ingest_graph(ext, existing.id, existing.timestamp)
                return existing

        mem = Memory.create(
            wing=w, topic=topic, content=content,
            session_id=session_id, importance=importance,
            ui_summary=ui_summary, metadata=meta,
        )

        # 1. 向量索引：只索引原文，不含结构信息（这是关键改动）
        vec = self._emb.embed(content)
        self._vec.upsert(mem.id, vec, {"id": mem.id})

        # 2. 元数据 + 原文持久化到 SQLite
        self._kg.save(mem)

        # 3. 时序知识图谱：实体/关系/状态演化
        self._ingest_graph(ext, mem.id, mem.timestamp)

        # 4. 可选：哈希链区块（防篡改审计）
        if self._chain is not None:
            from vecrecall.blockchain import extract_keywords
            self._chain.new_block(
                content=content,
                wing=w,
                session_id=mem.session_id,
                trigger="memory",
                keywords=extract_keywords(content),
            )

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
        hybrid: bool = False,
    ) -> ContextBundle:
        """
        构建完整的四层上下文 bundle。

        L0: 固定身份层
        L1: 全库按 importance 排序 top-15（不按 wing 过滤）
        L2: 当前查询语义触发，阈值 ≥ 0.55
        L3: 仅显式请求时触发，全量语义检索
        hybrid: True 时 L2/L3 分数 = 0.6·相似度 + 0.25·重要性 + 0.15·时间新鲜度
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
            l2_results = self._semantic_l2(current_query, hybrid=hybrid)

        # L3：全量语义检索
        l3_results: list[RetrievalResult] = []
        if load_l3 and current_query.strip():
            l3_results = self._deep_search(current_query, self.L3_TOP_K, hybrid=hybrid)

        # token 估算（中英混合修正：中文 ≈ 0.7 token/字）
        def count(results):
            return sum(estimate_tokens(r.memory.content) for r in results)

        total_tokens = estimate_tokens(l0) + count(l1_results) + count(l2_results) + count(l3_results)

        return ContextBundle(
            l0_identity=l0,
            l1_key_moments=l1_results,
            l2_topic_context=l2_results,
            l3_deep_results=l3_results,
            total_tokens_estimate=total_tokens,
        )

    def _semantic_l2(self, query: str, hybrid: bool = False) -> list[RetrievalResult]:
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
                s = self._hybrid_score(score, mem_map[mid]) if hybrid else score
                results.append(RetrievalResult(
                    memory=mem_map[mid], score=s, layer="L2"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.L2_TOP_K]

    def _deep_search(self, query: str, n: int, min_score: float = 0.0,
                     hybrid: bool = False) -> list[RetrievalResult]:
        """
        L3 全量语义检索：直接命中向量库，无任何过滤条件，
        召回率（R@5）由嵌入质量决定，可用 benchmarks/benchmark_recall.py 复现评测。
        hybrid=True 时对结果做 相似度+重要性+时间 融合重排（不改变召回集合）。
        """
        vec = self._emb.embed(query)
        raw = self._vec.query(vec, n=n, min_score=min_score)

        ids = [r[0] for r in raw]
        scores = {r[0]: r[1] for r in raw}
        mem_map = self._kg.get_many(ids)

        results = []
        for mid, score in scores.items():
            if mid in mem_map:
                s = self._hybrid_score(score, mem_map[mid]) if hybrid else score
                results.append(RetrievalResult(
                    memory=mem_map[mid], score=s, layer="L3"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _hybrid_score(self, sim: float, mem: Memory) -> float:
        """融合评分：0.6·相似度 + 0.25·重要性 + 0.15·时间新鲜度（指数衰减）"""
        age_days = max(0.0, time.time() - mem.timestamp) / 86400.0
        recency = 0.5 ** (age_days / self.RECENCY_HALF_LIFE_DAYS)
        return 0.6 * sim + 0.25 * mem.importance + 0.15 * recency

    # ── 搜索 ──────────────────────────────────

    def search(self, query: str, n: int = 10, min_score: float = 0.0,
               hybrid: bool = False) -> list[RetrievalResult]:
        """直接语义搜索，不经过任何结构过滤；hybrid=True 启用融合重排"""
        return self._deep_search(query, n, min_score=min_score, hybrid=hybrid)

    # ── 遗忘曲线 ──────────────────────────────

    def memory_strength(self, memory_id: str) -> float | None:
        """返回某条记忆的当前强度（0-1，新记忆≈1），不存在返回 None。"""
        m = self._kg.get(memory_id)
        if not m:
            return None
        return self._forgetting.strength(m)

    def forgotten(self, threshold: float = 0.2,
                  wing: str | None = None) -> list[Memory]:
        """列出强度已跌破阈值的「已遗忘」记忆（按强度升序，最遗忘的在前）。"""
        now = time.time()
        out = [m for m in self._kg.iter_all(wing=wing)
               if self._forgetting.is_forgotten(m, threshold, now)]
        out.sort(key=lambda m: self._forgetting.strength(m, now))
        return out

    def recall(self, memory_id: str) -> Memory:
        """复习一条记忆：增强其记忆强度（间隔效应减缓遗忘）。"""
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")
        self._forgetting.review(m)
        self._kg.save(m)
        return m

    # ── 主动蒸馏 ──────────────────────────────

    def distill(self, wing: str | None = None, topic: str | None = None,
                min_cluster: int = 2, dry_run: bool = True) -> list[dict]:
        """主动蒸馏：把同一话题下碎片化的多条记忆压缩成一条抽象长期记忆。

        只蒸馏「碎片化」的组：条数达标（≥ min_cluster）且组内无高价值记忆
        （importance < 0.8），避免误伤关键记忆的原始细节。

        dry_run=True 只返回蒸馏计划（默认），dry_run=False 真正写入。
        每条计划含 topic / sources / count / summary / importance。
        """
        groups: dict[str, list[Memory]] = {}
        for m in self._kg.iter_all(wing=wing):
            if m.metadata.get("distilled"):
                continue
            if topic and m.topic != topic:
                continue
            groups.setdefault(m.topic, []).append(m)

        plans: list[dict] = []
        for grp_topic, mems in groups.items():
            if len(mems) < min_cluster:
                continue
            if any(m.importance >= 0.8 for m in mems):
                continue  # 含高价值记忆，保留原始细节不蒸馏
            contents = [m.content for m in mems]
            summary = (self._extractor.summarize(contents)
                       if self._extractor is not None
                       else "；".join(heuristic_summary(c, 60)
                                      for c in contents)[:500])
            plans.append({
                "topic": grp_topic,
                "sources": [m.id for m in mems],
                "count": len(mems),
                "summary": summary,
                "importance": max(m.importance for m in mems),
            })

        if not dry_run:
            for plan in plans:
                self._write_distilled(plan)
        return plans

    def _write_distilled(self, plan: dict) -> Memory:
        """把蒸馏计划落成一条 Memory，并把源记忆标记为已蒸馏。"""
        sources = plan["sources"]
        mem = Memory.create(
            wing=self.wing, topic=plan["topic"],
            content=plan["summary"], importance=plan["importance"],
            ui_summary=plan["summary"],
            metadata={"distilled": True, "sources": sources,
                      "distilled_at": time.time()},
        )
        self._vec.upsert(mem.id, self._emb.embed(mem.content), {"id": mem.id})
        self._kg.save(mem)
        if self._extractor is not None:
            self._ingest_graph(self._extractor.extract(mem.content),
                               mem.id, mem.timestamp)
        for sid in sources:
            src = self._kg.get(sid)
            if src:
                src.metadata["distilled_into"] = mem.id
                self._kg.save(src)
        return mem

    # ── 多跳向量+图谱混合检索 ────────────────

    def graph_search(self, query: str, n: int = 10, hops: int = 2,
                     min_score: float = 0.1) -> dict:
        """多跳混合检索：向量召回种子 → 图谱扩展关联实体 → 汇总证据。

        返回 {"query": ..., "direct": [RetrievalResult...], "graph": [...]}。
        - direct：向量直接命中的记忆
        - graph：经图谱多跳扩展发现的关联记忆，每项含 memory / entities / paths
          （paths 记录「种子实体 → 关联实体」的关系路径，作为可解释证据）
        """
        seeds = self._deep_search(query, n=max(n, 5), min_score=min_score)

        seed_entities: list[str] = []
        for r in seeds:
            for e in r.memory.metadata.get("entities", []) or []:
                if e and e not in seed_entities:
                    seed_entities.append(e)

        seed_ids = {r.memory.id for r in seeds}
        discovered: dict[str, dict] = {}
        for ent in seed_entities:
            for nb in self._graph.neighbors(ent, depth=hops):
                for mid in self._graph.entity_memories(nb["entity"]):
                    if mid in seed_ids:
                        continue
                    m = self._kg.get(mid)
                    if not m:
                        continue
                    hit = discovered.setdefault(
                        mid, {"memory": m, "entities": [], "paths": []})
                    if nb["entity"] not in hit["entities"]:
                        hit["entities"].append(nb["entity"])
                    hit["paths"].append(
                        {"from": ent, "to": nb["entity"], "path": nb["path"]})

        graph_hits = sorted(discovered.values(),
                            key=lambda d: d["memory"].importance, reverse=True)
        return {"query": query, "direct": seeds, "graph": graph_hits}

    # ── 组织层（只供 UI，不影响检索）─────────

    def list_wings(self) -> list[str]:
        return self._kg.list_wings()

    def list_topics(self, wing: str | None = None) -> list[str]:
        return self._kg.list_topics(wing)

    def list_memories(self, wing: str | None = None, topic: str | None = None,
                      limit: int = 50, offset: int = 0,
                      full: bool = False, sort: str = "importance") -> dict:
        """
        向接口方回吐记忆列表（结构化清单，供浏览/挑选）。

        - wing/topic：可选过滤（只影响列表，不影响检索）
        - limit/offset：分页
        - full=True 时包含原文与 metadata
        - sort: "importance"（默认）或 "recent"

        返回 {"memories": [...], "total": N, "limit": L, "offset": O}
        """
        mems, total = self._kg.query_memories(wing, topic, limit, offset, sort)
        return {
            "memories": [memory_to_dict(m, full=full) for m in mems],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def link(self, source_id: str, target_id: str,
             link_type: str = "related", weight: float = 1.0) -> None:
        """手动建立跨 wing 关联（可选）"""
        self._kg.add_link(source_id, target_id, link_type, weight)

    # ── 生命周期管理 ──────────────────────────

    def update(self, memory_id: str, content: str | None = None,
               topic: str | None = None, importance: float | None = None,
               ui_summary: str | None = None,
               metadata: dict | None = None) -> Memory:
        """
        更新一条记忆。content 变化时自动重新向量化，
        向量索引与 SQLite 始终保持一致。
        """
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")

        if content is not None and content != m.content:
            m.content = content
            vec = self._emb.embed(content)
            self._vec.upsert(m.id, vec, {"id": m.id})
        if topic is not None:
            m.topic = topic
        if importance is not None:
            m.importance = max(0.0, min(1.0, importance))
        if ui_summary is not None:
            m.ui_summary = ui_summary
        if metadata:
            m.metadata.update(metadata)

        self._kg.save(m)
        return m

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆（向量索引 + SQLite 同步移除）"""
        if not self._kg.get(memory_id):
            return False
        self._vec.delete(memory_id)
        self._kg.delete(memory_id)
        return True

    def prune(self, min_importance: float | None = None,
              older_than_days: float | None = None,
              wing: str | None = None,
              dry_run: bool = True,
              use_forgetting: bool = False,
              strength_threshold: float = 0.2) -> list[Memory]:
        """
        清理低价值记忆。

        use_forgetting=False（默认）：按 importance 阈值 / 年龄阈值清理。
        use_forgetting=True：按遗忘曲线强度 < strength_threshold 清理 ——
        重要记忆衰减慢、复习过的记忆也会被保留，天然区分「该忘」与「该留」。

        dry_run=True 只返回候选清单不实际删除（默认），
        dry_run=False 真正删除（向量索引同步清理）。
        """
        now = time.time()
        victims: list[Memory] = []
        for m in self._kg.iter_all(wing=wing):
            if use_forgetting:
                if self._forgetting.is_forgotten(m, strength_threshold, now):
                    victims.append(m)
                continue
            if min_importance is not None and m.importance < min_importance:
                victims.append(m)
                continue
            if older_than_days is not None and \
                    (now - m.timestamp) > older_than_days * 86400:
                victims.append(m)

        if not dry_run:
            for m in victims:
                self.delete(m.id)
        return victims

    def reindex(self) -> int:
        """
        从 SQLite 原文重建全部向量索引。

        适用场景：更换/升级嵌入后端后迁移旧数据、
        向量库损坏恢复、Chroma/Numpy 混用切换。
        """
        count = 0
        for m in self._kg.iter_all():
            self._vec.upsert(m.id, self._emb.embed(m.content), {"id": m.id})
            count += 1
        return count

    # ── 防篡改验证（可选区块链）──────────────

    def verify_integrity(self, wing: str | None = None) -> dict:
        """验证哈希链完整性，检测记忆原文是否被篡改"""
        if self._chain is None:
            return {"enabled": False,
                    "message": "区块链未启用（构造时 use_blockchain=True 开启）"}
        wings = [wing] if wing else (self._kg.list_wings() or [self.wing])
        results = {}
        for w in wings:
            ok, msg = self._chain.verify_chain(w)
            results[w] = {"valid": ok, "message": msg}
        return {"enabled": True, "wings": results}

    # ── 时序知识图谱查询 ───────────────────────

    def entity(self, name: str) -> dict | None:
        """查询实体画像：当前属性快照 + 状态时间线 + 关联关系。"""
        return self._graph.get_entity(name)

    def entity_timeline(self, name: str) -> list[dict]:
        """实体各属性的状态演化（按时间升序）。"""
        return self._graph.entity_timeline(name)

    def entity_relations(self, name: str) -> list[dict]:
        """与实体直接相连的所有关系。"""
        return self._graph.entity_relations(name)

    def list_entities(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """按提及次数降序列出全部实体。"""
        return self._graph.list_entities(limit, offset)

    def graph_stats(self) -> dict:
        """时序知识图谱统计（实体 / 状态 / 关系数量）。"""
        return self._graph.stats()

    # ── 统计 ──────────────────────────────────

    def stats(self) -> dict:
        s = self._kg.stats()
        s["vector_backend"] = type(self._vec).__name__
        s["embedding_backend"] = type(self._emb).__name__
        if hasattr(self._vec, "count"):
            s["indexed_vectors"] = self._vec.count()
        if self._chain is not None:
            s["blockchain"] = self._chain.stats()
        s["graph"] = self._graph.stats()
        return s

    # ── 辅助 ──────────────────────────────────

    def _heuristic_importance(self, content: str) -> float:
        """启发式重要性评分（委托给 extractor 模块，保持单处定义）"""
        return heuristic_importance(content)

    def _dedup_candidate(self, content: str) -> Memory | None:
        """返回与 content 语义高度相似的既有记忆（用于去重合并），无则 None。"""
        vec = self._emb.embed(content)
        raw = self._vec.query(vec, n=1, min_score=self.dedup_threshold)
        if not raw:
            return None
        return self._kg.get(raw[0][0])

    def _ingest_graph(self, ext: Extraction | None,
                      memory_id: str, timestamp: float) -> None:
        """把一次抽取结果写入时序知识图谱（ext 为 None 时跳过）。"""
        if ext is None:
            return
        self._graph.ingest(
            ext.entities, ext.relations, ext.entity_states,
            memory_id=memory_id, timestamp=timestamp,
        )

    def close(self):
        self._kg.close()
        self._graph.close()
        if self._chain is not None:
            self._chain.close()
        if hasattr(self._vec, "close"):
            self._vec.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
