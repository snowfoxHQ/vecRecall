"""检索评测的公共指标（内置评测集与 LongMemEval 共用）。

全部是排名指标，输入为「排好序的候选 id 列表」与「正确答案 id 集合」：
  recall_any@k  前 k 个里至少命中一个正确答案（LongMemEval 官方主指标）
  recall_all@k  前 k 个里命中全部正确答案（多证据题更严格）
  ndcg@k        二值相关性的 nDCG，命中越靠前分越高
  mrr           第一个正确答案排名的倒数，没命中为 0
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def dedupe(ids: Iterable[str]) -> list[str]:
    """去重并保持顺序（多条记忆映射到同一会话时只算一次）。"""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def recall_any(ranked: Sequence[str], gold: set[str], k: int) -> float:
    return 1.0 if any(r in gold for r in ranked[:k]) else 0.0


def recall_all(ranked: Sequence[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    return 1.0 if gold <= set(ranked[:k]) else 0.0


def ndcg(ranked: Sequence[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, r in enumerate(ranked[:k]) if r in gold)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return dcg / ideal if ideal else 0.0


def mrr(ranked: Sequence[str], gold: set[str]) -> float:
    for i, r in enumerate(ranked):
        if r in gold:
            return 1.0 / (i + 1)
    return 0.0


def score(ranked: Sequence[str], gold: set[str], ks: Sequence[int] = (1, 5, 10)) -> dict:
    """一次检索的全部指标。"""
    out = {"mrr": mrr(ranked, gold)}
    for k in ks:
        out[f"recall_any@{k}"] = recall_any(ranked, gold, k)
        out[f"recall_all@{k}"] = recall_all(ranked, gold, k)
        out[f"ndcg@{k}"] = ndcg(ranked, gold, k)
    return out


def average(rows: list[dict]) -> dict:
    """逐键取平均；空列表返回空 dict。"""
    if not rows:
        return {}
    keys = rows[0].keys()
    return {k: sum(r[k] for r in rows) / len(rows) for k in keys}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    idx = min(len(v) - 1, max(0, int(round(p / 100.0 * (len(v) - 1)))))
    return v[idx]
