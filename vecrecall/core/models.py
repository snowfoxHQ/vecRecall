"""数据模型：Memory / RetrievalResult / ContextBundle 与序列化工具。"""

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


def status_fields(m) -> dict:
    """检索结果里给 Agent 看的状态与来源摘要（v2.5）。

    superseded_by：这条记忆已被另一条取代（信息可能过时），没有时不出现；
    source：写入入口 / agent / 会话 / 轮次 / 调用方补充的 source_ref，旧记忆没有时不出现。
    """
    md = m.metadata or {}
    out: dict = {}
    if md.get("superseded_by"):
        out["superseded_by"] = md["superseded_by"]
    if md.get("supersedes"):
        out["supersedes"] = list(md["supersedes"])
    pv = md.get("provenance")
    if isinstance(pv, dict):
        src = {k: pv[k] for k in ("via", "agent", "session_id", "turn_index", "source_ref")
               if k in pv}
        if src:
            out["source"] = src
    return out


def graph_search_to_dict(res: dict) -> dict:
    """graph_search() 结果序列化（MCP / REST 共用）。"""
    return {
        "query": res.get("query"),
        "direct": [{**memory_to_dict(r.memory), "score": round(r.score, 4)}
                   for r in res.get("direct", [])],
        "graph": [{**memory_to_dict(h["memory"]), "entities": h.get("entities", []),
                   "paths": h.get("paths", [])[:3]}
                  for h in res.get("graph", [])],
    }


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
        "memory_type": m.memory_type,
        "visibility": m.visibility,
        "retention_policy": m.retention_policy,
        "topic_key": m.topic_key,
    }
    d.update(status_fields(m))
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
    # ── 语义类型维度（与 L0-L3 加载策略维度正交）──
    memory_type: str = "episodic"       # episodic / semantic / procedural
    # ── 双层隔离 ──
    visibility: str = "private"         # private / shared
    owner: str = ""                     # 写入者标识（agent id / user id）
    # ── 留存策略 ──
    retention_policy: str = "decay"     # decay（艾宾浩斯衰减）/ immutable（豁免）
    # ── 分链键 ──
    topic_key: str = ""                 # 哈希链分链键（如 contract:<id>），空=默认链

    @classmethod
    def create(cls, wing: str, topic: str, content: str,
               session_id: str = "", importance: float = 0.5,
               ui_summary: str = "", metadata: dict | None = None,
               memory_type: str = "episodic", visibility: str = "private",
               owner: str = "", retention_policy: str = "decay",
               topic_key: str = "") -> "Memory":
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
            memory_type=memory_type,
            visibility=visibility,
            owner=owner,
            retention_policy=retention_policy,
            topic_key=topic_key,
        )


@dataclass
class RetrievalResult:
    memory: Memory
    score: float        # 余弦相似度，0-1
    layer: str          # L1 / L2 / L3
    acceptance: dict | None = None  # 三项验收结果（可选，不影响默认排序）


@dataclass
class ContextBundle:
    """四层记忆栈打包结果，交给 AI 直接用"""
    l0_identity: str            # ~50 tokens
    l1_key_moments: list[RetrievalResult]   # ~600 tokens
    l2_topic_context: list[RetrievalResult] # ~300 tokens，按需
    l3_deep_results: list[RetrievalResult]  # 按需触发
    total_tokens_estimate: int
