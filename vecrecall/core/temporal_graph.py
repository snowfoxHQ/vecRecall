"""
时序知识图谱（Temporal Knowledge Graph）。

在「LLM 认知层」抽取出实体/关系/状态的基础上，构建一张带时间戳的知识图谱，
记录实体的出现、实体间的关联，以及实体属性随时间的演化（谁在什么时候是什么状态）。

三张表：
  entities       实体节点（规范化名 → 展示名、首次/最近出现、提及次数、当前属性快照）
  entity_states  实体属性状态（某实体某属性在某个时间点的值，用于追溯演化）
  relations      实体间关系（source/target/type，带时间戳，同边去重保留最新）

零依赖：纯标准库 sqlite3，与 KnowledgeGraph / 向量后端风格一致。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid


def normalize_entity(name: str) -> str:
    """规范化实体名：小写 + 折叠空白，作为主键保证查询稳定。"""
    return " ".join(str(name).strip().lower().split())


class TemporalGraph:
    """时序知识图谱存储层，独立于向量检索与记忆元数据。"""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._setup()

    def _setup(self):
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            display TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            mentions INTEGER DEFAULT 1,
            attributes TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS entity_states (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            attribute TEXT NOT NULL,
            value TEXT NOT NULL,
            timestamp REAL NOT NULL,
            memory_id TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_states_entity
            ON entity_states(entity_id, attribute, timestamp);

        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT DEFAULT 'related',
            timestamp REAL NOT NULL,
            memory_id TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source);
        CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target);

        CREATE TABLE IF NOT EXISTS entity_mentions (
            entity_id TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            PRIMARY KEY (entity_id, memory_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mentions_mem ON entity_mentions(memory_id);
        """)
        self._conn.commit()

    # ── 写入 ──────────────────────────────────

    def ingest(self, entities: list[str], relations: list[dict],
               entity_states: list[dict], memory_id: str = "",
               timestamp: float | None = None) -> None:
        """把一次记忆抽取的结果写入图谱（entities/relations/entity_states 均可为空）。

        同一记忆里多次出现的实体只登记一次，保证 mentions 表示「被几条记忆提及」。
        """
        ts = timestamp if timestamp is not None else time.time()

        # 本次出现的实体去重后统一登记
        seen: dict[str, str] = {}  # 规范化名 -> 首次展示名

        def add_name(name):
            name = str(name).strip()
            if name:
                nid = normalize_entity(name)
                seen.setdefault(nid, name)

        for e in entities or []:
            add_name(e)
        for r in relations or []:
            if isinstance(r, dict):
                add_name(r.get("source", ""))
                add_name(r.get("target", ""))
        for s in entity_states or []:
            if isinstance(s, dict):
                add_name(s.get("entity", ""))

        for display in seen.values():
            self._touch_entity(display, ts)

        # 记录「实体 → 记忆」映射，供多跳检索反查
        if memory_id:
            for nid in seen:
                self._conn.execute(
                    "INSERT OR REPLACE INTO entity_mentions"
                    " (entity_id, memory_id, timestamp) VALUES (?, ?, ?)",
                    (nid, memory_id, ts),
                )
            self._conn.commit()

        for r in relations or []:
            if not isinstance(r, dict):
                continue
            src = str(r.get("source", "")).strip()
            tgt = str(r.get("target", "")).strip()
            typ = str(r.get("type", "related")).strip() or "related"
            if not src or not tgt:
                continue
            self._add_relation(src, tgt, typ, ts, memory_id)

        for s in entity_states or []:
            if not isinstance(s, dict):
                continue
            ent = str(s.get("entity", "")).strip()
            attr = str(s.get("attribute", "")).strip()
            value = str(s.get("value", "")).strip()
            if not ent or not attr:
                continue
            self._add_state(ent, attr, value, ts, memory_id)

    def _touch_entity(self, name: str, ts: float) -> str:
        nid = normalize_entity(name)
        row = self._conn.execute(
            "SELECT display FROM entities WHERE id=?", (nid,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO entities (id, display, first_seen, last_seen, mentions)"
                " VALUES (?, ?, ?, ?, 1)",
                (nid, name, ts, ts),
            )
        else:
            self._conn.execute(
                "UPDATE entities SET last_seen=MAX(last_seen, ?),"
                " mentions=mentions+1 WHERE id=?",
                (ts, nid),
            )
        self._conn.commit()
        return nid

    def _add_state(self, entity: str, attribute: str, value: str,
                   ts: float, memory_id: str) -> None:
        nid = normalize_entity(entity)
        self._conn.execute(
            "INSERT INTO entity_states (id, entity_id, attribute, value,"
            " timestamp, memory_id) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), nid, attribute, value, ts, memory_id),
        )
        # 更新实体当前属性快照（最后一个值生效）
        attrs = self._attributes(nid)
        attrs[attribute] = value
        self._conn.execute(
            "UPDATE entities SET attributes=? WHERE id=?",
            (json.dumps(attrs, ensure_ascii=False), nid),
        )
        self._conn.commit()

    def _add_relation(self, source: str, target: str, typ: str,
                      ts: float, memory_id: str) -> None:
        sid, tid = normalize_entity(source), normalize_entity(target)
        if sid == tid:
            return
        # 同一条边（source/target/type）只保留最新一条，避免膨胀
        rid = hashlib.sha1(f"{sid}|{tid}|{typ}".encode("utf-8")).hexdigest()[:32]
        self._conn.execute(
            "INSERT OR REPLACE INTO relations (id, source, target, type,"
            " timestamp, memory_id) VALUES (?, ?, ?, ?, ?, ?)",
            (rid, sid, tid, typ, ts, memory_id),
        )
        self._conn.commit()

    def _attributes(self, nid: str) -> dict:
        row = self._conn.execute(
            "SELECT attributes FROM entities WHERE id=?", (nid,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def _display(self, nid: str) -> str:
        row = self._conn.execute(
            "SELECT display FROM entities WHERE id=?", (nid,)
        ).fetchone()
        return row[0] if row else nid

    # ── 查询 ──────────────────────────────────

    def get_entity(self, entity: str) -> dict | None:
        """返回实体的完整画像：当前属性 + 状态时间线 + 关联关系。"""
        nid = normalize_entity(entity)
        row = self._conn.execute(
            "SELECT id, display, first_seen, last_seen, mentions, attributes"
            " FROM entities WHERE id=?", (nid,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "display": row[1],
            "first_seen": row[2],
            "last_seen": row[3],
            "mentions": row[4],
            "attributes": json.loads(row[5]),
            "timeline": self.entity_timeline(entity),
            "relations": self.entity_relations(entity),
        }

    def entity_timeline(self, entity: str) -> list[dict]:
        """实体各属性的状态演化，按时间升序。"""
        nid = normalize_entity(entity)
        rows = self._conn.execute(
            "SELECT attribute, value, timestamp, memory_id FROM entity_states"
            " WHERE entity_id=? ORDER BY timestamp, rowid", (nid,)
        ).fetchall()
        return [
            {"attribute": r[0], "value": r[1], "timestamp": r[2], "memory_id": r[3]}
            for r in rows
        ]

    def entity_relations(self, entity: str) -> list[dict]:
        """与该实体直接相连的所有关系（source 或 target）。"""
        nid = normalize_entity(entity)
        rows = self._conn.execute(
            "SELECT source, target, type, timestamp FROM relations"
            " WHERE source=? OR target=? ORDER BY timestamp DESC",
            (nid, nid),
        ).fetchall()
        out = []
        for src, tgt, typ, ts in rows:
            out.append({
                "source": self._display(src),
                "target": self._display(tgt),
                "type": typ,
                "timestamp": ts,
            })
        return out

    def list_entities(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """按提及次数降序列出实体。"""
        rows = self._conn.execute(
            "SELECT id, display, mentions, last_seen, attributes FROM entities"
            " ORDER BY mentions DESC, last_seen DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {"id": r[0], "display": r[1], "mentions": r[2],
             "last_seen": r[3], "attributes": json.loads(r[4])}
            for r in rows
        ]

    def neighbors(self, entity: str, depth: int = 1) -> list[dict]:
        """BFS 返回 depth 跳内可达的邻居实体，附带最短路径与关系类型。

        供多跳混合检索用：从种子实体沿关系图向外扩展关联实体。
        """
        start = normalize_entity(entity)
        if not self._conn.execute(
                "SELECT 1 FROM entities WHERE id=?", (start,)).fetchone():
            return []

        # 无向邻接表：实体 -> [(邻居, 关系类型)]
        adj: dict[str, list[tuple[str, str]]] = {}
        for src, tgt, typ in self._conn.execute(
                "SELECT source, target, type FROM relations").fetchall():
            adj.setdefault(src, []).append((tgt, typ))
            adj.setdefault(tgt, []).append((src, typ))

        visited = {start}
        frontier = {start}
        parent: dict[str, tuple[str, str]] = {}  # 实体 -> (来源实体, 关系类型)

        for _ in range(max(0, depth)):
            nxt: set[str] = set()
            for node in frontier:
                for nb, typ in adj.get(node, []):
                    if nb not in visited:
                        visited.add(nb)
                        parent[nb] = (node, typ)
                        nxt.add(nb)
            frontier = nxt
            if not frontier:
                break

        result: list[dict] = []
        for node in visited:
            if node == start:
                continue
            path: list[dict] = []
            cur = node
            while cur != start:
                prev, typ = parent[cur]
                path.append({"from": self._display(prev),
                             "to": self._display(cur), "type": typ})
                cur = prev
            path.reverse()
            result.append({"entity": self._display(node),
                           "depth": len(path), "path": path})
        result.sort(key=lambda x: (x["depth"], x["entity"]))
        return result

    def entity_memories(self, entity: str) -> list[str]:
        """返回提及该实体的记忆 id（按时间降序）。"""
        nid = normalize_entity(entity)
        rows = self._conn.execute(
            "SELECT memory_id FROM entity_mentions WHERE entity_id=?"
            " ORDER BY timestamp DESC", (nid,)
        ).fetchall()
        return [r[0] for r in rows if r[0]]

    def memory_entities(self, memory_id: str) -> list[str]:
        """返回某条记忆提及的实体展示名。"""
        rows = self._conn.execute(
            "SELECT entity_id FROM entity_mentions WHERE memory_id=?",
            (memory_id,)
        ).fetchall()
        return [self._display(r[0]) for r in rows]

    def stats(self) -> dict:
        entities = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        states = self._conn.execute("SELECT COUNT(*) FROM entity_states").fetchone()[0]
        relations = self._conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        return {"entities": entities, "entity_states": states, "relations": relations}

    def close(self):
        self._conn.close()
