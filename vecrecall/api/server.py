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

__version__ = "2.6.1"


def _err(msg: str) -> dict:
    return {"error": msg}


# 请求体上限（字节）。可用环境变量 VR_API_MAX_BODY 调整
MAX_BODY = int(os.environ.get("VR_API_MAX_BODY", str(8 * 1024 * 1024)))


class _BadRequest(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


# 单次请求能取的条数上限，防止一个请求把整库读出来
MAX_RESULTS = 200
MAX_LIST = 1000
MAX_CONTENT = 1_000_000   # 单条记忆原文上限（字符）


def _int_param(v, name: str, default: int, lo: int, hi: int) -> int:
    if v is None or v == "":
        return default
    try:
        x = int(v)
    except (TypeError, ValueError):
        raise _BadRequest(f"参数 {name} 必须是整数，收到 {v!r}")
    if not lo <= x <= hi:
        raise _BadRequest(f"参数 {name} 超出范围 [{lo}, {hi}]：{x}")
    return x


def _float_param(v, name: str, lo: float, hi: float):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise _BadRequest(f"参数 {name} 必须是数字，收到 {v!r}")
    if not lo <= x <= hi:
        raise _BadRequest(f"参数 {name} 超出范围 [{lo}, {hi}]：{x}")
    return x


def _content_param(body: dict) -> str:
    c = body.get("content")
    if not isinstance(c, str) or not c.strip():
        raise _BadRequest("content 必须是非空字符串")
    if len(c) > MAX_CONTENT:
        raise _BadRequest(f"content 过长：{len(c)} 字符，上限 {MAX_CONTENT}", 413)
    return c


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
        """校验 Authorization: Bearer <key>。

        v2.6 起不再接受 URL 里的 ?key=：查询参数会进入代理 / 服务器访问日志和浏览器历史。
        """
        key = self.api_key
        if not key:
            return True  # 未配置 token 时不强制（仅建议本机使用）
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], key)

    def _key_in_url(self) -> bool:
        return "key" in parse_qs(urlparse(self.path).query)

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
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise _BadRequest("Content-Length 不合法")
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise _BadRequest(f"请求体过大：{length} 字节，上限 {MAX_BODY}", 413)
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise _BadRequest(f"请求体不是合法的 JSON：{e}")
        if not isinstance(data, dict):
            raise _BadRequest("请求体必须是 JSON 对象")
        return data

    def _handle(self, fn):
        """统一认证校验 + 异常处理 + 串行化（保护 SQLite 并发写）。"""
        with self.lock:
            try:
                if not self._is_public() and self.api_key and self._key_in_url():
                    self._send(_err("不接受 URL 里的 ?key=（会进入访问日志）；"
                                    "请用请求头 Authorization: Bearer <key>"), 401)
                    return
                if not self._is_public() and not self._authorized():
                    self._send(_err("未授权：缺少或错误的 API Key"), 401)
                    return
                self._send(fn())
            except _BadRequest as e:
                self._send(_err(str(e)), e.status)
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
        if head == "contradictions":
            rows = p.contradictions(wing=q.get("wing", [None])[0])
            limit = _int_param(q.get("limit", [None])[0], "limit", 20, 1, MAX_LIST)
            return {"total": len(rows), "items": rows[:limit]}
        if head == "entities" and len(segs) == 2:
            e = p.entity(segs[1])
            if not e:
                raise ValueError(f"未找到实体: {segs[1]}")
            return e
        if head == "export":
            wing = q.get("wing", [None])[0]
            if not wing:
                raise _BadRequest("缺少参数 wing")
            mems = p._kg.top_by_importance(wing, 10**9)
            if p.owner:
                mems = [m for m in mems if p._visible_to(m, p.owner)]
            return {"wing": wing, "count": len(mems),
                    "memories": [memory_to_dict(m, full=True) for m in mems]}
        if head == "memories":
            if len(segs) == 1:
                return p.list_memories(
                    wing=q.get("wing", [None])[0],
                    topic=q.get("topic", [None])[0],
                    limit=_int_param(q.get("limit", [None])[0], "limit", 50, 1, MAX_LIST),
                    offset=_int_param(q.get("offset", [None])[0], "offset", 0, 0, 10**9),
                    full=q.get("full", ["0"])[0].lower() in ("1", "true", "yes"),
                    sort=q.get("sort", ["importance"])[0],
                )
            if len(segs) == 3 and segs[2] == "provenance":
                return p.provenance(segs[1])
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
                items = body.get("items", [])
                if not isinstance(items, list) or len(items) > MAX_LIST:
                    raise _BadRequest(f"items 必须是列表，且不超过 {MAX_LIST} 条")
                for it in items:
                    if not isinstance(it, dict):
                        raise _BadRequest("items 的每一项必须是对象")
                    _content_param(it)
                ms = p.add_batch(items)
                return {"added": len(ms), "ids": [m.id for m in ms]}
            m = p.add(
                content=_content_param(body),
                topic=body.get("topic", "general"),
                wing=body.get("wing"),
                importance=_float_param(body.get("importance"), "importance", 0.0, 1.0),
                session_id=body.get("session_id", ""),
                ui_summary=body.get("ui_summary", ""),
                metadata=body.get("metadata"),
                provenance=body.get("provenance"),
            )
            return {"ok": True, "id": m.id, "wing": m.wing,
                    "topic": m.topic, "importance": m.importance}

        if head == "search":
            results = p.search(
                body["query"], _int_param(body.get("n"), "n", 10, 1, MAX_RESULTS),
                _float_param(body.get("min_score"), "min_score", -1.0, 1.0), body.get("hybrid", False),
                mode=body.get("mode"),
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

        if head == "check":
            return p.check_consistency(repair=bool(body.get("repair", False)))

        if head == "graph-search":
            from vecrecall.core.models import graph_search_to_dict
            if not isinstance(body.get("query"), str) or not body["query"].strip():
                raise _BadRequest("query 必须是非空字符串")
            res = p.graph_search(body["query"],
                                 n=_int_param(body.get("n"), "n", 10, 1, MAX_RESULTS),
                                 hops=_int_param(body.get("hops"), "hops", 2, 1, 4))
            return graph_search_to_dict(res)

        if head == "supersede":
            action = body.get("action", "mark")
            if action == "mark":
                p.supersede(body["old_id"], body["new_id"], reason=body.get("reason", "manual"))
                return {"ok": True, "old_id": body["old_id"], "superseded_by": body["new_id"]}
            if action == "dismiss":
                m = p.unsupersede(body["old_id"])
                return {"ok": True, "old_id": m.id, "dismissed": m.metadata.get("supersede_dismissed")}
            raise _BadRequest(f"非法 action: {action}（mark / dismiss）")

        if head == "archive":
            msgs = body.get("messages")
            if not isinstance(msgs, list) or not msgs or len(msgs) > MAX_LIST:
                raise _BadRequest(f"messages 必须是非空列表，且不超过 {MAX_LIST} 条")
            gran = body.get("granularity", "turn")
            mems = p.archive_session(msgs, session_id=body.get("session_id", ""),
                                     topic=body.get("topic", "session"),
                                     wing=body.get("wing"), granularity=gran)
            return {"granularity": gran, "archived": len(mems), "ids": [m.id for m in mems],
                    "session_id": mems[0].session_id if mems else None}

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
    palace.DEFAULT_VIA = "rest"

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
