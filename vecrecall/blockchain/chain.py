"""
VecRecall Blockchain — Chain 哈希链管理

负责：
  - 区块的持久化存储（SQLite）
  - 哈希链的完整性验证
  - 区块的增删查
  - 大区块的自动组合（小区块数量达到阈值时触发）
  - 日期 + 关键词索引
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from vecrecall.blockchain.block import Block, BlockGroup


# 创世区块哈希（固定值，链的起点）
GENESIS_HASH = "0" * 64

# 小区块自动组合成大区块的阈值
DEFAULT_GROUP_SIZE = 4       # 每 4 个小区块组合一个 L1 大区块
DEFAULT_GROUP_SIZE_L2 = 4    # 每 4 个 L1 大区块组合一个 L2 超大区块


class BlockChain:
    """
    本地离线哈希链。

    存储结构：
      blocks 表      — 所有区块
      block_groups 表 — 大区块（多个小区块的组合）
      kw_index 表    — 关键词倒排索引

    不可篡改性：
      每个区块包含前一个区块的哈希，任何篡改都会导致哈希链断裂，
      verify_chain() 可以检测完整性。
    """

    def __init__(
        self,
        db_path: str,
        group_size: int = DEFAULT_GROUP_SIZE,
        group_size_l2: int = DEFAULT_GROUP_SIZE_L2,
    ):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._group_size = group_size
        self._group_size_l2 = group_size_l2
        self._setup()

    def _setup(self):
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS blocks (
            block_id    TEXT PRIMARY KEY,
            idx         INTEGER NOT NULL,
            timestamp   REAL NOT NULL,
            prev_hash   TEXT NOT NULL,
            content     TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            keywords    TEXT DEFAULT '[]',
            date_label  TEXT NOT NULL,
            wing        TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            trigger     TEXT DEFAULT 'manual',
            hash        TEXT NOT NULL,
            group_id    TEXT DEFAULT '',
            metadata    TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_blocks_wing ON blocks(wing);
        CREATE INDEX IF NOT EXISTS idx_blocks_date ON blocks(date_label);
        CREATE INDEX IF NOT EXISTS idx_blocks_idx  ON blocks(idx);
        CREATE INDEX IF NOT EXISTS idx_blocks_group ON blocks(group_id);

        CREATE TABLE IF NOT EXISTS block_groups (
            group_id      TEXT PRIMARY KEY,
            level         INTEGER NOT NULL,
            block_ids     TEXT NOT NULL,
            timestamp     REAL NOT NULL,
            date_start    TEXT NOT NULL,
            date_end      TEXT NOT NULL,
            total_tokens  INTEGER DEFAULT 0,
            keywords      TEXT DEFAULT '[]',
            wing          TEXT NOT NULL,
            hash          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_groups_wing  ON block_groups(wing);
        CREATE INDEX IF NOT EXISTS idx_groups_level ON block_groups(level);

        CREATE TABLE IF NOT EXISTS kw_index (
            keyword     TEXT NOT NULL,
            block_id    TEXT NOT NULL,
            date_label  TEXT NOT NULL,
            wing        TEXT NOT NULL,
            PRIMARY KEY (keyword, block_id)
        );
        CREATE INDEX IF NOT EXISTS idx_kw ON kw_index(keyword);
        CREATE INDEX IF NOT EXISTS idx_kw_date ON kw_index(date_label);
        """)
        self._conn.commit()

    # ── 写入 ──────────────────────────────────

    def add_block(self, block: Block) -> Block:
        """添加区块到链上，同时更新关键词索引"""
        self._conn.execute("""
        INSERT OR REPLACE INTO blocks
          (block_id, idx, timestamp, prev_hash, content, token_count,
           keywords, date_label, wing, session_id, trigger, hash, group_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block.block_id, block.index, block.timestamp, block.prev_hash,
            block.content, block.token_count,
            json.dumps(block.keywords, ensure_ascii=False),
            block.date_label, block.wing, block.session_id,
            block.trigger, block.hash, block.group_id,
            json.dumps(block.metadata, ensure_ascii=False),
        ))

        # 更新关键词倒排索引
        for kw in block.keywords:
            self._conn.execute("""
            INSERT OR REPLACE INTO kw_index (keyword, block_id, date_label, wing)
            VALUES (?, ?, ?, ?)
            """, (kw.lower(), block.block_id, block.date_label, block.wing))

        self._conn.commit()

        # 检查是否需要自动组合大区块
        self._maybe_group(block.wing)

        return block

    def new_block(
        self,
        content: str,
        wing: str = "default",
        session_id: str = "",
        trigger: str = "manual",
        keywords: list[str] | None = None,
        metadata: dict | None = None,
    ) -> Block:
        """创建并存储新区块"""
        prev = self.get_latest_block(wing)
        prev_hash = prev.hash if prev else GENESIS_HASH
        index = (prev.index + 1) if prev else 0

        block = Block.create(
            index=index,
            prev_hash=prev_hash,
            content=content,
            wing=wing,
            session_id=session_id,
            trigger=trigger,
            keywords=keywords or [],
            metadata=metadata or {},
        )
        return self.add_block(block)

    # ── 查询 ──────────────────────────────────

    def get_block(self, block_id: str) -> Optional[Block]:
        row = self._conn.execute(
            "SELECT * FROM blocks WHERE block_id=?", (block_id,)
        ).fetchone()
        return self._row_to_block(row) if row else None

    def get_latest_block(self, wing: str = "default") -> Optional[Block]:
        row = self._conn.execute(
            "SELECT * FROM blocks WHERE wing=? ORDER BY idx DESC LIMIT 1", (wing,)
        ).fetchone()
        return self._row_to_block(row) if row else None

    def get_blocks_by_wing(self, wing: str, limit: int = 50) -> list[Block]:
        rows = self._conn.execute(
            "SELECT * FROM blocks WHERE wing=? ORDER BY idx DESC LIMIT ?",
            (wing, limit)
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def get_blocks_by_date(self, date_label: str, wing: str | None = None) -> list[Block]:
        if wing:
            rows = self._conn.execute(
                "SELECT * FROM blocks WHERE date_label=? AND wing=? ORDER BY idx",
                (date_label, wing)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM blocks WHERE date_label=? ORDER BY idx",
                (date_label,)
            ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def search_by_keywords(
        self,
        keywords: list[str],
        wing: str | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        limit: int = 20,
    ) -> list[Block]:
        """按关键词检索区块（日期+关键词索引）"""
        if not keywords:
            return []

        placeholders = ",".join("?" * len(keywords))
        params = [kw.lower() for kw in keywords]

        query = f"""
        SELECT b.*, COUNT(k.keyword) as match_count
        FROM blocks b
        JOIN kw_index k ON b.block_id = k.block_id
        WHERE k.keyword IN ({placeholders})
        """
        if wing:
            query += " AND b.wing=?"
            params.append(wing)
        if date_start:
            query += " AND b.date_label>=?"
            params.append(date_start)
        if date_end:
            query += " AND b.date_label<=?"
            params.append(date_end)

        query += f" GROUP BY b.block_id ORDER BY match_count DESC, b.idx DESC LIMIT {limit}"

        rows = self._conn.execute(query, params).fetchall()
        # rows 多了一列 match_count，取前面的列
        return [self._row_to_block(r[:14]) for r in rows]

    def get_ungrouped_blocks(self, wing: str, level: int = 0) -> list[Block]:
        """获取还没有归入大区块的小区块"""
        rows = self._conn.execute(
            "SELECT * FROM blocks WHERE wing=? AND group_id='' ORDER BY idx",
            (wing,)
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    # ── 大区块 ────────────────────────────────

    def _maybe_group(self, wing: str):
        """检查是否需要自动组合大区块"""
        ungrouped = self.get_ungrouped_blocks(wing)
        if len(ungrouped) >= self._group_size:
            # 取最早的 group_size 个组合
            to_group = ungrouped[:self._group_size]
            self._create_group(to_group, level=1, wing=wing)

        # 检查 L1 大区块是否需要组合成 L2
        l1_ungrouped = self._get_ungrouped_groups(wing, level=1)
        if len(l1_ungrouped) >= self._group_size_l2:
            self._create_group_from_groups(l1_ungrouped[:self._group_size_l2], level=2, wing=wing)

    def _create_group(self, blocks: list[Block], level: int, wing: str) -> BlockGroup:
        """把多个小区块组合成大区块"""
        group = BlockGroup.create(blocks, level=level, wing=wing)

        # 存储大区块
        self._conn.execute("""
        INSERT OR REPLACE INTO block_groups
          (group_id, level, block_ids, timestamp, date_start, date_end,
           total_tokens, keywords, wing, hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group.group_id, group.level,
            json.dumps(group.block_ids),
            group.timestamp,
            group.date_range[0], group.date_range[1],
            group.total_tokens,
            json.dumps(group.keywords, ensure_ascii=False),
            group.wing, group.hash,
        ))

        # 更新小区块的 group_id
        for block in blocks:
            self._conn.execute(
                "UPDATE blocks SET group_id=? WHERE block_id=?",
                (group.group_id, block.block_id)
            )

        self._conn.commit()
        return group

    def _create_group_from_groups(
        self, groups: list[BlockGroup], level: int, wing: str
    ) -> BlockGroup:
        """把多个大区块组合成更大的区块"""
        now = time.time()
        dates = sorted(set(g.date_range[0] for g in groups) | set(g.date_range[1] for g in groups))
        all_kw = []
        for g in groups:
            all_kw.extend(g.keywords)
        kw_freq = {}
        for kw in all_kw:
            kw_freq[kw] = kw_freq.get(kw, 0) + 1
        top_kw = sorted(kw_freq, key=lambda k: kw_freq[k], reverse=True)[:20]

        from vecrecall.blockchain.block import BlockGroup as BG
        import uuid, hashlib, json
        meta_group = BG(
            group_id=str(uuid.uuid4()),
            level=level,
            block_ids=[g.group_id for g in groups],
            timestamp=now,
            date_range=(dates[0], dates[-1]),
            total_tokens=sum(g.total_tokens for g in groups),
            keywords=top_kw,
            wing=wing,
        )

        self._conn.execute("""
        INSERT OR REPLACE INTO block_groups
          (group_id, level, block_ids, timestamp, date_start, date_end,
           total_tokens, keywords, wing, hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meta_group.group_id, meta_group.level,
            json.dumps(meta_group.block_ids),
            meta_group.timestamp,
            meta_group.date_range[0], meta_group.date_range[1],
            meta_group.total_tokens,
            json.dumps(meta_group.keywords, ensure_ascii=False),
            meta_group.wing, meta_group.hash,
        ))
        self._conn.commit()
        return meta_group

    def _get_ungrouped_groups(self, wing: str, level: int) -> list[BlockGroup]:
        """获取还没有归入上层大区块的大区块"""
        # 找出 level 层中没有被更高层引用的大区块
        all_l_groups = self._conn.execute(
            "SELECT * FROM block_groups WHERE wing=? AND level=?",
            (wing, level)
        ).fetchall()

        higher_groups = self._conn.execute(
            "SELECT block_ids FROM block_groups WHERE wing=? AND level>?",
            (wing, level)
        ).fetchall()

        referenced_ids = set()
        for (ids_json,) in higher_groups:
            referenced_ids.update(json.loads(ids_json))

        ungrouped = []
        for row in all_l_groups:
            if row[0] not in referenced_ids:
                ungrouped.append(self._row_to_group(row))
        return ungrouped

    # ── 验证 ──────────────────────────────────

    def verify_chain(self, wing: str = "default") -> tuple[bool, str]:
        """验证哈希链完整性，返回 (是否完整, 错误信息)"""
        blocks = self._conn.execute(
            "SELECT * FROM blocks WHERE wing=? ORDER BY idx ASC", (wing,)
        ).fetchall()

        if not blocks:
            return True, "链为空"

        prev_hash = GENESIS_HASH
        for i, row in enumerate(blocks):
            block = self._row_to_block(row)

            # 验证哈希
            if not block.verify():
                return False, f"区块 #{block.index} 哈希验证失败，数据可能被篡改"

            # 验证链接
            if block.prev_hash != prev_hash:
                return False, f"区块 #{block.index} 前向哈希断裂"

            prev_hash = block.hash

        return True, f"链完整，共 {len(blocks)} 个区块"

    # ── 统计 ──────────────────────────────────

    def stats(self, wing: str | None = None) -> dict:
        if wing:
            total_blocks = self._conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE wing=?", (wing,)
            ).fetchone()[0]
            total_tokens = self._conn.execute(
                "SELECT SUM(token_count) FROM blocks WHERE wing=?", (wing,)
            ).fetchone()[0] or 0
            groups_l1 = self._conn.execute(
                "SELECT COUNT(*) FROM block_groups WHERE wing=? AND level=1", (wing,)
            ).fetchone()[0]
            groups_l2 = self._conn.execute(
                "SELECT COUNT(*) FROM block_groups WHERE wing=? AND level=2", (wing,)
            ).fetchone()[0]
        else:
            total_blocks = self._conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
            total_tokens = self._conn.execute("SELECT SUM(token_count) FROM blocks").fetchone()[0] or 0
            groups_l1 = self._conn.execute("SELECT COUNT(*) FROM block_groups WHERE level=1").fetchone()[0]
            groups_l2 = self._conn.execute("SELECT COUNT(*) FROM block_groups WHERE level=2").fetchone()[0]

        return {
            "total_blocks": total_blocks,
            "total_tokens": total_tokens,
            "groups_l1": groups_l1,
            "groups_l2": groups_l2,
            "db_size_kb": round(os.path.getsize(self._db_path) / 1024, 1),
        }

    def list_dates(self, wing: str | None = None) -> list[str]:
        if wing:
            rows = self._conn.execute(
                "SELECT DISTINCT date_label FROM blocks WHERE wing=? ORDER BY date_label DESC",
                (wing,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT DISTINCT date_label FROM blocks ORDER BY date_label DESC"
            ).fetchall()
        return [r[0] for r in rows]

    # ── 内部工具 ──────────────────────────────

    def _row_to_block(self, row) -> Block:
        return Block(
            block_id=row[0], index=row[1], timestamp=row[2],
            prev_hash=row[3], content=row[4], token_count=row[5],
            keywords=json.loads(row[6]), date_label=row[7],
            wing=row[8], session_id=row[9], trigger=row[10],
            hash=row[11], group_id=row[12],
            metadata=json.loads(row[13]) if row[13] else {},
        )

    def _row_to_group(self, row) -> BlockGroup:
        return BlockGroup(
            group_id=row[0], level=row[1],
            block_ids=json.loads(row[2]),
            timestamp=row[3],
            date_range=(row[4], row[5]),
            total_tokens=row[6],
            keywords=json.loads(row[7]),
            wing=row[8], hash=row[9],
        )

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
