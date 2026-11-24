"""记忆持久化：KnowledgeGraph（SQLite 元数据、原文、跨 wing 关联），不参与检索路径。"""

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

from vecrecall.core.crypto import Cipher
from vecrecall.core.models import Memory


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
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """老库无痛升级：为 memories 表补齐新字段列（存在即跳过）。"""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)")}
        migrations = [
            ("memory_type", "TEXT DEFAULT 'episodic'"),
            ("visibility", "TEXT DEFAULT 'private'"),
            ("owner", "TEXT DEFAULT ''"),
            ("retention_policy", "TEXT DEFAULT 'decay'"),
            ("topic_key", "TEXT DEFAULT ''"),
        ]
        for col, ddl in migrations:
            if col not in cols:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {ddl}")
        # 阶段 C/B/F 检索过滤要用的索引
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_type ON memories(memory_type)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_retention ON memories(retention_policy)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_visibility ON memories(visibility)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic_key ON memories(topic_key)")

    def save(self, memory: Memory) -> None:
        content, ui_summary = memory.content, memory.ui_summary
        meta_raw = json.dumps(memory.metadata)
        if self._cipher is not None:
            content = self._cipher.encrypt(content)
            ui_summary = self._cipher.encrypt(ui_summary)
            meta_raw = self._cipher.encrypt(meta_raw)
        self._conn.execute("""
        INSERT OR REPLACE INTO memories
          (id, wing, topic, content, timestamp, session_id, importance,
           ui_summary, metadata, memory_type, visibility, owner,
           retention_policy, topic_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory.id, memory.wing, memory.topic, content,
              memory.timestamp, memory.session_id, memory.importance,
              ui_summary, meta_raw,
              memory.memory_type, memory.visibility, memory.owner,
              memory.retention_policy, memory.topic_key))
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

    def list_by_retention(self, policy: str) -> list[Memory]:
        """按留存策略取记忆（immutable 等豁免路径专用）。"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE retention_policy=? "
            "ORDER BY importance DESC, timestamp DESC", (policy,)
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def list_by_type(self, memory_type: str) -> list[Memory]:
        """按语义类型取记忆（procedural 精确匹配路径专用）。"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE memory_type=? "
            "ORDER BY importance DESC, timestamp DESC", (memory_type,)
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def list_by_topic_key_prefix(self, prefix: str,
                                 wing: str | None = None) -> list[Memory]:
        """按分链键前缀取记忆（时间升序，即链写入顺序）。"""
        if wing:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE wing=? AND topic_key LIKE ? "
                "ORDER BY timestamp ASC",
                (wing, prefix + "%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE topic_key LIKE ? "
                "ORDER BY timestamp ASC", (prefix + "%",),
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
        # 新列在 migration 之后必然存在；len 防御外部传入的旧结构 row
        return Memory(
            id=row[0], wing=row[1], topic=row[2], content=content,
            timestamp=row[4], session_id=row[5], importance=row[6],
            ui_summary=ui_summary, metadata=json.loads(meta_raw),
            memory_type=row[9] if len(row) > 9 else "episodic",
            visibility=row[10] if len(row) > 10 else "private",
            owner=(row[11] or "") if len(row) > 11 else "",
            retention_policy=row[12] if len(row) > 12 else "decay",
            topic_key=(row[13] or "") if len(row) > 13 else "",
        )

    def close(self):
        self._conn.close()
