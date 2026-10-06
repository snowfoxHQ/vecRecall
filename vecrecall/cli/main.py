"""
VecRecall — CLI

用法:
<<<<<<< HEAD
  vr init [--dir DIR] [--wing WING]
  vr add "内容" [--topic TOPIC] [--wing WING] [--importance 0.8]
  vr search "查询" [--n 10] [--layer l3]
  vr context "当前对话" [--l3]
=======
  vr init [--dir DIR] [--wing WING] [--chain] [--encrypt]
  vr add "内容" [--topic TOPIC] [--wing WING] [--importance 0.8]
  vr search "查询" [--n 10] [--layer l3] [--hybrid]
  vr context "当前对话" [--l3] [--hybrid]
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
  vr stats
  vr wings
  vr topics [--wing WING]
  vr diary write AGENT "内容"
  vr diary read AGENT [QUERY]
  vr archive SESSION_FILE
  vr export WING [--out FILE]
  vr import FILE
<<<<<<< HEAD
  vr mcp [--dir DIR] [--wing WING]
=======
  vr delete ID
  vr prune [--min-importance 0.3] [--older-than 90] [--wing W] [--yes]
  vr verify [--wing W]
  vr reindex
  vr list [--wing W] [--topic T] [--limit N] [--full]
  vr graph [--limit N]
  vr entity NAME
  vr decay [--threshold 0.2]
  vr distill [--topic T] [--yes]
  vr graph-search "查询" [--n N] [--hops 2]
  vr mcp [--dir DIR] [--wing WING] [--chain] [--embedder ollama|api]
  vr api [--dir DIR] [--wing WING] [--port 8791] [--embedder ollama|api]
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 自动适应中英文编码：强制 stdin/stdout/stderr 使用 UTF-8
# 解决 Windows PowerShell 默认 GBK 编码导致中文乱码的问题
if sys.stdout.encoding and sys.stdout.encoding.upper() not in ('UTF-8', 'UTF8'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.upper() not in ('UTF-8', 'UTF8'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
if sys.stdin.encoding and sys.stdin.encoding.upper() not in ('UTF-8', 'UTF8'):
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')

# 修正命令行参数编码：Windows 下参数可能以 GBK 传入
def _fix_encoding(s: str) -> str:
    """尝试修正错误编码的字符串，自动识别中英文"""
    if not isinstance(s, str):
        return s
    try:
        # 尝试以 latin-1 解码后再用 GBK/UTF-8 重新编码，修正乱码
        return s.encode('latin-1').decode('gbk')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s  # 已经是正确的 UTF-8，直接返回

# Windows 环境下自动修正 sys.argv 中的中文参数
if sys.platform == 'win32':
    fixed_argv = []
    for arg in sys.argv:
        fixed_argv.append(_fix_encoding(arg))
    sys.argv = fixed_argv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

<<<<<<< HEAD
from vecrecall.core.engine import VecRecall
=======
from vecrecall.core.engine import (
    VecRecall, estimate_tokens, build_embedding_backend,
)
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

DEFAULT_DIR = os.path.expanduser("~/.vecrecall")
CONFIG_FILE = os.path.join(DEFAULT_DIR, "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
<<<<<<< HEAD
    return {"dir": DEFAULT_DIR, "wing": "default"}
=======
    return {"dir": DEFAULT_DIR, "wing": "default", "chain": False,
            "embedder": "auto"}
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f


def save_config(cfg: dict):
    os.makedirs(DEFAULT_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


<<<<<<< HEAD
def get_palace(cfg: dict) -> VecRecall:
    return VecRecall(base_dir=cfg["dir"], wing=cfg.get("wing", "default"))
=======
def build_extractor_from(cfg: dict, args=None):
    """从配置（args 可选覆盖）构造认知层抽取器；未配置返回 None（关闭认知层）。"""
    kind = (getattr(args, "extractor", None) if args else None) or cfg.get("extractor")
    if not kind or kind in ("none", "off"):
        return None
    from vecrecall.core.extractor import build_extractor
    if kind == "heuristic":
        return build_extractor("heuristic")
    model = (getattr(args, "llm_model", None) if args else None) or cfg.get("llm_model")
    base_url = (getattr(args, "llm_url", None) if args else None) or cfg.get("llm_url")
    api_key = (getattr(args, "llm_api_key", None) if args else None) or cfg.get("llm_api_key")
    return build_extractor(kind, model=model, base_url=base_url, api_key=api_key)


def _encryption_key(cfg: dict, args=None) -> str | None:
    """静态加密密钥来源优先级：CLI 参数 > 环境变量 VR_ENCRYPTION_KEY > 配置。"""
    return ((getattr(args, "encrypt_key", None) if args else None)
            or os.environ.get("VR_ENCRYPTION_KEY")
            or cfg.get("encryption_key"))


def get_palace(cfg: dict, args=None) -> VecRecall:
    emb = build_embedding_backend(
        cfg.get("embedder", "auto"),
        model=cfg.get("embed_model"),
        base_url=cfg.get("embed_url"),
    )
    return VecRecall(base_dir=cfg["dir"], wing=cfg.get("wing", "default"),
                     use_blockchain=cfg.get("chain", False),
                     embedding_backend=emb,
                     extractor=build_extractor_from(cfg, args),
                     encryption_key=_encryption_key(cfg, args))
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f


# ─────────────────────────────────────────────
# 格式化输出
# ─────────────────────────────────────────────

def _print_results(results, verbose: bool = False):
    if not results:
        print("  （无结果）")
        return
    for r in results:
        score_bar = "█" * int(r.score * 10) + "░" * (10 - int(r.score * 10))
        print(f"\n  [{r.layer}] score={r.score:.3f} {score_bar}")
        print(f"  wing={r.memory.wing}  topic={r.memory.topic}  id={r.memory.id[:8]}…")
        if r.memory.ui_summary:
            print(f"  摘要: {r.memory.ui_summary}")
        preview = r.memory.content[:200].replace("\n", " ")
        print(f"  内容: {preview}{'…' if len(r.memory.content) > 200 else ''}")


def _print_memories(memories):
    for m in memories:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.timestamp))
        print(f"\n  [{ts}] id={m.id[:8]}… importance={m.importance:.2f}")
        print(f"  wing={m.wing}  topic={m.topic}")
        if m.ui_summary:
            print(f"  摘要: {m.ui_summary}")
        preview = m.content[:150].replace("\n", " ")
        print(f"  {preview}{'…' if len(m.content) > 150 else ''}")


# ─────────────────────────────────────────────
# 子命令处理
# ─────────────────────────────────────────────

def cmd_init(args, cfg):
    if args.dir:
        cfg["dir"] = os.path.expanduser(args.dir)
    if args.wing:
        cfg["wing"] = args.wing
<<<<<<< HEAD
=======
    if args.chain:
        cfg["chain"] = True
    if getattr(args, "embedder", None):
        cfg["embedder"] = args.embedder
    if getattr(args, "embed_model", None):
        cfg["embed_model"] = args.embed_model
    if getattr(args, "embed_url", None):
        cfg["embed_url"] = args.embed_url
    if getattr(args, "extractor", None):
        cfg["extractor"] = args.extractor
    if getattr(args, "llm_model", None):
        cfg["llm_model"] = args.llm_model
    if getattr(args, "llm_url", None):
        cfg["llm_url"] = args.llm_url
    if getattr(args, "llm_api_key", None):
        cfg["llm_api_key"] = args.llm_api_key
    if getattr(args, "encrypt", False):
        if not cfg.get("encryption_key"):
            from vecrecall.core.crypto import Cipher
            cfg["encryption_key"] = Cipher.generate_key()
            print("  🔑 已生成加密密钥（请妥善保存，丢失将无法解密）")
    if getattr(args, "encrypt_key", None):
        cfg["encryption_key"] = args.encrypt_key
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    save_config(cfg)
    p = get_palace(cfg)
    stats = p.stats()
    print(f"✓ VecRecall 初始化完成")
    print(f"  数据目录: {cfg['dir']}")
    print(f"  默认 wing: {cfg['wing']}")
<<<<<<< HEAD
=======
    print(f"  哈希链:    {'启用' if cfg.get('chain') else '关闭'}")
    print(f"  嵌入后端:  {cfg.get('embedder', 'auto')}")
    print(f"  认知层:    {cfg.get('extractor') or '关闭'}")
    print(f"  静态加密:  {'启用' if cfg.get('encryption_key') else '关闭'}")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    print(f"  当前记忆数: {stats['total_memories']}")
    p.close()


def cmd_add(args, cfg):
<<<<<<< HEAD
    p = get_palace(cfg)
=======
    p = get_palace(cfg, args)
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    content = args.content
    if content == "-":
        content = sys.stdin.read()
    m = p.add(
        content=content,
        topic=args.topic or "general",
        wing=args.wing or cfg.get("wing"),
        importance=args.importance,
        ui_summary=args.summary or "",
    )
<<<<<<< HEAD
    print(f"✓ 已存入记忆")
=======
    if m.metadata.get("occurrences", 1) > 1:
        print(f"↻ 与既有记忆合并（已出现 {m.metadata['occurrences']} 次）")
    else:
        print(f"✓ 已存入记忆")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    print(f"  ID: {m.id}")
    print(f"  wing={m.wing}  topic={m.topic}  importance={m.importance:.2f}")
    p.close()


def cmd_add_file(args, cfg):
    """从文件存入记忆，支持任意大小，自动识别编码"""
    path = os.path.expanduser(args.file)
    if not os.path.exists(path):
        print(f"✗ 文件不存在: {path}", file=sys.stderr)
        return

    # 自动识别编码，优先 UTF-8，失败则尝试 GBK
    text = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if text is None:
        print(f"✗ 无法识别文件编码: {path}", file=sys.stderr)
        return

    size_kb = os.path.getsize(path) / 1024
    print(f"📂 读取文件: {path}")
    print(f"   大小: {size_kb:.1f} KB  字符数: {len(text):,}")

<<<<<<< HEAD
    p = get_palace(cfg)
=======
    p = get_palace(cfg, args)
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    m = p.add(
        content=text,
        topic=args.topic or "general",
        wing=args.wing or cfg.get("wing"),
        importance=args.importance,
        ui_summary=args.summary or "",
    )
<<<<<<< HEAD
    print(f"✓ 已存入记忆")
    print(f"  ID: {m.id}")
    print(f"  wing={m.wing}  topic={m.topic}  importance={m.importance:.2f}")
    print(f"  字符数: {len(text):,}  token 估算: {len(text)//4:,}")
=======
    if m.metadata.get("occurrences", 1) > 1:
        print(f"↻ 与既有记忆合并（已出现 {m.metadata['occurrences']} 次）")
    else:
        print(f"✓ 已存入记忆")
    print(f"  ID: {m.id}")
    print(f"  wing={m.wing}  topic={m.topic}  importance={m.importance:.2f}")
    print(f"  字符数: {len(text):,}  token 估算: {estimate_tokens(text):,}")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    p.close()


def cmd_get(args, cfg):
    """按 ID 查看记忆完整内容"""
    p = get_palace(cfg)
    mem = p._kg.get(args.id)
    if not mem:
        # 支持短 ID（前8位）模糊匹配
        conn = p._kg._conn
        rows = conn.execute(
            "SELECT * FROM memories WHERE id LIKE ?",
            (args.id + "%",)
        ).fetchall()
        if not rows:
            print(f"✗ 找不到记忆: {args.id}", file=sys.stderr)
            p.close()
            return
        mem = p._kg._row_to_memory(rows[0])

    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(mem.timestamp))
    print(f"\n📌 记忆详情")
    print(f"  ID:         {mem.id}")
    print(f"  时间:       {ts}")
    print(f"  wing:       {mem.wing}")
    print(f"  topic:      {mem.topic}")
    print(f"  importance: {mem.importance:.2f}")
    print(f"  字符数:     {len(mem.content):,}")
    if mem.ui_summary:
        print(f"  摘要:       {mem.ui_summary}")
    print(f"\n{'─' * 50}")
    print(mem.content)
    print(f"{'─' * 50}")
    p.close()

def cmd_search(args, cfg):
    p = get_palace(cfg)
    layer = args.layer or "l3"
<<<<<<< HEAD
    print(f"🔍 搜索: \"{args.query}\"  (层: {layer.upper()})")
=======
    hybrid = getattr(args, "hybrid", False)
    print(f"🔍 搜索: \"{args.query}\"  (层: {layer.upper()}"
          f"{', hybrid 融合' if hybrid else ''})")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    if layer == "l1":
        top = p._kg.top_by_importance(None, args.n or 15)
        from vecrecall.core.engine import RetrievalResult
        results = [RetrievalResult(m, m.importance, "L1") for m in top]
    elif layer == "l2":
<<<<<<< HEAD
        results = p._semantic_l2(args.query)
    else:
        results = p.search(args.query, args.n or 10)
=======
        results = p._semantic_l2(args.query, hybrid=hybrid)
    else:
        results = p.search(args.query, args.n or 10, hybrid=hybrid)
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    _print_results(results, verbose=args.verbose)
    print(f"\n  共 {len(results)} 条结果")
    p.close()


def cmd_context(args, cfg):
    p = get_palace(cfg)
    print(f"📋 构建四层上下文  query='{args.query[:50]}'")
    ctx = p.build_context(
        current_query=args.query,
        load_l2=True,
        load_l3=args.l3,
<<<<<<< HEAD
=======
        hybrid=getattr(args, "hybrid", False),
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    )
    print(f"\n[L0] 身份层")
    print(f"  {ctx.l0_identity}")

    print(f"\n[L1] 关键时刻 ({len(ctx.l1_key_moments)} 条)")
    _print_results(ctx.l1_key_moments)

    print(f"\n[L2] 语义触发上下文 ({len(ctx.l2_topic_context)} 条)")
    _print_results(ctx.l2_topic_context)

    if ctx.l3_deep_results:
        print(f"\n[L3] 深度检索 ({len(ctx.l3_deep_results)} 条)")
        _print_results(ctx.l3_deep_results)

    print(f"\n  估计 token 用量: {ctx.total_tokens_estimate}")
    p.close()


def cmd_stats(args, cfg):
    p = get_palace(cfg)
    stats = p.stats()
    wings = p.list_wings()
    print("📊 VecRecall 状态")
    print(f"  记忆总数:    {stats['total_memories']}")
    print(f"  Wing 数:     {stats['wings']}")
    print(f"  跨关联数:    {stats['cross_links']}")
    print(f"  数据目录:    {cfg['dir']}")
    print(f"  默认 wing:   {cfg.get('wing', 'default')}")
    print(f"\n  Wings: {', '.join(wings) if wings else '（空）'}")
<<<<<<< HEAD
    print(f"\n  向量后端: {type(p._vec).__name__}")
    print(f"  嵌入后端: {type(p._emb).__name__}")
=======
    print(f"\n  向量后端: {stats.get('vector_backend', type(p._vec).__name__)}")
    print(f"  嵌入后端: {stats.get('embedding_backend', type(p._emb).__name__)}")
    if "indexed_vectors" in stats:
        print(f"  索引向量: {stats['indexed_vectors']}")
    if "blockchain" in stats:
        print(f"  哈希链:    {stats['blockchain']}")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    p.close()


def cmd_wings(args, cfg):
    p = get_palace(cfg)
    wings = p.list_wings()
    print(f"🏛  Wings ({len(wings)} 个，仅用于 UI 组织，不影响检索)")
    for w in wings:
        topics = p.list_topics(w)
        print(f"  {w}  ({len(topics)} 个话题)")
    p.close()


def cmd_topics(args, cfg):
    p = get_palace(cfg)
    topics = p.list_topics(args.wing)
    wing_label = f"[{args.wing}]" if args.wing else "[全部]"
    print(f"🏷  话题 {wing_label} ({len(topics)} 个)")
    for t in topics:
        print(f"  {t}")
    p.close()


<<<<<<< HEAD
=======
def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _clip(text: str, n: int = 50) -> str:
    s = " ".join(str(text).split())
    return s[:n] + ("…" if len(s) > n else "")


def cmd_graph(args, cfg):
    """列出时序知识图谱的实体"""
    p = get_palace(cfg)
    stats = p.graph_stats()
    print("🕸  时序知识图谱")
    print(f"  实体: {stats['entities']}   状态: {stats['entity_states']}"
          f"   关系: {stats['relations']}")
    ents = p.list_entities(limit=args.limit)
    if not ents:
        print("\n  （空。启用认知层抽取器后自动填充："
              "vr init --extractor ollama --llm-model llama3.2）")
    for e in ents:
        attrs = " ".join(f"{k}={v}" for k, v in e["attributes"].items())
        suffix = f"  [{attrs}]" if attrs else ""
        print(f"  {e['display']:<20} 提及 {e['mentions']} 次{suffix}")
    p.close()


def cmd_entity(args, cfg):
    """查询实体画像与状态演化"""
    p = get_palace(cfg)
    e = p.entity(args.name)
    if not e:
        print(f"✗ 未找到实体: {args.name}")
        p.close()
        return
    print(f"🕸  实体: {e['display']}")
    print(f"  提及次数: {e['mentions']}")
    if e["attributes"]:
        print(f"  当前属性: {e['attributes']}")
    tl = e["timeline"]
    if tl:
        print(f"  状态演化 ({len(tl)} 条):")
        for s in tl:
            print(f"    {s['attribute']} = {s['value']}  ({_fmt_time(s['timestamp'])})")
    rels = e["relations"]
    if rels:
        print(f"  关联关系 ({len(rels)} 条):")
        for r in rels:
            print(f"    {r['source']} --{r['type']}--> {r['target']}")
    p.close()


def cmd_decay(args, cfg):
    """遗忘评估：按遗忘曲线列出低强度（已遗忘）记忆"""
    p = get_palace(cfg)
    forgotten = p.forgotten(threshold=args.threshold)
    print(f"🧠 遗忘评估（强度 < {args.threshold}）")
    if not forgotten:
        print("  暂无已遗忘记忆")
    for m in forgotten:
        s = p.memory_strength(m.id)
        print(f"  [{m.id[:8]}] 强度={s:.2f} imp={m.importance:.2f}"
              f"  {_clip(m.content, 40)}")
    p.close()


def cmd_distill(args, cfg):
    """主动蒸馏：把碎片化记忆压缩为抽象长期记忆"""
    p = get_palace(cfg)
    plans = p.distill(topic=args.topic, wing=args.wing,
                      min_cluster=args.min_cluster, dry_run=not args.yes)
    label = "（已执行）" if args.yes else "（dry-run，加 --yes 真正执行）"
    print(f"🧪 主动蒸馏 {label}")
    if not plans:
        print("  无可蒸馏的碎片化记忆簇")
    for pl in plans:
        print(f"  话题[{pl['topic']}] {pl['count']} 条 → {_clip(pl['summary'], 60)}")
    p.close()


def cmd_graph_search(args, cfg):
    """多跳向量+图谱混合检索"""
    p = get_palace(cfg)
    res = p.graph_search(args.query, n=args.n, hops=args.hops)
    print(f"🕸  多跳混合检索: {args.query}")
    print(f"\n  直接命中 ({len(res['direct'])} 条):")
    for r in res["direct"]:
        print(f"    [{r.score:.2f}] {_clip(r.memory.content, 50)}")
    print(f"\n  图谱扩展 ({len(res['graph'])} 条):")
    for h in res["graph"]:
        ents = ", ".join(h["entities"])
        print(f"    [{h['memory'].importance:.2f}] {_clip(h['memory'].content, 50)}"
              f"  ← {ents}")
        for pth in h["paths"][:1]:
            via = " → ".join(f"{e['from']}-{e['type']}->{e['to']}"
                             for e in pth["path"])
            print(f"        路径: {via}")
    p.close()


>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
def cmd_diary(args, cfg):
    p = get_palace(cfg)
    if args.action == "write":
        agent_wing = f"agent:{args.agent}"
        content = args.content
        if content == "-":
            content = sys.stdin.read()
        m = p.add(content=content, topic="diary", wing=agent_wing)
        print(f"✓ [{args.agent}] 日记已写入  id={m.id[:8]}…")
    elif args.action == "read":
        agent_wing = f"agent:{args.agent}"
        query = args.query or ""
        if query:
            results = p.search(query, 10)
            results = [r for r in results if r.memory.wing == agent_wing]
            _print_results(results)
        else:
            mems = p._kg.top_by_importance(agent_wing, 10)
            _print_memories(mems)
    p.close()


def cmd_archive(args, cfg):
    p = get_palace(cfg)
    with open(args.file) as f:
        messages = json.load(f)
    combined = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
    sid = str(int(time.time()))
    mem = p.add(content=combined, topic="session",
                wing=cfg.get("wing"), session_id=sid)
    print(f"✓ 会话已存档  id={mem.id}  chars={len(combined)}")
    p.close()


def cmd_export(args, cfg):
    p = get_palace(cfg)
    mems = p._kg.top_by_importance(args.wing, 999999)
    data = [
        {"id": m.id, "wing": m.wing, "topic": m.topic,
         "content": m.content, "importance": m.importance,
         "ui_summary": m.ui_summary, "timestamp": m.timestamp}
        for m in mems
    ]
    out = args.out or f"{args.wing}_export.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ 已导出 {len(data)} 条记忆到 {out}")
    p.close()


def cmd_import(args, cfg):
    p = get_palace(cfg)
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    items = [
        {"content": d["content"], "topic": d.get("topic", "general"),
         "wing": d.get("wing"), "importance": d.get("importance"),
         "ui_summary": d.get("ui_summary", "")}
        for d in data
    ]
    ms = p.add_batch(items)
    print(f"✓ 已导入 {len(ms)} 条记忆")
    p.close()


<<<<<<< HEAD
=======
def cmd_delete(args, cfg):
    """按 ID（支持短 ID 前 8 位）删除记忆"""
    p = get_palace(cfg)
    mem = p._kg.get(args.id)
    if not mem:
        conn = p._kg._conn
        rows = conn.execute(
            "SELECT id FROM memories WHERE id LIKE ?", (args.id + "%",)
        ).fetchall()
        if not rows:
            print(f"✗ 找不到记忆: {args.id}", file=sys.stderr)
            p.close()
            return
        mem_id = rows[0][0]
    else:
        mem_id = mem.id

    ok = p.delete(mem_id)
    print(f"✓ 已删除记忆 id={mem_id[:8]}…" if ok else "✗ 删除失败")
    p.close()


def cmd_prune(args, cfg):
    """清理低价值记忆"""
    p = get_palace(cfg)
    if args.min_importance is None and args.older_than is None:
        print("✗ 请指定 --min-importance 或 --older-than", file=sys.stderr)
        p.close()
        return

    victims = p.prune(
        min_importance=args.min_importance,
        older_than_days=args.older_than,
        wing=args.wing,
        dry_run=not args.yes,
    )
    if args.yes:
        print(f"✓ 已清理 {len(victims)} 条记忆")
    else:
        print(f"⚠ 找到 {len(victims)} 条候选（预览模式，加 --yes 确认删除）")
        for m in victims[:20]:
            ts = time.strftime("%Y-%m-%d", time.localtime(m.timestamp))
            print(f"  [{ts}] imp={m.importance:.2f} {m.content[:60]}")
        if len(victims) > 20:
            print(f"  ... 及另外 {len(victims) - 20} 条")
    p.close()


def cmd_verify(args, cfg):
    """验证哈希链完整性（需 --chain 启用）"""
    p = get_palace(cfg)
    result = p.verify_integrity(args.wing)
    if not result.get("enabled"):
        print("✗ " + result["message"], file=sys.stderr)
    else:
        print("🔐 哈希链完整性验证")
        all_ok = True
        for w, info in result["wings"].items():
            mark = "✓" if info["valid"] else "✗ 被篡改"
            all_ok = all_ok and info["valid"]
            print(f"  [{mark}] wing={w}  {info['message']}")
        print("\n" + ("✓ 全部区块完整，未检测到篡改" if all_ok
                       else "✗ 检测到篡改！"))
    p.close()


def cmd_reindex(args, cfg):
    """从 SQLite 原文重建全部向量索引"""
    p = get_palace(cfg)
    print("🔄 重建向量索引中...")
    count = p.reindex()
    print(f"✓ 已重建 {count} 条记忆的向量索引")
    p.close()


def _embedder_from_args(args, cfg):
    """从 CLI 参数 + config 统一构造嵌入后端（供 mcp / api 使用）"""
    return build_embedding_backend(
        getattr(args, "embedder", None) or cfg.get("embedder", "auto"),
        model=getattr(args, "embed_model", None) or cfg.get("embed_model"),
        base_url=getattr(args, "embed_url", None) or cfg.get("embed_url"),
        api_key=getattr(args, "embed_api_key", None),
    )


>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
def cmd_mcp(args, cfg):
    """启动 MCP stdio 服务器"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from vecrecall.mcp.server import MCPServer
    d = args.dir or cfg["dir"]
    w = args.wing or cfg.get("wing", "default")
<<<<<<< HEAD
    print(f"[VecRecall MCP] 启动中  dir={d}  wing={w}", file=sys.stderr)
    server = MCPServer(base_dir=d, wing=w)
    server.run()
=======
    chain = args.chain or cfg.get("chain", False)
    emb = _embedder_from_args(args, cfg)
    print(f"[VecRecall MCP] 启动中  dir={d}  wing={w}  chain={chain}  "
          f"embedder={type(emb).__name__}", file=sys.stderr)
    server = MCPServer(base_dir=d, wing=w, use_blockchain=chain,
                       embedding_backend=emb,
                       extractor=build_extractor_from(cfg, args),
                       encryption_key=_encryption_key(cfg, args))
    server.run()


def cmd_api(args, cfg):
    """启动 HTTP REST API 服务器（供传统软件接入）"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from vecrecall.api.server import VecRecallHTTPServer, resolve_api_key
    d = args.dir or cfg["dir"]
    w = args.wing or cfg.get("wing", "default")
    chain = args.chain or cfg.get("chain", False)
    emb = _embedder_from_args(args, cfg)
    api_key = resolve_api_key(getattr(args, "api_key", None), args.host)
    palace = VecRecall(
        base_dir=d, wing=w, use_blockchain=chain, embedding_backend=emb,
        extractor=build_extractor_from(cfg, args),
        encryption_key=_encryption_key(cfg, args),
    )
    server = VecRecallHTTPServer((args.host, args.port), palace,
                                 api_key=api_key,
                                 cors_origin=getattr(args, "cors_origin", None))
    auth_note = ("已启用 API Key 认证" if api_key
                 else "无认证（仅本机，建议生产环境提供 --api-key）")
    print(f"[VecRecall API] 监听 http://{args.host}:{args.port}  "
          f"embedder={type(emb).__name__}  wing={w}", file=sys.stderr)
    print(f"[VecRecall API] 安全: {auth_note}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        palace.close()


def cmd_list(args, cfg):
    """回吐记忆列表（结构化清单）"""
    p = get_palace(cfg)
    result = p.list_memories(
        wing=args.wing, topic=args.topic,
        limit=args.limit, offset=args.offset,
        full=args.full, sort=args.sort,
    )
    print(f"📋 记忆列表  共 {result['total']} 条  "
          f"(显示 {len(result['memories'])} 条)\n")
    for m in result["memories"]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["timestamp"]))
        print(f"  [{ts}] {m['id'][:8]}…  imp={m['importance']:.2f}  "
              f"wing={m['wing']}  topic={m['topic']}")
        if m["ui_summary"]:
            print(f"      摘要: {m['ui_summary']}")
        print(f"      {m['content_preview'].replace(chr(10), ' ')}")
    p.close()
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
def cmd_browse(args, cfg):
    """按日期浏览记忆目录（格式：日期  |  话题+内容预览）"""
    p = get_palace(cfg)
    conn = p._kg._conn
    wing_filter = args.wing or cfg.get("wing")

    # 取所有日期（降序）
    if wing_filter and not args.all:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch', 'localtime')) "
            "as d FROM memories WHERE wing=? ORDER BY d DESC",
            (wing_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch', 'localtime')) "
            "as d FROM memories ORDER BY d DESC"
        ).fetchall()

    if not rows:
        print("  （暂无记忆）")
        p.close()
        return

    wing_label = f"[{wing_filter}]" if (wing_filter and not args.all) else "[全部]"
    print(f"📋 记忆目录  {wing_label}  共 {len(rows)} 天\n")

    for (date,) in rows:
        # 取当天所有记忆
        if wing_filter and not args.all:
            mems = conn.execute(
                "SELECT topic, content FROM memories "
                "WHERE wing=? AND strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch', 'localtime'))=? "
                "ORDER BY importance DESC",
                (wing_filter, date)
            ).fetchall()
        else:
            mems = conn.execute(
                "SELECT topic, content FROM memories "
                "WHERE strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch', 'localtime'))=? "
                "ORDER BY importance DESC",
                (date,)
            ).fetchall()

        # 格式：日期  |  [话题] 内容预览  [话题] 内容预览...
        entries = []
        for topic, content in mems:
            preview = content[:40].replace("\n", " ")
            entries.append(f"[{topic}] {preview}")

        # 每行最多显示 3 条，超出显示数量
        shown = entries[:3]
        rest = len(entries) - 3
        line = "  ".join(shown)
        if rest > 0:
            line += f"  （+{rest} 条）"

        print(f"  {date}  |  {line}")

    print()
    p.close()




# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        prog="vr",
        description="VecRecall — 改进版 AI 长期记忆系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    # init
    p_init = sub.add_parser("init", help="初始化")
    p_init.add_argument("--dir", help="数据目录")
    p_init.add_argument("--wing", help="默认 wing")
<<<<<<< HEAD
=======
    p_init.add_argument("--chain", action="store_true",
                        help="启用哈希链防篡改（写入记忆时生成区块）")
    p_init.add_argument("--embedder",
                        choices=["auto", "bow", "sentence", "ollama", "api"],
                        help="嵌入后端（默认 auto）")
    p_init.add_argument("--embed-model", help="嵌入模型名")
    p_init.add_argument("--embed-url", help="Ollama 或 API 服务地址")
    p_init.add_argument("--extractor",
                        choices=["heuristic", "ollama", "api", "none"],
                        help="认知层抽取器（默认关闭）")
    p_init.add_argument("--llm-model", help="抽取器 LLM 模型名")
    p_init.add_argument("--llm-url", help="抽取器 LLM 服务地址")
    p_init.add_argument("--llm-api-key", help="抽取器 LLM API Key")
    p_init.add_argument("--encrypt", action="store_true",
                        help="启用静态加密（自动生成密钥，记忆内容加密落盘）")
    p_init.add_argument("--encrypt-key", help="指定加密密钥（44 字符 base64）或密码")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    # add
    p_add = sub.add_parser("add", help="添加记忆")
    p_add.add_argument("content", help="内容，- 表示从 stdin 读取")
    p_add.add_argument("--topic", help="话题标签")
    p_add.add_argument("--wing", help="wing 标识")
    p_add.add_argument("--importance", type=float, help="重要性 0-1")
    p_add.add_argument("--summary", help="AAAK 摘要（仅 UI 展示）")
<<<<<<< HEAD
=======
    p_add.add_argument("--extractor",
                       choices=["heuristic", "ollama", "api", "none"],
                       help="临时覆盖认知层抽取器")
    p_add.add_argument("--llm-model", help="临时覆盖抽取器 LLM 模型")
    p_add.add_argument("--llm-url", help="临时覆盖抽取器 LLM 服务地址")
    p_add.add_argument("--llm-api-key", help="临时覆盖抽取器 LLM API Key")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    # add-file
    p_addf = sub.add_parser("add-file", help="从文件存入记忆（支持大文件，自动识别编码）")
    p_addf.add_argument("file", help="文件路径")
    p_addf.add_argument("--topic", help="话题标签")
    p_addf.add_argument("--wing", help="wing 标识")
    p_addf.add_argument("--importance", type=float, help="重要性 0-1")
    p_addf.add_argument("--summary", help="摘要（仅 UI 展示）")
<<<<<<< HEAD
=======
    p_addf.add_argument("--extractor",
                        choices=["heuristic", "ollama", "api", "none"],
                        help="临时覆盖认知层抽取器")
    p_addf.add_argument("--llm-model", help="临时覆盖抽取器 LLM 模型")
    p_addf.add_argument("--llm-url", help="临时覆盖抽取器 LLM 服务地址")
    p_addf.add_argument("--llm-api-key", help="临时覆盖抽取器 LLM API Key")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    # search
    p_search = sub.add_parser("search", help="语义搜索")
    p_search.add_argument("query")
    p_search.add_argument("--n", type=int, default=10)
    p_search.add_argument("--layer", choices=["l1", "l2", "l3"], default="l3")
<<<<<<< HEAD
=======
    p_search.add_argument("--hybrid", action="store_true",
                          help="融合相似度+重要性+时间新鲜度重排")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    p_search.add_argument("--verbose", action="store_true")

    # context
    p_ctx = sub.add_parser("context", help="构建四层上下文")
    p_ctx.add_argument("query", nargs="?", default="")
    p_ctx.add_argument("--l3", action="store_true", help="启用 L3 深度检索")
<<<<<<< HEAD
=======
    p_ctx.add_argument("--hybrid", action="store_true", help="L2/L3 融合重排")
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    # stats
    sub.add_parser("stats", help="系统状态")

    # wings
    sub.add_parser("wings", help="列出所有 wing")

    # topics
    p_topics = sub.add_parser("topics", help="列出话题")
    p_topics.add_argument("--wing", help="筛选某个 wing")

    # diary
    p_diary = sub.add_parser("diary", help="Agent 日记")
    diary_sub = p_diary.add_subparsers(dest="action")
    p_dw = diary_sub.add_parser("write")
    p_dw.add_argument("agent")
    p_dw.add_argument("content", help="内容，- 从 stdin 读取")
    p_dr = diary_sub.add_parser("read")
    p_dr.add_argument("agent")
    p_dr.add_argument("query", nargs="?", default="")

    # archive
    p_arch = sub.add_parser("archive", help="存档会话 JSON")
    p_arch.add_argument("file")

    # export
    p_exp = sub.add_parser("export", help="导出 wing 数据")
    p_exp.add_argument("wing")
    p_exp.add_argument("--out", help="输出文件路径")

    # import
    p_imp = sub.add_parser("import", help="从 JSON 导入")
    p_imp.add_argument("file")

    # get
    p_get = sub.add_parser("get", help="按 ID 查看记忆完整内容（支持短 ID 前8位）")
    p_get.add_argument("id", help="记忆 ID 或前8位短 ID")

    # browse
    p_browse = sub.add_parser("browse", help="按日期浏览记忆目录")
    p_browse.add_argument("--wing", help="筛选某个 wing")
    p_browse.add_argument("--all", action="store_true", help="显示所有 wing")

<<<<<<< HEAD
=======
    # delete
    p_del = sub.add_parser("delete", help="删除记忆（支持短 ID 前 8 位）")
    p_del.add_argument("id")

    # prune
    p_prune = sub.add_parser("prune", help="清理低价值记忆")
    p_prune.add_argument("--min-importance", type=float, help="重要性低于此值")
    p_prune.add_argument("--older-than", type=float, help="早于 N 天")
    p_prune.add_argument("--wing", help="只清理某个 wing")
    p_prune.add_argument("--yes", action="store_true", help="确认真正删除")

    # verify
    p_verify = sub.add_parser("verify", help="验证哈希链完整性")
    p_verify.add_argument("--wing", help="只验证某个 wing")

    # reindex
    sub.add_parser("reindex", help="重建全部向量索引")

>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    # mcp
    p_mcp = sub.add_parser("mcp", help="启动 MCP 服务器")
    p_mcp.add_argument("--dir")
    p_mcp.add_argument("--wing")
<<<<<<< HEAD
=======
    p_mcp.add_argument("--chain", action="store_true", help="启用哈希链")
    p_mcp.add_argument("--embedder",
                       choices=["auto", "bow", "sentence", "ollama", "api"],
                       help="嵌入后端")
    p_mcp.add_argument("--embed-model", help="嵌入模型名")
    p_mcp.add_argument("--embed-url", help="Ollama 或 API 服务地址")
    p_mcp.add_argument("--embed-api-key", help="远程 API Key")
    p_mcp.add_argument("--extractor",
                       choices=["heuristic", "ollama", "api", "none"],
                       help="认知层抽取器（默认关闭）")
    p_mcp.add_argument("--llm-model", help="抽取器 LLM 模型名")
    p_mcp.add_argument("--llm-url", help="抽取器 LLM 服务地址")
    p_mcp.add_argument("--llm-api-key", help="抽取器 LLM API Key")

    # api（HTTP REST，供传统软件接入）
    p_api = sub.add_parser("api", help="启动 HTTP REST API 服务器")
    p_api.add_argument("--dir")
    p_api.add_argument("--wing")
    p_api.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_api.add_argument("--port", type=int, default=8791, help="监听端口")
    p_api.add_argument("--chain", action="store_true", help="启用哈希链")
    p_api.add_argument("--embedder",
                       choices=["auto", "bow", "sentence", "ollama", "api"],
                       help="嵌入后端")
    p_api.add_argument("--embed-model", help="嵌入模型名")
    p_api.add_argument("--embed-url", help="Ollama 或 API 服务地址")
    p_api.add_argument("--embed-api-key", help="远程 API Key")
    p_api.add_argument("--api-key", help="REST API 访问令牌（缺省读环境变量 VR_API_KEY）")
    p_api.add_argument("--cors-origin", help="允许跨域的 Origin（默认禁止跨域）")
    p_api.add_argument("--extractor",
                       choices=["heuristic", "ollama", "api", "none"],
                       help="认知层抽取器（默认关闭）")
    p_api.add_argument("--llm-model", help="抽取器 LLM 模型名")
    p_api.add_argument("--llm-url", help="抽取器 LLM 服务地址")
    p_api.add_argument("--llm-api-key", help="抽取器 LLM API Key")

    # list（回吐记忆列表）
    p_list = sub.add_parser("list", help="回吐记忆列表（结构化清单）")
    p_list.add_argument("--wing", help="筛选某个 wing")
    p_list.add_argument("--topic", help="筛选某个话题")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--full", action="store_true", help="显示原文")
    p_list.add_argument("--sort", choices=["importance", "recent"],
                        default="importance")

    p_graph = sub.add_parser("graph", help="列出时序知识图谱实体")
    p_graph.add_argument("--limit", type=int, default=50)

    p_entity = sub.add_parser("entity", help="查询实体画像与状态演化")
    p_entity.add_argument("name", help="实体名")

    p_decay = sub.add_parser("decay", help="遗忘评估（按遗忘曲线列出低强度记忆）")
    p_decay.add_argument("--threshold", type=float, default=0.2)

    p_distill = sub.add_parser("distill", help="主动蒸馏（碎片化记忆压缩为抽象长期记忆）")
    p_distill.add_argument("--topic", help="限定话题")
    p_distill.add_argument("--wing", help="限定 wing")
    p_distill.add_argument("--min-cluster", type=int, default=2)
    p_distill.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")

    p_gs = sub.add_parser("graph-search", help="多跳向量+图谱混合检索")
    p_gs.add_argument("query", help="查询")
    p_gs.add_argument("--n", type=int, default=10)
    p_gs.add_argument("--hops", type=int, default=2)
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init, "add": cmd_add, "search": cmd_search,
        "context": cmd_context, "stats": cmd_stats, "wings": cmd_wings,
        "topics": cmd_topics, "diary": cmd_diary, "archive": cmd_archive,
        "export": cmd_export, "import": cmd_import, "mcp": cmd_mcp,
        "browse": cmd_browse, "add-file": cmd_add_file,
<<<<<<< HEAD
        "get": cmd_get,
=======
        "get": cmd_get, "delete": cmd_delete, "prune": cmd_prune,
        "verify": cmd_verify, "reindex": cmd_reindex,
        "api": cmd_api, "list": cmd_list,
        "graph": cmd_graph, "entity": cmd_entity,
        "decay": cmd_decay, "distill": cmd_distill,
        "graph-search": cmd_graph_search,
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
    }

    fn = dispatch.get(args.cmd)
    if fn:
        try:
            fn(args, cfg)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"✗ 错误: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
