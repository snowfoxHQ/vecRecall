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
    """内存向量后端（不持久化），适合开发和测试。

    装了 numpy 时用矩阵计算，否则纯 Python；见 core/dense.py。
    """

    def __init__(self, use_numpy: bool | None = None):
        from vecrecall.core.dense import DenseIndex
        self._index = DenseIndex(use_numpy)

    def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
        self._index.upsert(memory_id, vector, payload, keep_position=True)

    def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
        return self._index.query(vector, n, min_score)

    def delete(self, memory_id: str) -> None:
        self._index.delete(memory_id)

    def count(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        self._index.clear()


def _decode_vec(dim: int, blob: bytes) -> tuple[float, ...]:
    """v2.4 起新写入为 float32（每维 4 字节），旧库为 float64（8 字节），按长度识别。"""
    if len(blob) == dim * 4:
        return struct.unpack(f"<{dim}f", blob)
    return struct.unpack(f"<{dim}d", blob)


class SqliteVectorBackend(VectorBackend):
    """
    零依赖持久化向量后端（SQLite 存储，重启不丢数据）。

    与 NumpyVectorBackend 的区别：
      - NumpyVectorBackend 纯内存，进程退出即丢失全部向量索引
      - 本后端把向量以二进制 BLOB 存入 SQLite，默认安装下记忆可跨会话检索

    v2.4：首次查询时把全部向量载入内存索引（core/dense.py），之后增量维护，
    查询不再每次读库、逐条解码。装了 numpy 时用矩阵计算。
    其他连接（另一个进程）写入同一个库时，通过 PRAGMA data_version 发现并重新载入。
    新写入的向量存为 float32，旧库的 float64 向量照常读取。
    """

    def __init__(self, db_path: str, use_numpy: bool | None = None):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL,
            payload TEXT DEFAULT '{}'
        )""")
        self._conn.commit()
        self._use_numpy = use_numpy
        self._index = None
        self._data_version = None

    def _version(self) -> int:
        return self._conn.execute("PRAGMA data_version").fetchone()[0]

    def _ensure_index(self):
        ver = self._version()
        if self._index is not None and ver == self._data_version:
            return self._index
        from vecrecall.core.dense import DenseIndex
        idx = DenseIndex(self._use_numpy)
        for seq, (mid, dim, blob, payload) in enumerate(self._conn.execute(
                "SELECT id, dim, vec, payload FROM vectors ORDER BY rowid")):
            idx.upsert(mid, _decode_vec(dim, blob), json.loads(payload), seq=seq)
        self._index = idx
        self._data_version = ver
        return idx

    def _sync_version(self) -> None:
        if self._index is not None:
            self._data_version = self._version()

    def upsert(self, memory_id: str, vector: list[float], payload: dict) -> None:
        blob = struct.pack(f"<{len(vector)}f", *vector)
        self._conn.execute(
            "INSERT OR REPLACE INTO vectors (id, dim, vec, payload) VALUES (?, ?, ?, ?)",
            (memory_id, len(vector), blob,
             json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()
        if self._index is not None:
            stale = self._version() != self._data_version
            if stale:
                self._index = None              # 别的进程也写过，下次查询整体重载
            else:
                # INSERT OR REPLACE 会把已有行移到末尾，排序上与重新读库一致
                self._index.upsert(memory_id, vector, payload, keep_position=False)

    def query(self, vector: list[float], n: int, min_score: float = 0.0) -> list[tuple[str, float, dict]]:
        return self._ensure_index().query(vector, n, min_score)

    def delete(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM vectors WHERE id=?", (memory_id,))
        self._conn.commit()
        if self._index is not None:
            if self._version() != self._data_version:
                self._index = None
            else:
                self._index.delete(memory_id)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM vectors")
        self._conn.commit()
        self._index = None

    def close(self) -> None:
        self._index = None
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
