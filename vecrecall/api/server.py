"""
VecRecall — HTTP REST API Server

为传统软件（非 MCP 客户端）提供 HTTP JSON 接口，零依赖，
基于 Python 标准库 http.server。任何语言（curl / requests /
Java / C# / Node / Go ...）都能通过 HTTP 接入。

启动：
  vr api --dir ~/.vecrecall --port 8791 --embedder ollama
  vr-api --dir ~/.vecrecall --port 8791

端点一览：
  GET    /health                   健康检查
  GET    /stats                    统计
  GET    /wings                    列出 wings
  GET    /topics?wing=             列出话题
  GET    /memories                 记忆列表（?wing=&topic=&limit=&offset=&full=&sort=）
  GET    /memories/{id}            单条记忆（含原文）
  POST   /memories                 添加记忆
  POST   /memories/batch           批量添加
  POST   /search                   语义搜索
  POST   /context                  构建四层上下文
  POST   /prune                    清理低价值记忆
  POST   /reindex                  重建向量索引
  POST   /verify                   验证哈希链
  PUT    /memories/{id}            更新记忆
  DELETE /memories/{id}            删除记忆
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 把父目录加入 path（支持直接 python -m 运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from vecrecall.core.engine import (
    VecRecall, build_embedding_backend, memory_to_dict,
)

__version__ = "2.0.0"


def _err(msg: str) -> dict:
    return {"error": msg}


def resolve_api_key(cli_key: str | None, host: str) -> str | None:
    """解析 REST API 访问令牌。

    优先级：--api-key > 环境变量 VR_API_KEY > None（本机免认证）。
    监听非本机地址（如 0.0.0.0）时若未提供 token，直接拒绝启动。
    """
    key = cli_key or os.environ.get("VR_API_KEY")
    if not key and host not in ("127.0.0.1", "localhost", "::1"):
        print("[VecRecall API] 错误：监听非本机地址必须提供 --api-key 或 VR_API_KEY",
              file=sys.stderr)
        sys.exit(1)
    return key


class VecRecallHTTPHandler(BaseHTTPRequestHandler):
    """HTTP → VecRecall 引擎的桥接，全部返回 JSON。"""

    @property
    def palace(self) -> VecRecall:
        return self.server.palace

    @property
    def lock(self):
        return self.server.lock

    # ── 响应辅助 ──
    @property
    def api_key(self) -> str | None:
        return getattr(self.server, "api_key", None)

    @property
    def cors_origin(self) -> str | None:
        return getattr(self.server, "cors_origin", None)

    def _authorized(self) -> bool:
        """校验 Bearer Token（Authorization 头或 ?key= 查询参数）。"""
        key = self.api_key
        if not key:
            return True  # 未配置 token 时不强制（仅建议本机使用）
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], key):
            return True
        qkey = parse_qs(urlparse(self.path).query).get("key", [None])[0]
        return bool(qkey) and secrets.compare_digest(qkey, key)

    def _is_public(self) -> bool:
        """无需认证的路径（健康检查）。"""
        return self.path.rstrip("/") in ("/health", "")

    def _send(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.cors_origin:
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _handle(self, fn):
        """统一认证校验 + 异常处理 + 串行化（保护 SQLite 并发写）。"""
        with self.lock:
            try:
                if not self._is_public() and not self._authorized():
                    self._send(_err("未授权：缺少或错误的 API Key"), 401)
                    return
                self._send(fn())
            except ValueError as e:
                self._send(_err(str(e)), 400)
            except KeyError as e:
                self._send(_err(f"缺少参数: {e}"), 400)
            except Exception as e:  # noqa: BLE001
                self._send(_err(f"服务器内部错误: {e}"), 500)

    # ── HTTP 方法 ──
    def do_OPTIONS(self):
        self.send_response(204)
        if self.cors_origin:
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        self._handle(self._route_get)

    def do_POST(self):
        self._handle(self._route_post)

    def do_PUT(self):
        self._handle(self._route_put)

    def do_DELETE(self):
        self._handle(self._route_delete)

    # ── 路由辅助 ──
    def _parts(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        segs = [s for s in path.split("/") if s]
        return segs, query

    # ── GET ──
    def _route_get(self):
        p = self.palace
        segs, q = self._parts()

        if not segs:
            return {"name": "vecrecall", "version": __version__, "status": "ok",
                    "embedding": type(p._emb).__name__,
                    "vector": type(p._vec).__name__}
        head = segs[0]

        if head == "health":
            return {"status": "ok", "name": "vecrecall", "version": __version__}
        if head == "stats":
            return p.stats()
        if head == "wings":
            return {"wings": p.list_wings()}
        if head == "topics":
            return {"topics": p.list_topics(q.get("wing", [None])[0])}
        if head == "memories":
            if len(segs) == 1:
                return p.list_memories(
                    wing=q.get("wing", [None])[0],
                    topic=q.get("topic", [None])[0],
                    limit=int(q.get("limit", ["50"])[0]),
                    offset=int(q.get("offset", ["0"])[0]),
                    full=q.get("full", ["0"])[0].lower() in ("1", "true", "yes"),
                    sort=q.get("sort", ["importance"])[0],
                )
            m = p._kg.get(segs[1])
            if not m:
                raise ValueError(f"记忆不存在: {segs[1]}")
            return memory_to_dict(m, full=True)
        raise ValueError(f"未知路径: /{'/'.join(segs)}")

    # ── POST ──
    def _route_post(self):
        p = self.palace
        segs, q = self._parts()
        body = self._read_json()

        if not segs:
            raise ValueError("未知路径")
        head = segs[0]

        if head == "memories":
            if len(segs) == 2 and segs[1] == "batch":
                ms = p.add_batch(body.get("items", []))
                return {"added": len(ms), "ids": [m.id for m in ms]}
            m = p.add(
                content=body["content"],
                topic=body.get("topic", "general"),
                wing=body.get("wing"),
                importance=body.get("importance"),
                session_id=body.get("session_id", ""),
                ui_summary=body.get("ui_summary", ""),
                metadata=body.get("metadata"),
            )
            return {"ok": True, "id": m.id, "wing": m.wing,
                    "topic": m.topic, "importance": m.importance}

        if head == "search":
            results = p.search(
                body["query"], body.get("n", 10),
                body.get("min_score", 0.0), body.get("hybrid", False),
            )
            return {"results": [
                {**memory_to_dict(r.memory),
                 "score": round(r.score, 4), "layer": r.layer}
                for r in results
            ]}

        if head == "context":
            ctx = p.build_context(
                body.get("current_query", ""),
                body.get("load_l2", True),
                body.get("load_l3", False),
                body.get("hybrid", False),
            )
            return {
                "l0": ctx.l0_identity,
                "l1": [memory_to_dict(r.memory) for r in ctx.l1_key_moments],
                "l2": [memory_to_dict(r.memory) for r in ctx.l2_topic_context],
                "l3": [memory_to_dict(r.memory) for r in ctx.l3_deep_results],
                "total_tokens_estimate": ctx.total_tokens_estimate,
            }

        if head == "prune":
            victims = p.prune(
                min_importance=body.get("min_importance"),
                older_than_days=body.get("older_than_days"),
                wing=body.get("wing"),
                dry_run=body.get("dry_run", True),
            )
            return {"candidates": len(victims),
                    "dry_run": body.get("dry_run", True),
                    "ids": [m.id for m in victims[:200]]}

        if head == "reindex":
            return {"reindexed": p.reindex()}

        if head == "verify":
            return p.verify_integrity(body.get("wing"))

        raise ValueError(f"未知路径: /{'/'.join(segs)}")

    # ── PUT ──
    def _route_put(self):
        p = self.palace
        segs, q = self._parts()
        body = self._read_json()
        if len(segs) == 2 and segs[0] == "memories":
            m = p.update(
                segs[1],
                content=body.get("content"),
                topic=body.get("topic"),
                importance=body.get("importance"),
                ui_summary=body.get("ui_summary"),
            )
            return {"ok": True, "id": m.id, "importance": m.importance}
        raise ValueError(f"未知路径: /{'/'.join(segs)}")

    # ── DELETE ──
    def _route_delete(self):
        p = self.palace
        segs, q = self._parts()
        if len(segs) == 2 and segs[0] == "memories":
            ok = p.delete(segs[1])
            return {"ok": ok, "deleted": ok}
        raise ValueError(f"未知路径: /{'/'.join(segs)}")


class VecRecallHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, palace: VecRecall,
                 api_key: str | None = None,
                 cors_origin: str | None = None):
        super().__init__(addr, VecRecallHTTPHandler)
        self.palace = palace
        self.lock = threading.RLock()
        self.api_key = api_key
        self.cors_origin = cors_origin


def main():
    parser = argparse.ArgumentParser(description="VecRecall HTTP REST API Server")
    parser.add_argument("--dir", default=os.path.expanduser("~/.vecrecall"),
                        help="数据存储目录")
    parser.add_argument("--wing", default="default", help="默认 wing")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8791, help="监听端口")
    parser.add_argument("--chain", action="store_true", help="启用哈希链")
    parser.add_argument("--embedder", default="auto",
                        choices=["auto", "bow", "sentence", "ollama", "api"],
                        help="嵌入后端（默认 auto）")
    parser.add_argument("--embed-model", default=None, help="嵌入模型名")
    parser.add_argument("--embed-url", default=None, help="Ollama/API 服务地址")
    parser.add_argument("--embed-api-key", default=None, help="远程 API Key")
    parser.add_argument("--api-key", default=None,
                        help="REST API 访问令牌（缺省读环境变量 VR_API_KEY）")
    parser.add_argument("--cors-origin", default=None,
                        help="允许跨域的 Origin（默认禁止跨域，防止浏览器网页盗读）")
    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key, args.host)

    emb = build_embedding_backend(
        args.embedder, model=args.embed_model,
        base_url=args.embed_url, api_key=args.embed_api_key,
    )
    palace = VecRecall(
        base_dir=args.dir, wing=args.wing,
        use_blockchain=args.chain, embedding_backend=emb,
    )

    server = VecRecallHTTPServer((args.host, args.port), palace,
                                 api_key=api_key, cors_origin=args.cors_origin)
    auth_note = ("已启用 API Key 认证" if api_key
                 else "无认证（仅本机，建议生产环境提供 --api-key）")
    cors_note = ("跨域受限" if not args.cors_origin
                 else f"允许跨域 Origin={args.cors_origin}")
    print(f"[VecRecall API] 监听 http://{args.host}:{args.port}  "
          f"embedder={type(emb).__name__}  wing={args.wing}",
          file=sys.stderr)
    print(f"[VecRecall API] 安全: {auth_note}  |  {cors_note}",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        palace.close()


if __name__ == "__main__":
    main()
