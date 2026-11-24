"""LongMemEval 检索评测（会话级 / 轮次级召回，不需要 LLM）。

LongMemEval（ICLR 2025）为每道题提供一段带时间戳的多会话聊天历史，并标注
答案所在的会话（answer_session_ids）与轮次（turn 上的 has_answer）。
本脚本把历史写入 VecRecall，用问题检索，评估答案会话能否排到前面。
只评检索，不评问答正确率；问答评测需要 LLM 判分，见官方仓库。

按官方做法跳过拒答题（question_id 以 _abs 结尾），它们没有答案位置。

数据（需自行下载一次，放在任意目录）：
    https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
      longmemeval_s_cleaned.json   每题约 40 个会话（推荐）
      longmemeval_m_cleaned.json   每题约 500 个会话（很慢）
      longmemeval_oracle.json      只含证据会话（过于简单，仅作上限参考）

用法：
    # 先用内置样例确认脚本可用（3 道题，几秒）
    python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_sample.json

    # 抽 50 道题快速跑（零依赖词法嵌入）
    python benchmarks/longmemeval.py --data data/longmemeval_s_cleaned.json --limit 50

    # 全量 + 消融（vector / hybrid / dedup / graph）+ 输出报告
    python benchmarks/longmemeval.py --data data/longmemeval_s_cleaned.json \
        --configs vector,hybrid,dedup,graph --out benchmarks/results/lme_s_bow.md

    # 语义嵌入（需 pip install -e ".[sentence]"）
    python benchmarks/longmemeval.py --data ... --embedder sentence

配置说明：
    vector  纯向量检索（默认路径，dedup 关闭）
    hybrid  search(hybrid=True)：相似度 + 重要性 + 时间新鲜度重排
    dedup   开启写入时语义去重合并（默认阈值 0.92），观察去重对召回的影响
    graph   启发式抽取实体入图，用 graph_search 的直接命中 + 图谱扩展结果排序
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vecrecall.core.engine import (  # noqa: E402
    VecRecall, NumpyVectorBackend, build_embedding_backend, estimate_tokens,
)
from vecrecall.core.extractor import HeuristicExtractor  # noqa: E402
from _metrics import average, dedupe, percentile, score  # noqa: E402

CONFIGS = ("vector", "hybrid", "dedup", "graph")
KS = (1, 5, 10)


# ── 数据 ─────────────────────────────────────

def load_questions(path: str, limit: int | None, types: set[str] | None,
                   seed: int) -> tuple[list[dict], int]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    skipped_abs = sum(1 for q in data if str(q["question_id"]).endswith("_abs"))
    qs = [q for q in data if not str(q["question_id"]).endswith("_abs")]
    if types:
        qs = [q for q in qs if q["question_type"] in types]
    if limit and limit < len(qs):
        rng = random.Random(seed)
        qs = rng.sample(qs, limit)
    return qs, skipped_abs


_DATE_RE = re.compile(r"\s*\([^)]*\)\s*")


def parse_date(s: str) -> float | None:
    """'2023/05/20 (Sat) 02:21' → epoch 秒；解析失败返回 None。"""
    if not s:
        return None
    t = _DATE_RE.sub(" ", str(s)).strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    return None


def session_text(turns: list[dict], user_only: bool) -> str:
    lines = []
    for t in turns:
        role = t.get("role", "")
        if user_only and role != "user":
            continue
        lines.append(f"{role}: {t.get('content', '')}")
    return "\n".join(lines)


def build_units(q: dict, granularity: str, user_only: bool):
    """把一道题的历史拆成待写入单元。

    返回 (units, gold_sessions, gold_turns)：
      units: [(unit_id, session_id, text, timestamp)]
    """
    units = []
    gold_turns: set[str] = set()
    dates = q.get("haystack_dates") or [None] * len(q["haystack_sessions"])
    for sid, date, turns in zip(q["haystack_session_ids"], dates, q["haystack_sessions"]):
        ts = parse_date(date)
        if granularity == "session":
            text = session_text(turns, user_only)
            if text.strip():
                units.append((sid, sid, text, ts))
        else:
            for i, t in enumerate(turns):
                if user_only and t.get("role") != "user":
                    continue
                uid = f"{sid}#{i}"
                if t.get("has_answer"):
                    gold_turns.add(uid)
                text = f"{t.get('role', '')}: {t.get('content', '')}"
                if text.strip():
                    units.append((uid, sid, text, ts))
    return units, set(q.get("answer_session_ids") or []), gold_turns


# ── 单题评测 ─────────────────────────────────

def eval_question(q: dict, config: str, embedder, granularity: str,
                  user_only: bool, k_max: int) -> dict:
    units, gold_s, gold_t = build_units(q, granularity, user_only)
    with tempfile.TemporaryDirectory() as d:
        p = VecRecall(
            base_dir=d, wing="lme", embedding_backend=embedder,
            vector_backend=NumpyVectorBackend(),
            dedup=(config == "dedup"),
            extractor=HeuristicExtractor() if config == "graph" else None,
        )
        mem_units: dict[str, list[tuple[str, str]]] = defaultdict(list)
        write_ms = []
        for uid, sid, text, ts in units:
            t0 = time.perf_counter()
            m = p.add(text, topic="lme", session_id=sid, importance=0.5)
            write_ms.append((time.perf_counter() - t0) * 1000)
            mem_units[m.id].append((uid, sid))
            # 用会话真实时间，hybrid 的时间新鲜度才有意义（去重合并时保留首条时间）
            if ts is not None and len(mem_units[m.id]) == 1:
                m.timestamp = ts
                p._kg.save(m)

        t0 = time.perf_counter()
        if config == "graph":
            # min_score=None 与 vector 配置一致（不按分数截断）；graph_search 默认 0.1
            # 会先砍掉低分种子，对比就不公平了
            res = p.graph_search(q["question"], n=k_max, hops=2, min_score=None)
            mems = [r.memory for r in res["direct"]] + [h["memory"] for h in res["graph"]]
            contents = [m.content for m in mems]
            ranked_mem = [m.id for m in mems]
        else:
            # hybrid 的时间新鲜度以「提问时刻」为基准：LongMemEval 的会话都在过去，
            # 若按当前时间计算，所有记忆的新鲜度都接近 0，hybrid 就退化成纯向量
            res = p.search(q["question"], n=k_max, hybrid=(config == "hybrid"),
                           as_of=parse_date(q.get("question_date")))
            contents = [r.memory.content for r in res]
            ranked_mem = [r.memory.id for r in res]
        query_ms = (time.perf_counter() - t0) * 1000
        p.close()

    ranked_s = dedupe(sid for mid in ranked_mem for _uid, sid in mem_units.get(mid, []))
    out = {"session": score(ranked_s, gold_s, KS)}
    if granularity == "turn":
        ranked_t = dedupe(uid for mid in ranked_mem for uid, _sid in mem_units.get(mid, []))
        out["turn"] = score(ranked_t, gold_t, KS) if gold_t else None
    out.update({
        "question_id": q["question_id"],
        "question_type": q["question_type"],
        "n_units": len(units),
        "n_memories": len(mem_units),
        "write_ms": write_ms,
        "query_ms": query_ms,
        "ctx_tokens@5": sum(estimate_tokens(c) for c in contents[:5]),
    })
    return out


# ── 汇总与报告 ───────────────────────────────

def summarize(rows: list[dict]) -> dict:
    write = [w for r in rows for w in r["write_ms"]]
    query = [r["query_ms"] for r in rows]
    summ = {
        "n": len(rows),
        "session": average([r["session"] for r in rows]),
        "turn": average([r["turn"] for r in rows if r.get("turn")]),
        "write_ms_p50": percentile(write, 50),
        "write_ms_p95": percentile(write, 95),
        "query_ms_p50": percentile(query, 50),
        "query_ms_p95": percentile(query, 95),
        "ctx_tokens@5": sum(r["ctx_tokens@5"] for r in rows) / max(1, len(rows)),
        "memories_per_q": sum(r["n_memories"] for r in rows) / max(1, len(rows)),
        "units_per_q": sum(r["n_units"] for r in rows) / max(1, len(rows)),
    }
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["question_type"]].append(r["session"])
    summ["by_type"] = {t: (len(v), average(v)) for t, v in sorted(by_type.items())}
    return summ


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def render(results: dict, meta: dict) -> str:
    L = []
    L.append("# LongMemEval 检索评测")
    L.append("")
    L.append(f"- 数据：`{meta['data']}`")
    L.append(f"- 题数：{meta['n']}（已按官方做法跳过 {meta['skipped_abs']} 道拒答题）"
             + (f"，随机抽样 seed={meta['seed']}" if meta["limit"] else ""))
    L.append(f"- 嵌入：`{meta['embedder']}`　粒度：`{meta['granularity']}`"
             f"　内容：{'仅用户轮次' if meta['user_only'] else '用户 + 助手'}")
    L.append(f"- 生成时间：{meta['time']}")
    L.append("")
    L.append("## 会话级召回")
    L.append("")
    L.append("| 配置 | R_any@1 | R_any@5 | R_any@10 | R_all@5 | R_all@10 | nDCG@10 | MRR |")
    L.append("|---|---|---|---|---|---|---|---|")
    for c, s in results.items():
        m = s["session"]
        L.append(f"| {c} | {pct(m['recall_any@1'])} | {pct(m['recall_any@5'])} | "
                 f"{pct(m['recall_any@10'])} | {pct(m['recall_all@5'])} | "
                 f"{pct(m['recall_all@10'])} | {m['ndcg@10']:.3f} | {m['mrr']:.3f} |")
    if any(s["turn"] for s in results.values()):
        L.append("")
        L.append("## 轮次级召回")
        L.append("")
        L.append("| 配置 | R_any@5 | R_any@10 | R_all@10 | nDCG@10 | MRR |")
        L.append("|---|---|---|---|---|---|")
        for c, s in results.items():
            m = s["turn"]
            if m:
                L.append(f"| {c} | {pct(m['recall_any@5'])} | {pct(m['recall_any@10'])} | "
                         f"{pct(m['recall_all@10'])} | {m['ndcg@10']:.3f} | {m['mrr']:.3f} |")
    L.append("")
    L.append("## 开销")
    L.append("")
    L.append("| 配置 | 写入 p50 (ms) | 写入 p95 (ms) | 检索 p50 (ms) | 检索 p95 (ms) | "
             "top-5 上下文 token | 每题记忆数 / 写入单元 |")
    L.append("|---|---|---|---|---|---|---|")
    for c, s in results.items():
        L.append(f"| {c} | {s['write_ms_p50']:.1f} | {s['write_ms_p95']:.1f} | "
                 f"{s['query_ms_p50']:.1f} | {s['query_ms_p95']:.1f} | "
                 f"{s['ctx_tokens@5']:.0f} | {s['memories_per_q']:.1f} / {s['units_per_q']:.1f} |")
    L.append("")
    L.append("## 按题型（会话级 R_any@5）")
    L.append("")
    types = sorted({t for s in results.values() for t in s["by_type"]})
    L.append("| 题型 | 题数 | " + " | ".join(results.keys()) + " |")
    L.append("|---|---|" + "---|" * len(results))
    for t in types:
        n = next(s["by_type"][t][0] for s in results.values() if t in s["by_type"])
        cells = [pct(s["by_type"][t][1]["recall_any@5"]) if t in s["by_type"] else "-"
                 for s in results.values()]
        L.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="LongMemEval 检索评测")
    ap.add_argument("--data", required=True, help="LongMemEval JSON 文件路径")
    ap.add_argument("--configs", default="vector",
                    help=f"逗号分隔，可选 {','.join(CONFIGS)}（默认 vector）")
    ap.add_argument("--embedder", default="bow",
                    help="bow（默认，零依赖）/ sentence / ollama / api / auto")
    ap.add_argument("--embed-model", default=None, help="嵌入模型名")
    ap.add_argument("--granularity", choices=("session", "turn"), default="session",
                    help="写入粒度：每个会话一条记忆，或每轮对话一条记忆")
    ap.add_argument("--user-only", action="store_true", help="只写入用户轮次")
    ap.add_argument("--limit", type=int, default=None, help="随机抽样题数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--types", default=None, help="只评测这些题型（逗号分隔）")
    ap.add_argument("--k", type=int, default=10, help="检索条数上限（默认 10）")
    ap.add_argument("--out", default=None, help="Markdown 报告输出路径")
    ap.add_argument("--dump", default=None, help="逐题结果 JSONL 输出路径")
    args = ap.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    bad = [c for c in configs if c not in CONFIGS]
    if bad:
        print(f"未知配置: {bad}，可选 {CONFIGS}")
        return 2
    types = {t.strip() for t in args.types.split(",")} if args.types else None
    qs, skipped_abs = load_questions(args.data, args.limit, types, args.seed)
    if not qs:
        print("没有可评测的题目")
        return 2
    embedder = build_embedding_backend(args.embedder, model=args.embed_model)
    k_max = max(args.k, max(KS))

    print("=" * 64)
    print(f"LongMemEval 检索评测  题数={len(qs)}  嵌入={args.embedder}  "
          f"粒度={args.granularity}  配置={','.join(configs)}")
    print("=" * 64)

    results: dict[str, dict] = {}
    dump = open(args.dump, "w", encoding="utf-8") if args.dump else None
    for c in configs:
        rows = []
        t0 = time.perf_counter()
        for i, q in enumerate(qs, 1):
            rows.append(eval_question(q, c, embedder, args.granularity, args.user_only, k_max))
            if i % 25 == 0 or i == len(qs):
                print(f"  [{c}] {i}/{len(qs)}  用时 {time.perf_counter() - t0:.0f}s", flush=True)
        if dump:
            for r in rows:
                rec = {k: v for k, v in r.items() if k != "write_ms"}
                rec["config"] = c
                dump.write(json.dumps(rec, ensure_ascii=False) + "\n")
        s = summarize(rows)
        results[c] = s
        m = s["session"]
        print(f"  {c:<7} R_any@5={pct(m['recall_any@5'])}  R_any@10={pct(m['recall_any@10'])}  "
              f"R_all@10={pct(m['recall_all@10'])}  nDCG@10={m['ndcg@10']:.3f}  MRR={m['mrr']:.3f}")
    if dump:
        dump.close()

    meta = {
        "data": args.data, "n": len(qs), "skipped_abs": skipped_abs,
        "limit": args.limit, "seed": args.seed,
        "embedder": args.embedder + (f":{args.embed_model}" if args.embed_model else ""),
        "granularity": args.granularity, "user_only": args.user_only,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    report = render(results, meta)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n报告已写入 {args.out}")
    else:
        print()
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
