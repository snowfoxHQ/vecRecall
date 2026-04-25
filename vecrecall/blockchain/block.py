"""
VecRecall Blockchain — Block 区块数据结构

每个区块对应一个上下文窗口的完整存档。

区块结构：
  - block_id      唯一标识
  - index         区块序号（0, 1, 2...）
  - timestamp     创建时间
  - prev_hash     前一个区块的哈希（哈希链）
  - content       完整原文（逐字存储）
  - token_count   token 数量估算
  - keywords      关键词列表（用于检索）
  - date_label    日期标签 YYYY-MM-DD
  - wing          所属 wing
  - session_id    来源会话 ID
  - trigger       触发方式（auto_75/manual/compaction）
  - hash          本区块哈希（由以上所有字段生成）
  - group_id      所属大区块 ID（多个小区块组合时使用）
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Block:
    """单个上下文区块"""
    block_id: str
    index: int
    timestamp: float
    prev_hash: str
    content: str
    token_count: int
    keywords: list[str]
    date_label: str
    wing: str
    session_id: str
    trigger: str          # auto_75 | manual | compaction | session_end
    hash: str = ""
    group_id: str = ""    # 归属的大区块 ID，空表示未归组
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """基于区块所有核心字段生成哈希，保证不可篡改"""
        data = {
            "block_id": self.block_id,
            "index": self.index,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "content": self.content,
            "token_count": self.token_count,
            "keywords": sorted(self.keywords),
            "date_label": self.date_label,
            "wing": self.wing,
            "session_id": self.session_id,
            "trigger": self.trigger,
        }
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def verify(self) -> bool:
        """验证区块哈希是否被篡改"""
        return self.hash == self._compute_hash()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(**d)

    @classmethod
    def create(
        cls,
        index: int,
        prev_hash: str,
        content: str,
        wing: str = "default",
        session_id: str = "",
        trigger: str = "manual",
        keywords: list[str] | None = None,
        metadata: dict | None = None,
    ) -> "Block":
        now = time.time()
        date_label = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        token_count = len(content) // 4  # 粗略估算：1 token ≈ 4 字符

        return cls(
            block_id=str(uuid.uuid4()),
            index=index,
            timestamp=now,
            prev_hash=prev_hash,
            content=content,
            token_count=token_count,
            keywords=keywords or [],
            date_label=date_label,
            wing=wing,
            session_id=session_id or str(uuid.uuid4()),
            trigger=trigger,
            metadata=metadata or {},
        )

    @property
    def summary(self) -> str:
        """区块摘要，用于展示"""
        kb = len(self.content.encode('utf-8')) / 1024
        return (f"Block #{self.index} | {self.date_label} | "
                f"{self.token_count:,} tokens | {kb:.1f}KB | "
                f"trigger={self.trigger} | "
                f"keywords=[{', '.join(self.keywords[:5])}]")


@dataclass
class BlockGroup:
    """
    大区块：多个小区块的组合。
    当小区块数量达到阈值时，自动组合成大区块。
    大区块可以继续组合成更大的区块（树状结构）。
    """
    group_id: str
    level: int              # 层级：1=小区块组，2=大区块组，3=超大区块组...
    block_ids: list[str]    # 包含的区块/子组 ID
    timestamp: float
    date_range: tuple[str, str]   # (开始日期, 结束日期)
    total_tokens: int
    keywords: list[str]           # 合并所有子块关键词
    wing: str
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        data = {
            "group_id": self.group_id,
            "level": self.level,
            "block_ids": self.block_ids,
            "timestamp": self.timestamp,
            "date_range": list(self.date_range),
            "total_tokens": self.total_tokens,
            "wing": self.wing,
        }
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d['date_range'] = list(self.date_range)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BlockGroup":
        d['date_range'] = tuple(d['date_range'])
        return cls(**d)

    @classmethod
    def create(
        cls,
        blocks: list[Block],
        level: int = 1,
        wing: str = "default",
    ) -> "BlockGroup":
        now = time.time()
        dates = sorted(set(b.date_label for b in blocks))
        all_keywords = []
        for b in blocks:
            all_keywords.extend(b.keywords)
        # 去重保留高频关键词
        kw_freq = {}
        for kw in all_keywords:
            kw_freq[kw] = kw_freq.get(kw, 0) + 1
        top_keywords = sorted(kw_freq, key=lambda k: kw_freq[k], reverse=True)[:20]

        return cls(
            group_id=str(uuid.uuid4()),
            level=level,
            block_ids=[b.block_id for b in blocks],
            timestamp=now,
            date_range=(dates[0] if dates else "", dates[-1] if dates else ""),
            total_tokens=sum(b.token_count for b in blocks),
            keywords=top_keywords,
            wing=wing,
        )

    @property
    def summary(self) -> str:
        return (f"Group L{self.level} | {self.date_range[0]} ~ {self.date_range[1]} | "
                f"{len(self.block_ids)} blocks | {self.total_tokens:,} tokens | "
                f"keywords=[{', '.join(self.keywords[:5])}]")
