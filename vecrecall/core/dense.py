"""内存稠密向量索引：Numpy / SQLite 向量后端共用的相似度计算。

- 装了 numpy：向量预先归一化，存成 float32 矩阵，一次矩阵乘法算出全部余弦相似度。
- 没装 numpy（或设置环境变量 VECRECALL_NO_NUMPY=1）：退回纯 Python，
  同样预先归一化，查询只需一次点积，比逐条重算范数快约 2–3 倍，但仍随条数线性增长。

返回结果与旧实现一致：按分数降序；同分时按写入顺序（seq 小的在前）；
维度不一致时按 zip 截断计算（与旧实现相同）。
"""

from __future__ import annotations

import math
import os

try:  # 可选加速
    if os.environ.get("VECRECALL_NO_NUMPY"):
        raise ImportError
    import numpy as _np
except ImportError:  # pragma: no cover - 取决于环境
    _np = None

HAS_NUMPY = _np is not None


def _normalize(vec) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1e-9
    return [x / norm for x in vec]


class DenseIndex:
    """id → (归一化向量, payload, seq) 的内存索引，支持增删改与 top-n 查询。"""

    def __init__(self, use_numpy: bool | None = None):
        self.use_numpy = HAS_NUMPY if use_numpy is None else (use_numpy and HAS_NUMPY)
        self._row: dict[str, int] = {}      # id → 行号
        self._ids: list[str] = []
        self._payloads: list[dict] = []
        self._seq: list[int] = []
        self._dims: list[int] = []
        self._next_seq = 0
        # numpy 存储
        self._mat = None                    # (capacity, dim) float32
        self._dim: int | None = None        # 矩阵维度；出现不同维度时置为 -1（走通用路径）
        # 纯 Python 存储
        self._vecs: list[list[float]] = []
        self._ragged: dict | None = None   # 维度不一致时保存原始单位向量（行号 → list）

    # ── 维护 ──────────────────────────────

    def __len__(self) -> int:
        return len(self._ids)

    def clear(self) -> None:
        self.__init__(self.use_numpy)

    def upsert(self, memory_id: str, vector, payload: dict,
               seq: int | None = None, keep_position: bool = True) -> None:
        """写入或更新。keep_position=False 时已有条目移到末尾（对应 SQLite 的 INSERT OR REPLACE）。"""
        if seq is None:
            seq = self._next_seq
        self._next_seq = max(self._next_seq, seq + 1)
        unit = _normalize(vector)
        r = self._row.get(memory_id)
        if r is not None:
            if keep_position:
                seq = self._seq[r]
            self._set_row(r, memory_id, unit, payload, seq)
            return
        r = len(self._ids)
        self._row[memory_id] = r
        self._ids.append(memory_id)
        self._payloads.append(payload)
        self._seq.append(seq)
        self._dims.append(len(unit))
        if self.use_numpy:
            self._np_put(r, unit)
        else:
            self._vecs.append(unit)

    def _set_row(self, r, memory_id, unit, payload, seq):
        self._payloads[r] = payload
        self._seq[r] = seq
        self._dims[r] = len(unit)
        if self.use_numpy:
            self._np_put(r, unit)
        else:
            self._vecs[r] = unit

    def delete(self, memory_id: str) -> None:
        r = self._row.pop(memory_id, None)
        if r is None:
            return
        last = len(self._ids) - 1
        if r != last:                       # 用最后一行填补空位
            mid = self._ids[last]
            self._ids[r] = mid
            self._payloads[r] = self._payloads[last]
            self._seq[r] = self._seq[last]
            self._dims[r] = self._dims[last]
            self._row[mid] = r
            if self.use_numpy:
                self._mat[r] = self._mat[last]
                self._ragged_move(r, last)
            else:
                self._vecs[r] = self._vecs[last]
        self._ids.pop()
        self._payloads.pop()
        self._seq.pop()
        self._dims.pop()
        if not self.use_numpy:
            self._vecs.pop()
        elif self._ragged is not None:
            self._ragged.pop(last, None)

    def bulk_load(self, ids: list[str], mat, payloads: list[dict]) -> None:
        """一次性载入（仅 numpy、空索引、所有向量同维度）：整体归一化，不逐行处理。

        mat：(n, dim) 的 numpy 数组（未归一化），行顺序即写入顺序。
        """
        if not self.use_numpy or self._ids:
            raise RuntimeError("bulk_load 只能用于空的 numpy 索引")
        m = _np.asarray(mat, dtype=_np.float64)
        norms = _np.sqrt((m * m).sum(axis=1))
        norms[norms == 0] = 1e-9
        self._mat = _np.ascontiguousarray(m / norms[:, None], dtype=_np.float32)
        n, d = self._mat.shape
        self._dim = d
        self._ids = list(ids)
        self._row = {k: i for i, k in enumerate(self._ids)}
        self._payloads = list(payloads)
        self._seq = list(range(n))
        self._dims = [d] * n
        self._next_seq = n

    # ── numpy 存储细节 ─────────────────────

    def _np_put(self, r: int, unit: list[float]) -> None:
        d = len(unit)
        if self._dim is None:
            self._dim = d
            self._mat = _np.zeros((max(16, r + 1), d), dtype=_np.float32)
        if r >= self._mat.shape[0]:
            grow = _np.zeros((max(r + 1, self._mat.shape[0] * 2), self._mat.shape[1]),
                             dtype=_np.float32)
            grow[: self._mat.shape[0]] = self._mat
            self._mat = grow
        if d == self._mat.shape[1]:
            self._mat[r] = unit
            if self._ragged is not None:
                self._ragged.pop(r, None)
        else:                               # 维度不同：矩阵行置零，原向量另存
            self._mat[r] = 0.0
            if self._ragged is None:
                self._ragged = {}
            self._ragged[r] = unit

    def _ragged_move(self, dst: int, src: int) -> None:
        if self._ragged is None:
            return
        if src in self._ragged:
            self._ragged[dst] = self._ragged.pop(src)
        else:
            self._ragged.pop(dst, None)

    # ── 查询 ──────────────────────────────

    def query(self, vector, n: int, min_score: float | None = 0.0) -> list[tuple[str, float, dict]]:
        size = len(self._ids)
        if size == 0 or n <= 0:
            return []
        q = _normalize(vector)
        floor = -math.inf if min_score is None else min_score
        if self.use_numpy:
            return self._query_np(q, n, floor, size)
        return self._query_py(q, n, floor)

    def _query_py(self, q, n, floor):
        scored = []
        for r, v in enumerate(self._vecs):
            s = sum(a * b for a, b in zip(q, v))
            if s >= floor:
                scored.append((-s, self._seq[r], r))
        if len(scored) > n:
            import heapq
            scored = heapq.nsmallest(n, scored)
        else:
            scored.sort()
        return [(self._ids[r], -ns, dict(self._payloads[r])) for ns, _sq, r in scored]

    def _query_np(self, q, n, floor, size):
        mat = self._mat[:size]
        d = mat.shape[1]
        qv = _np.zeros(d, dtype=_np.float32)
        m = min(d, len(q))
        qv[:m] = q[:m]                      # zip 截断语义
        scores = mat @ qv
        if self._ragged:                    # 维度不同的行单独算
            for r, v in self._ragged.items():
                scores[r] = sum(a * b for a, b in zip(q, v))
        _np.clip(scores, -1.0, 1.0, out=scores)
        cand = _np.flatnonzero(scores >= floor)
        if cand.size == 0:
            return []
        cs = scores[cand]
        if cand.size > n:                   # 先取第 n 大的分数，同分的全部保留，再精确排序
            kth = _np.partition(cs, cand.size - n)[cand.size - n]
            keep = cs >= kth
            cand, cs = cand[keep], cs[keep]
        seq = _np.asarray(self._seq, dtype=_np.int64)[cand]
        order = _np.lexsort((seq, -cs))[:n]
        return [(self._ids[int(r)], float(cs[i]), dict(self._payloads[int(r)]))
                for i, r in zip(order, cand[order])]
