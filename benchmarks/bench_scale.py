"""检索性能评估：记忆数从 1 千增长到 10 万时，各检索模式的延迟与资源占用。

只测性能，不测召回质量（召回见 benchmarks/longmemeval.py）。

做法：
  - 用 LongMemEval-S 的真实对话轮次做语料（按轮存储，与产品默认一致），
    没有数据文件时用合成文本（中英混合，长度分布接近真实对话）。
  - 一个库逐步写到各个检查点（默认 1k / 10k / 50k / 100k），每个检查点：
      · 检索：vector / bm25 / rrf 各跑 Q 次，报 p50 / p95；
        bm25 / rrf 的第一次检索包含建 BM25 索引，单独报「首次」耗时
      · 带去重的写入：默认 add() 写入前要扫一遍向量找重复，单独测几次
      · 磁盘占用、进程内存（RSS，可得时）
  - 填充阶段关闭去重（dedup=False），否则写满 10 万条要 O(n²) 次扫描。

用法：
    python benchmarks/bench_scale.py                                  # 默认检查点，零依赖词法嵌入
    python benchmarks/bench_scale.py --sizes 1000,10000 --queries 20  # 快速试跑
    python benchmarks/bench_scale.py --data benchmarks/data/longmemeval_s_cleaned.json \\
        --out benchmarks/results/scale_bow.md
    python benchmarks/bench_scale.py --backend numpy                  # 内存向量后端对照

注意：默认 SQLite 向量后端是纯 Python 全表扫描，单次 vector 检索在 10 万条时
可能要数秒；这正是本评估要量化的，用于决定是否需要 ANN 索引。
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vecrecall.core.engine import (  # noqa: E402
    NumpyVectorBackend, SqliteVectorBackend, VecRecall, build_embedding_backend,
)

MODES = ("vector", "bm25", "rrf")


# ── 语料 ─────────────────────────────────────

def load_corpus(path: str | None, need: int, seed: int) -> tuple[list[str], list[str], str]:
    """返回 (待写入文本, 查询文本, 来源说明)。"""
    rng = random.Random(seed)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        texts, queries, seen = [], [], set()
        for q in data:
            if not str(q["question_id"]).endswith("_abs"):
                queries.append(q["question"])
            for sess in q.get("haystack_sessions") or []:
                for t in sess:
                    c = (t.get("content") or "").strip()
                    if c and c not in seen:
                        seen.add(c)
                        texts.append(f"{t.get('role', '')}: {c}")
        rng.shuffle(texts)
        rng.shuffle(queries)
        src = f"LongMemEval-S 对话轮次（去重后 {len(texts):,} 条可用）"
        if len(texts) >= need:
            return texts[:need], queries, src
        # 不够就循环复用并加编号，避免完全重复
        out = list(texts)
        i = 0
        while len(out) < need:
            out.append(f"{texts[i % len(texts)]} #{i // len(texts) + 1}")
            i += 1
        return out, queries, src + "，不足部分循环复用"
    return _synthetic(need, rng), _synthetic_queries(rng), "合成文本（未提供 --data）"


_ZH = ("数据库 连接池 超时 部署 回滚 用户 偏好 周末 爬山 会议 预算 接口 认证 缓存 "
       "日志 告警 订单 支付 搜索 推荐 测试 发布 版本 配置 网络 延迟 队列 线程 内存 磁盘").split()
_EN = ("database connection pool timeout deploy rollback user preference weekend hiking "
       "meeting budget api auth cache log alert order payment search release config "
       "network latency queue thread memory disk recipe travel movie book music").split()


def _synthetic(n: int, rng: random.Random) -> list[str]:
    out = []
    for i in range(n):
        words = rng.choices(_ZH if rng.random() < 0.5 else _EN, k=rng.randint(8, 60))
        out.append(("user: " if i % 2 == 0 else "assistant: ") + " ".join(words) + f" #{i}")
    return out


def _synthetic_queries(rng: random.Random) -> list[str]:
    return [" ".join(rng.choices(_ZH if rng.random() < 0.5 else _EN, k=rng.randint(3, 8)))
            for _ in range(500)]


# ── 计量 ─────────────────────────────────────

def rss_mb() -> float | None:
    try:
        import resource  # Unix
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / 1024 if sys.platform != "darwin" else r / 1024 / 1024
    except Exception:  # noqa: BLE001
        pass
    try:  # Windows：64 位下必须声明句柄类型，否则 GetCurrentProcess 的返回值会被截断
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC),
                                                wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / 1024 / 1024
    except Exception:  # noqa: BLE001
        pass
    return None


def dir_mb(path: str) -> float:
    total = 0
    for root, _ds, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1024 / 1024


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    v = sorted(vals)
    return v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))]


def ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def measure_new_process(p, d, emb, query) -> dict:
    """模拟 CLI：新开一个实例做第一次 rrf 检索（要载入向量、构建 BM25）。

    cold：词频缓存需要补齐（本检查点新增的记忆要重新分词并写入缓存）；
    warm：缓存已完整，再开一个新实例。
    """
    def once():
        q = VecRecall(base_dir=d, wing="bench", embedding_backend=emb,
                      vector_backend=SqliteVectorBackend(os.path.join(d, "vectors.db")),
                      dedup=False)
        t = time.perf_counter()
        q.search(query, n=10, mode="rrf")
        dt = ms(t)
        st = dict(q.last_lexical_build)
        q.close()
        return dt, st
    gc.collect()
    cold, _ = once()
    warm, st = once()
    return {"np_cold_ms": cold, "np_warm_ms": warm, "np_warm_bm25_ms": st.get("ms", float("nan")),
            "np_warm_hit": st.get("hit"), "np_warm_miss": st.get("miss")}


# ── 主流程 ───────────────────────────────────

def run(args) -> dict:
    sizes = sorted(int(x) for x in args.sizes.split(",") if x.strip())
    corpus, queries, src = load_corpus(args.data, sizes[-1] + args.dedup_writes * len(sizes),
                                       args.seed)
    emb = build_embedding_backend(args.embedder, model=args.embed_model)
    rng = random.Random(args.seed + 1)
    results = {"meta": {
        "source": src, "embedder": args.embedder + (f":{args.embed_model}" if args.embed_model else ""),
        "backend": args.backend, "queries_per_mode": args.queries, "sizes": sizes,
        "python": sys.version.split()[0], "platform": sys.platform,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }, "points": []}

    with tempfile.TemporaryDirectory() as d:
        vec = (SqliteVectorBackend(os.path.join(d, "vectors.db"))
               if args.backend == "sqlite" else NumpyVectorBackend())
        p = VecRecall(base_dir=d, wing="bench", embedding_backend=emb,
                      vector_backend=vec, dedup=False)
        written = 0
        fill_ms_total = 0.0
        pool = iter(corpus)
        for size in sizes:
            t0 = time.perf_counter()
            batch_t = []
            while written < size:
                tb = time.perf_counter()
                p.add(next(pool), topic="bench", importance=0.5)
                batch_t.append(ms(tb))
                written += 1
            fill_ms = ms(t0)
            fill_ms_total += fill_ms
            print(f"[{size:>7,}] 填充完成，本段 {fill_ms / 1000:.1f}s", flush=True)

            point = {"size": size,
                     "write_ms_p50": pct(batch_t, 50) if batch_t else None,
                     "write_ms_p95": pct(batch_t, 95) if batch_t else None,
                     "disk_mb": dir_mb(d)}
            qs = [queries[rng.randrange(len(queries))] for _ in range(args.queries)]

            p.lexical_cache = False        # 「首次」列按无缓存计，与 v2.3 可比
            for mode in MODES:
                p._bm25 = None             # 每个检查点重新计首次建索引的成本
                gc.collect()
                first = None
                lat = []
                for i, q in enumerate(qs):
                    t1 = time.perf_counter()
                    p.search(q, n=10, mode=mode)
                    dt = ms(t1)
                    if i == 0 and mode != "vector":
                        first = dt         # 含构建 BM25 索引
                    else:
                        lat.append(dt)
                point[mode] = {"p50": pct(lat, 50), "p95": pct(lat, 95),
                               "mean": statistics.fmean(lat) if lat else None,
                               "first": first}
                print(f"          {mode:<6} p50={point[mode]['p50']:8.1f}ms  "
                      f"p95={point[mode]['p95']:8.1f}ms"
                      + (f"  首次(含建索引)={first:8.1f}ms" if first is not None else ""),
                      flush=True)

            p.lexical_cache = True
            if args.backend == "sqlite":
                point.update(measure_new_process(p, d, emb, qs[0]))
                print(f"          新进程首次 rrf：建缓存时 {point['np_cold_ms']:.0f}ms，"
                      f"有缓存 {point['np_warm_ms']:.0f}ms（其中 BM25 {point['np_warm_bm25_ms']:.0f}ms）",
                      flush=True)

            # 带去重的写入（默认 add() 的路径）
            dd = []
            for _ in range(args.dedup_writes):
                t2 = time.perf_counter()
                p.add(next(pool), topic="bench", importance=0.5, dedup=True)
                dd.append(ms(t2))
                written += 1
            point["dedup_write_ms_p50"] = pct(dd, 50)
            point["rss_mb"] = rss_mb()
            print(f"          去重写入 p50={point['dedup_write_ms_p50']:.1f}ms  "
                  f"磁盘={point['disk_mb']:.0f}MB"
                  + (f"  内存={point['rss_mb']:.0f}MB" if point["rss_mb"] else ""), flush=True)
            results["points"].append(point)
            # 去重写入多出的条数计入下一段的起点
        p.close()
    results["meta"]["fill_seconds"] = fill_ms_total / 1000
    return results


def render(res: dict) -> str:
    m = res["meta"]
    L = ["# 检索性能评估", "",
         f"- 语料：{m['source']}",
         f"- 嵌入：`{m['embedder']}`  向量后端：`{m['backend']}`  每种模式 {m['queries_per_mode']} 次查询（top-10）",
         f"- 环境：Python {m['python']} / {m['platform']}  生成时间：{m['time']}",
         f"- 填充总耗时：{m['fill_seconds']:.0f}s（关闭去重）", "",
         "## 检索延迟（ms）", "",
         "| 记忆数 | vector p50 | vector p95 | bm25 p50 | bm25 p95 | rrf p50 | rrf p95 |",
         "|---|---|---|---|---|---|---|"]
    for pt in res["points"]:
        L.append(f"| {pt['size']:,} | {pt['vector']['p50']:.1f} | {pt['vector']['p95']:.1f} | "
                 f"{pt['bm25']['p50']:.1f} | {pt['bm25']['p95']:.1f} | "
                 f"{pt['rrf']['p50']:.1f} | {pt['rrf']['p95']:.1f} |")
    L += ["", "## 首次检索（含构建 BM25 索引，ms）", "",
          "| 记忆数 | bm25 首次 | rrf 首次 |", "|---|---|---|"]
    for pt in res["points"]:
        L.append(f"| {pt['size']:,} | {pt['bm25']['first']:.0f} | {pt['rrf']['first']:.0f} |")
    if any("np_warm_ms" in pt for pt in res["points"]):
        L += ["", "## 新进程首次 rrf 检索（模拟每次运行 vr 命令，ms）", "",
              "| 记忆数 | 无缓存（v2.3 做法） | 建缓存那次 | 有缓存 | 其中 BM25 构建 |", "|---|---|---|---|---|"]
        for pt in res["points"]:
            if "np_warm_ms" in pt:
                L.append(f"| {pt['size']:,} | {pt['rrf']['first']:.0f} | {pt['np_cold_ms']:.0f} | "
                         f"{pt['np_warm_ms']:.0f} | {pt['np_warm_bm25_ms']:.0f} |")
        L += ["", "「无缓存」取自上表的 rrf 首次（同进程，向量已在内存），新进程还要多载入一次向量，"
              "所以实际 v2.3 的新进程耗时比这一列更长。"]
    L += ["", "## 写入与资源", "",
          "| 记忆数 | 写入 p50（无去重） | 写入 p50（默认去重） | 磁盘 MB | 进程内存 MB |",
          "|---|---|---|---|---|"]
    for pt in res["points"]:
        w = f"{pt['write_ms_p50']:.1f}" if pt["write_ms_p50"] is not None else "-"
        r = f"{pt['rss_mb']:.0f}" if pt.get("rss_mb") else "-"
        L.append(f"| {pt['size']:,} | {w} | {pt['dedup_write_ms_p50']:.1f} | "
                 f"{pt['disk_mb']:.0f} | {r} |")
    L += ["", "说明：",
          "- 默认 `add()` 开启语义去重，写入前要做一次全量向量检索，所以「默认去重」写入延迟随记忆数增长；"
          "`archive_session()` 逐轮模式默认关闭去重，对应「无去重」一列。",
          "- bm25 / rrf 的词法索引只在进程内第一次检索时构建，之后增量维护；常驻进程（MCP / REST 服务）"
          "只付一次。CLI 每次调用都是新进程，每次都要构建。", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="VecRecall 检索性能评估")
    ap.add_argument("--sizes", default="1000,10000,50000,100000")
    ap.add_argument("--queries", type=int, default=30, help="每种模式每个检查点的查询次数")
    ap.add_argument("--dedup-writes", type=int, default=5, help="每个检查点测几次带去重的写入")
    ap.add_argument("--data", default=None, help="LongMemEval JSON；不给则用合成文本")
    ap.add_argument("--embedder", default="bow")
    ap.add_argument("--embed-model", default=None)
    ap.add_argument("--backend", choices=("sqlite", "numpy"), default="sqlite")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None, help="原始结果 JSON 输出路径")
    args = ap.parse_args()
    res = run(args)
    rep = render(res)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(rep)
        print(f"\n报告已写入 {args.out}")
    else:
        print("\n" + rep)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
