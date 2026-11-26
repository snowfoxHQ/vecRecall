"""v2.6.1 能力对照表：MCP / REST / CLI 三个入口与 vecrecall/capabilities.py 保持一致。

- 每个入口实际注册的工具 / 路由 / 子命令必须都登记在表里（新增入口必须先登记）
- 表里写了的入口必须真的存在
- 缺席的入口必须写明 reason
- 新补齐的入口能跑通
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vecrecall.capabilities import CAPABILITIES, entries  # noqa: E402
from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, VecRecall,
)
from vecrecall.core.extractor import HeuristicExtractor  # noqa: E402

PASS = FAIL = 0


def test(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def actual_mcp() -> set[str]:
    from vecrecall.mcp.server import TOOLS
    return {t["name"] for t in TOOLS}


def actual_cli() -> set[str]:
    src = read("vecrecall/cli/main.py")
    i = src.index("dispatch = {")
    j = src.index("}", i)
    return set(re.findall(r'"([\w-]+)":', src[i:j]))


def actual_rest() -> set[str]:
    """从 _route_get / post / put / delete 的源码里解析出 "方法 路径"。"""
    src = read("vecrecall/api/server.py")
    out = set()
    for method in ("get", "post", "put", "delete"):
        i = src.index(f"    def _route_{method}(self)")
        j = src.find("\n    def ", i + 10)
        body = src[i:j if j > 0 else len(src)]
        M = method.upper()
        for head in re.findall(r'head == "([\w-]+)"', body):
            out.add(f"{M} /{head}")
        if 'segs[1] == "batch"' in body:
            out.add(f"{M} /memories/batch")
        if 'segs[2] == "provenance"' in body:
            out.add(f"{M} /memories/{{id}}/provenance")
        if re.search(r'head == "memories"', body) and "segs[1]" in body:
            out.add(f"{M} /memories/{{id}}")
        if re.search(r'head == "entities" and len\(segs\) == 2', body):
            out.discard(f"{M} /entities")
            out.add(f"{M} /entities/{{name}}")
        if re.search(r'len\(segs\) == 2 and segs\[0\] == "memories"', body):
            out.add(f"{M} /memories/{{id}}")
    out.discard("POST /memories/{id}")
    return out


def test_matrix():
    print("\n[对照表]")
    for kind, actual in (("mcp", actual_mcp()), ("cli", actual_cli()), ("rest", actual_rest())):
        listed = entries(kind)
        unlisted = sorted(actual - listed)
        missing = sorted(listed - actual)
        test(f"{kind}：实际注册的都已登记", not unlisted, f"未登记: {unlisted}")
        test(f"{kind}：登记的都已实现", not missing, f"未实现: {missing}")
    no_reason = [c for c, v in CAPABILITIES.items()
                 if not all(v.get(k) for k in ("mcp", "rest", "cli")) and not v.get("reason") and not v.get("note")]
    test("缺席的入口都写明原因", not no_reason, str(no_reason))
    full = [c for c, v in CAPABILITIES.items() if all(v.get(k) for k in ("mcp", "rest", "cli"))]
    test("至少 15 项能力三个入口都有", len(full) >= 15, f"{len(full)}: {full}")


def make(d):
    return VecRecall(base_dir=d, wing="cap", embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend(), dedup=False, extractor=HeuristicExtractor())


def test_mcp_new():
    print("\n[MCP 新工具]")
    from vecrecall.mcp.server import MCPServer
    with tempfile.TemporaryDirectory() as d:
        s = MCPServer(base_dir=d, wing="cap", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=NumpyVectorBackend(), extractor=HeuristicExtractor())
        s._call("mp_add", {"content": "Alice leads the Phoenix project"})
        s._call("mp_add", {"content": "Phoenix uses PostgreSQL as primary database"})
        r = s._call("mp_graph_search", {"query": "Phoenix", "n": 5})
        test("mp_graph_search 返回 direct / graph", isinstance(r.get("direct"), list) and "graph" in r)
        json.dumps(r, ensure_ascii=False)
        test("mp_graph_search 结果可 JSON 序列化", True)
        e = s._call("mp_entity", {"name": "Phoenix"})
        test("mp_entity", e.get("display") == "Phoenix" and e.get("mentions") == 2, str(e)[:200])
        try:
            s._call("mp_entity", {"name": "Nobody"})
            test("mp_entity 不存在时报错", False)
        except ValueError:
            test("mp_entity 不存在时报错", True)
        test("mp_check", s._call("mp_check", {}).get("ok") is True)
        r = s._call("mp_distill", {"topic": "general"})
        test("mp_distill 默认 dry_run", r["dry_run"] is True and isinstance(r["plans"], list))
        s._palace.close()


def test_rest_new():
    print("\n[REST 新路由]")
    from vecrecall.api.server import VecRecallHTTPServer
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        srv = VecRecallHTTPServer(("127.0.0.1", 0), p, api_key="k")  # 与下面 TOKEN 一致
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)
        TOKEN = "k"
        H = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}

        def call(path, body=None, method=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=H,
                                         method=method or ("POST" if body is not None else "GET"))
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        code, r = call("/archive", {"messages": [{"role": "user", "content": "我们决定用 PostgreSQL 做主库"},
                                                 {"role": "assistant", "content": "好的"}],
                                    "session_id": "s1"})
        test("POST /archive", code == 200 and r["archived"] == 2, str(r))
        code, r = call("/memories", {"content": "主库从 PostgreSQL 换成 MySQL"})
        new_id = r.get("id")
        code, r = call("/contradictions")
        test("GET /contradictions", code == 200 and r["total"] == 1, str(r))
        old_id = r["items"][0]["old_id"] if r.get("items") else ""
        code, r = call("/supersede", {"action": "dismiss", "old_id": old_id})
        test("POST /supersede dismiss", code == 200 and r.get("ok"), str(r))
        code, r = call("/supersede", {"action": "mark", "old_id": old_id, "new_id": new_id})
        test("POST /supersede mark", code == 200 and r.get("superseded_by") == new_id, str(r))
        code, r = call("/supersede", {"action": "nope", "old_id": old_id})
        test("POST /supersede 非法 action 返回 400", code == 400)
        call("/memories", {"content": "Alice leads the Phoenix project"})
        code, r = call("/entities/Phoenix")
        test("GET /entities/{name}", code == 200 and r.get("display") == "Phoenix", str(r)[:200])
        code, _ = call("/entities/Nobody")
        test("GET /entities 不存在返回 400", code == 400)
        code, r = call("/graph-search", {"query": "Phoenix"})
        test("POST /graph-search", code == 200 and isinstance(r.get("direct"), list), str(r)[:200])
        code, _ = call("/graph-search", {"query": "x", "hops": 99})
        test("POST /graph-search hops 超范围返回 400", code == 400)
        code, r = call("/check", {})
        test("POST /check", code == 200 and r.get("ok") is True, str(r))
        code, r = call("/export?wing=cap")
        test("GET /export", code == 200 and r["count"] >= 4 and "content" in r["memories"][0], str(r)[:200])
        code, _ = call("/export")
        test("GET /export 缺 wing 返回 400", code == 400)
        srv.shutdown()
        srv.server_close()
        p.close()


def test_docs():
    print("\n[文档]")
    doc = read("docs/interfaces.md")
    for cap, v in CAPABILITIES.items():
        if v.get("mcp") and f"`{v['mcp']}`" not in doc:
            test(f"interfaces.md 列出 {v['mcp']}", False)
            return
    test("interfaces.md 列出全部 MCP 工具", True)
    n_mcp, n_cli = len(entries("mcp")), len(entries("cli"))
    readme = read("README.md")
    test("README 的 MCP 工具数一致", f"{n_mcp} 个工具" in readme, f"应为 {n_mcp}")
    test("README 的 CLI 子命令数一致", f"{n_cli} 个子命令" in readme, f"应为 {n_cli}")


if __name__ == "__main__":
    test_matrix()
    test_mcp_new()
    test_rest_new()
    test_docs()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
