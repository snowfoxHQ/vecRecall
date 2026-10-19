#!/usr/bin/env python3
"""凭据审计：扫描已存记忆，检查是否混入了凭据。

只读，不修改任何数据。命中时只报告位置与类型，**绝不打印凭据原文** ——
审计输出本身可能被记录、转发或贴进对话，不能成为新的泄露面。

用法：
    python scripts/audit_secrets.py                 # 扫描 ~/.vecrecall
    python scripts/audit_secrets.py --dir /path     # 指定记忆库
    python scripts/audit_secrets.py --wing hestiaOS # 只扫某个 wing

退出码：0=干净，1=发现疑似凭据，2=找不到记忆库。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vecrecall.core.guard import find_secrets  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描记忆库中是否混入了凭据")
    ap.add_argument("--dir", default="~/.vecrecall", help="记忆库目录（默认 ~/.vecrecall）")
    ap.add_argument("--wing", default=None, help="只扫描指定 wing")
    args = ap.parse_args()

    base = Path(args.dir).expanduser()
    db = base / "knowledge.db"
    if not db.exists():
        print(f"找不到记忆库：{db}")
        return 2

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "select id, wing, coalesce(topic,''), content from memories"
    ).fetchall()
    if args.wing:
        rows = [r for r in rows if r[1] == args.wing]

    hits = []
    for mid, wing, topic, content in rows:
        kinds = find_secrets(content or "")
        if kinds:
            hits.append((mid, wing, topic, kinds))

    print(f"扫描 {len(rows)} 条记忆（{base}）")
    if not hits:
        print("未发现疑似凭据。")
        return 0

    print(f"\n发现 {len(hits)} 条疑似包含凭据：")
    for mid, wing, topic, kinds in hits:
        print(f"  {mid[:8]}  [{wing}]  {topic:<14} {'、'.join(kinds)}")
    print("\n处理：用 vecrecall forget <id> 删除，或去掉敏感值后重新写入。")
    print("（输出刻意不显示凭据原文，避免审计本身造成二次泄露。）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
