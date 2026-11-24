"""向量后端（可插拔）：Numpy / SQLite（默认，零依赖持久化）/ ChromaDB（可选）。"""

from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sqlite3  # noqa: F401
import struct  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
import urllib.error  # noqa: F401
import urllib.request  # noqa: F401
from dataclasses import dataclass, field, asdict  # noqa: F401
from typing import Iterator, Optional  # noqa: F401


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
