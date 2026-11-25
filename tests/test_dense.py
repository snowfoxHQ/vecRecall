"""v2.4 向量化检索：DenseIndex 与 Numpy / SQLite 后端的正确性。

对照实现是 v2.3 的逐条余弦计算；numpy 与纯 Python 两条路径都要和它一致。
"""

from __future__ import annotations

import math
import os
import random
import sqlite3
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.dense import HAS_NUMPY, DenseIndex  # noqa: E402
from vecrecall.core.vectors import NumpyVectorBackend, SqliteVectorBackend  # noqa: E402

PASS = FAIL = 0


def test(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def reference(store, q, n, min_score):
    floor = -math.inf if min_score is None else min_score
    qn = math.sqrt(sum(x * x for x in q)) or 1e-9
    res = []
    for mid, (v, p) in store.items():
        vn = math.sqrt(sum(x * x for x in v)) or 1e-9
        s = sum(a * b for a, b in zip(q, v)) / (qn * vn)
        if s >= floor:
            res.append((mid, s, p))
    res.sort(key=lambda x: x[1], reverse=True)
    return res[:n]


def close(a, b, tol=1e-5):
    return len(a) == len(b) and all(abs(x[1] - y[1]) < tol for x, y in zip(a, b))


def off_boundary(res, min_score, eps=1e-6):
    """去掉分数恰好落在阈值上的条目：预先归一化与逐条计算的舍入不同，
    分数正好等于 min_score（如 0.5）时两边可能一个保留一个过滤，这不是错误。"""
    if min_score is None:
        return res
    return [x for x in res if abs(x[1] - min_score) > eps]


def same_order(a, b, insertion, n, eps=1e-9):
    """名次一致（含同分按写入顺序）。

    预先归一化再点积与「点积 / 范数」的舍入不同，数学上相等的两个分数可能差 1e-16，
    排序时就会互换；不同 Python 版本的 sum() 精度也不同（3.12 起用补偿求和）。
    所以分数差在 eps 内的视为同分，按写入顺序比较；结果被 n 截断时，
    与第 n 名同分的条目谁进谁出都算对。
    """
    pos = {k: i for i, k in enumerate(insertion)}

    def canon(res):
        out, i = [], 0
        while i < len(res):
            j = i
            while j + 1 < len(res) and abs(res[j + 1][1] - res[i][1]) < eps:
                j += 1
            out.append(sorted(x[0] for x in res[i:j + 1]))
            i = j + 1
        return out

    ca, cb = canon(a), canon(b)
    if len(a) == n and len(b) == n and ca and cb:   # 最后一组可能被截断
        ca, cb = ca[:-1], cb[:-1]
    return len(a) == len(b) and ca == cb and all(
        [x[0] for x in a if x[0] in g] == sorted((y for y in g), key=pos.get)
        for g in map(set, ca))


def random_ops(idx_upsert, idx_delete, rng, n, dim):
    store = {}
    for i in range(n):
        v = [rng.choice([0, 0, 1, 2, -1]) for _ in range(dim)]
        idx_upsert(f"m{i}", v, {"id": f"m{i}"})
        store[f"m{i}"] = (v, {"id": f"m{i}"})
    for i in rng.sample(range(n), n // 5):
        idx_delete(f"m{i}")
        store.pop(f"m{i}")
    for i in rng.sample(range(n), n // 10):
        if f"m{i}" in store:
            v = [rng.random() for _ in range(dim)]
            idx_upsert(f"m{i}", v, {"id": f"m{i}"})
            store[f"m{i}"] = (v, {"id": f"m{i}"})
    return store


def check_index(use_numpy):
    label = "numpy" if use_numpy else "纯 Python"
    rng = random.Random(42)
    ok_scores = ok_ids = True
    for _ in range(25):
        idx = DenseIndex(use_numpy)
        store = random_ops(lambda i, v, p: idx.upsert(i, v, p), idx.delete,
                           rng, rng.randint(0, 250), rng.choice([8, 32]))
        dim = len(next(iter(store.values()))[0]) if store else 8
        for _ in range(4):
            q = [rng.choice([0, 1, -1, 2]) for _ in range(dim)]
            n = rng.choice([1, 5, 20])
            ms = rng.choice([None, 0.0, 0.5, -1.0])
            a, b = idx.query(q, n, ms), reference(store, q, n, ms)
            if ms is not None and (len(a) < n or len(b) < n):
                a, b = off_boundary(a, ms), off_boundary(b, ms)
            ok_scores &= close(a, b)
            if not use_numpy:   # 纯 Python 是 float64，名次（含同分顺序）应一致
                ok_ids &= same_order(a, b, list(store), n)
    test(f"[{label}] 随机增删改后分数与旧实现一致", ok_scores)
    if not use_numpy:
        test(f"[{label}] 名次与同分顺序与旧实现完全一致", ok_ids)

    idx = DenseIndex(use_numpy)
    test(f"[{label}] 空索引返回空", idx.query([1.0, 0.0], 5) == [])
    for i in range(5):
        idx.upsert(f"t{i}", [1.0, 0.0], {"id": f"t{i}"})
    test(f"[{label}] 同分按写入顺序", [x[0] for x in idx.query([1.0, 0.0], 3)] == ["t0", "t1", "t2"])
    idx.upsert("t0", [1.0, 0.0], {"id": "t0", "v": 2})
    test(f"[{label}] 更新保留原位置", idx.query([1.0, 0.0], 1)[0][0] == "t0")
    test(f"[{label}] 更新 payload 生效", idx.query([1.0, 0.0], 1)[0][2].get("v") == 2)
    idx.upsert("neg", [-1.0, 0.0], {})
    test(f"[{label}] min_score=None 保留负分", any(x[0] == "neg" for x in idx.query([1.0, 0.0], 10, None)))
    test(f"[{label}] min_score=0 过滤负分", all(x[0] != "neg" for x in idx.query([1.0, 0.0], 10, 0.0)))
    idx.delete("t2")
    idx.delete("missing")
    test(f"[{label}] 删除后不再返回", all(x[0] != "t2" for x in idx.query([1.0, 0.0], 10, None)))
    test(f"[{label}] 长度正确", len(idx) == 5)
    idx.upsert("z", [0.0, 0.0], {})
    test(f"[{label}] 零向量不报错", isinstance(idx.query([0.0, 0.0], 3, None), list))
    idx.upsert("d3", [1.0, 0.0, 5.0], {})
    r = dict((x[0], x[1]) for x in idx.query([1.0, 0.0], 20, None))
    test(f"[{label}] 维度不同按截断计算", "d3" in r and abs(r["d3"] - 1 / math.sqrt(26)) < 1e-5)
    idx.delete("t0")
    test(f"[{label}] 维度不同的行删除/移动后仍正确",
         abs(dict((x[0], x[1]) for x in idx.query([1.0, 0.0], 20, None))["d3"] - 1 / math.sqrt(26)) < 1e-5)
    test(f"[{label}] 分数不超过 1", all(x[1] <= 1.0 for x in idx.query([1.0, 0.0], 20, None)))
    payload = idx.query([1.0, 0.0], 1)[0][2]
    payload["hack"] = 1
    test(f"[{label}] 返回的 payload 是副本", "hack" not in idx.query([1.0, 0.0], 1)[0][2])


print("\n[DenseIndex]")
check_index(False)
if HAS_NUMPY:
    check_index(True)
else:
    print("  - 未安装 numpy，跳过 numpy 路径")

print("\n[后端]")
rng = random.Random(7)
nb = NumpyVectorBackend()
store = random_ops(nb.upsert, nb.delete, rng, 200, 16)
q = [1, 0, 2] + [0] * 13
test("NumpyVectorBackend 与旧实现一致", close(nb.query(q, 10, None), reference(store, q, 10, None)))
test("NumpyVectorBackend.count", nb.count() == len(store))
nb.clear()
test("NumpyVectorBackend.clear", nb.count() == 0 and nb.query(q, 5) == [])

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "vectors.db")
    sb = SqliteVectorBackend(path)
    store = random_ops(sb.upsert, sb.delete, rng, 200, 16)
    test("SqliteVectorBackend 首次查询（载入）一致", close(sb.query(q, 10, None), reference(store, q, 10, None)))
    for i in range(200, 230):
        v = [rng.random() for _ in range(16)]
        sb.upsert(f"m{i}", v, {"id": f"m{i}"})
        store[f"m{i}"] = (v, {"id": f"m{i}"})
    sb.delete("m201")
    store.pop("m201")
    test("SqliteVectorBackend 增量维护一致", close(sb.query(q, 15, None), reference(store, q, 15, None)))
    fresh = SqliteVectorBackend(path)
    test("SqliteVectorBackend 增量与重新读库名次一致",
         [x[0] for x in sb.query(q, 15, None)] == [x[0] for x in fresh.query(q, 15, None)])
    fresh.close()          # Windows 下未关闭的连接会锁住文件，临时目录删不掉

    other = SqliteVectorBackend(path)
    other.upsert("from_other", q, {"id": "from_other"})
    other.delete("m5")
    store.pop("m5", None)
    test("另一连接写入后能被发现", sb.query(q, 1)[0][0] == "from_other")
    test("另一连接删除后不再返回", all(x[0] != "m5" for x in sb.query(q, 300, None)))
    other.close()

    blob = sb._conn.execute("SELECT dim, vec FROM vectors WHERE id='m210'").fetchone()
    test("新写入为 float32", len(blob[1]) == blob[0] * 4)
    sb.close()

    old = os.path.join(d, "old.db")
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE vectors (id TEXT PRIMARY KEY, dim INTEGER NOT NULL, "
              "vec BLOB NOT NULL, payload TEXT DEFAULT '{}')")
    c.execute("INSERT INTO vectors VALUES (?,?,?,?)", ("a", 3, struct.pack("<3d", 1, 0, 0), '{"id":"a"}'))
    c.execute("INSERT INTO vectors VALUES (?,?,?,?)", ("b", 3, struct.pack("<3d", 0, 1, 0), '{"id":"b"}'))
    c.commit()
    c.close()
    ob = SqliteVectorBackend(old)
    r = ob.query([1, 0, 0], 2, None)
    test("旧库 float64 向量照常读取", r[0][0] == "a" and abs(r[0][1] - 1.0) < 1e-6 and abs(r[1][1]) < 1e-6)
    ob.upsert("c", [1, 1, 0], {"id": "c"})
    test("新旧格式混存可查询", [x[0] for x in ob.query([1, 0, 0], 3, None)][:2] == ["a", "c"])
    ob.clear()
    test("SqliteVectorBackend.clear", ob.count() == 0 and ob.query([1, 0, 0], 3) == [])
    ob.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
