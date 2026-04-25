"""
VecRecall — CLI

用法:
  vr init [--dir DIR] [--wing WING]
  vr add "内容" [--topic TOPIC] [--wing WING] [--importance 0.8]
  vr search "查询" [--n 10] [--layer l3]
  vr context "当前对话" [--l3]
  vr stats
  vr wings
  vr topics [--wing WING]
  vr diary write AGENT "内容"
  vr diary read AGENT [QUERY]
  vr archive SESSION_FILE
  vr export WING [--out FILE]
  vr import FILE
  vr mcp [--dir DIR] [--wing WING]
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

from vecrecall.core.engine import VecRecall

DEFAULT_DIR = os.path.expanduser("~/.vecrecall")
CONFIG_FILE = os.path.join(DEFAULT_DIR, "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"dir": DEFAULT_DIR, "wing": "default"}


def save_config(cfg: dict):
    os.makedirs(DEFAULT_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_palace(cfg: dict) -> VecRecall:
    return VecRecall(base_dir=cfg["dir"], wing=cfg.get("wing", "default"))


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
    save_config(cfg)
    p = get_palace(cfg)
    stats = p.stats()
    print(f"✓ VecRecall 初始化完成")
    print(f"  数据目录: {cfg['dir']}")
    print(f"  默认 wing: {cfg['wing']}")
    print(f"  当前记忆数: {stats['total_memories']}")
    p.close()


def cmd_add(args, cfg):
    p = get_palace(cfg)
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
    print(f"✓ 已存入记忆")
    print(f"  ID: {m.id}")
    print(f"  wing={m.wing}  topic={m.topic}  importance={m.importance:.2f}")
    p.close()


def cmd_search(args, cfg):
    p = get_palace(cfg)
    layer = args.layer or "l3"
    print(f"🔍 搜索: \"{args.query}\"  (层: {layer.upper()})")

    if layer == "l1":
        top = p._kg.top_by_importance(None, args.n or 15)
        from vecrecall.core.engine import RetrievalResult
        results = [RetrievalResult(m, m.importance, "L1") for m in top]
    elif layer == "l2":
        results = p._semantic_l2(args.query)
    else:
        results = p.search(args.query, args.n or 10)

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
    print(f"\n  向量后端: {type(p._vec).__name__}")
    print(f"  嵌入后端: {type(p._emb).__name__}")
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


def cmd_mcp(args, cfg):
    """启动 MCP stdio 服务器"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from vecrecall.mcp.server import MCPServer
    d = args.dir or cfg["dir"]
    w = args.wing or cfg.get("wing", "default")
    print(f"[VecRecall MCP] 启动中  dir={d}  wing={w}", file=sys.stderr)
    server = MCPServer(base_dir=d, wing=w)
    server.run()
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

    # add
    p_add = sub.add_parser("add", help="添加记忆")
    p_add.add_argument("content", help="内容，- 表示从 stdin 读取")
    p_add.add_argument("--topic", help="话题标签")
    p_add.add_argument("--wing", help="wing 标识")
    p_add.add_argument("--importance", type=float, help="重要性 0-1")
    p_add.add_argument("--summary", help="AAAK 摘要（仅 UI 展示）")

    # search
    p_search = sub.add_parser("search", help="语义搜索")
    p_search.add_argument("query")
    p_search.add_argument("--n", type=int, default=10)
    p_search.add_argument("--layer", choices=["l1", "l2", "l3"], default="l3")
    p_search.add_argument("--verbose", action="store_true")

    # context
    p_ctx = sub.add_parser("context", help="构建四层上下文")
    p_ctx.add_argument("query", nargs="?", default="")
    p_ctx.add_argument("--l3", action="store_true", help="启用 L3 深度检索")

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

    # browse
    p_browse = sub.add_parser("browse", help="按日期浏览记忆目录")
    p_browse.add_argument("--wing", help="筛选某个 wing")
    p_browse.add_argument("--all", action="store_true", help="显示所有 wing")

    # mcp
    p_mcp = sub.add_parser("mcp", help="启动 MCP 服务器")
    p_mcp.add_argument("--dir")
    p_mcp.add_argument("--wing")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init, "add": cmd_add, "search": cmd_search,
        "context": cmd_context, "stats": cmd_stats, "wings": cmd_wings,
        "topics": cmd_topics, "diary": cmd_diary, "archive": cmd_archive,
        "export": cmd_export, "import": cmd_import, "mcp": cmd_mcp,
        "browse": cmd_browse,
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
