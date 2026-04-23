"""
VecRecall — MCP Server

兼容 Claude Code / Gemini CLI / 任意 MCP 客户端。
所有工具的检索路径都走纯向量语义，不经过结构过滤。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

# 把父目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from vecrecall.core.engine import VecRecall, NumpyVectorBackend, HashEmbeddingBackend

# ─────────────────────────────────────────────
# MCP 协议基础（stdio JSON-RPC）
# ─────────────────────────────────────────────

def _send(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def _ok(req_id, result: Any):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})

def _err(req_id, code: int, msg: str):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}})


# ─────────────────────────────────────────────
# 工具定义
# ─────────────────────────────────────────────

TOOLS = [
    # ── 写入类 ──
    {
        "name": "mp_add",
        "description": "添加一条记忆（逐字存储原文，向量化索引）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要存储的原始内容"},
                "topic": {"type": "string", "description": "话题标签（仅用于 UI 组织，不影响检索）", "default": "general"},
                "wing": {"type": "string", "description": "项目/人物标识（仅用于 UI 组织）"},
                "importance": {"type": "number", "description": "重要性 0-1，留空则自动评分"},
                "session_id": {"type": "string", "description": "当前会话 ID"},
                "ui_summary": {"type": "string", "description": "AAAK 压缩摘要（仅供 UI 展示，不参与检索）"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "mp_add_batch",
        "description": "批量添加记忆",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "topic": {"type": "string"},
                            "wing": {"type": "string"},
                            "importance": {"type": "number"},
                            "ui_summary": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "mp_update_importance",
        "description": "更新一条记忆的重要性评分",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "importance": {"type": "number"},
            },
            "required": ["memory_id", "importance"],
        },
    },

    # ── 检索类（核心：全部走纯向量，无结构过滤）──
    {
        "name": "mp_search",
        "description": "语义搜索（纯向量，无结构过滤，高召回率）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "n": {"type": "integer", "default": 10},
                "min_score": {"type": "number", "default": 0.0},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mp_build_context",
        "description": "构建四层记忆上下文（L0+L1+L2+L3），直接注入 AI prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "current_query": {"type": "string", "description": "当前对话内容，用于触发 L2/L3"},
                "load_l2": {"type": "boolean", "default": True},
                "load_l3": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "mp_l1_moments",
        "description": "获取 L1 关键时刻（按 importance 排序的 top-15 记忆）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "default": 15},
            },
        },
    },
    {
        "name": "mp_l2_context",
        "description": "语义触发 L2 话题上下文（相似度 ≥ 阈值才返回）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "threshold": {"type": "number", "default": 0.55},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mp_l3_deep",
        "description": "L3 全量深度语义检索（最高召回，按需触发）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "n": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mp_fuzzy_recall",
        "description": "模糊回忆：'我们之前聊过类似的东西吧？'——低阈值宽松语义匹配",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hint": {"type": "string", "description": "模糊印象描述"},
                "n": {"type": "integer", "default": 5},
            },
            "required": ["hint"],
        },
    },

    # ── 组织层（仅 UI/浏览用，不参与检索）──
    {
        "name": "mp_list_wings",
        "description": "列出所有 wing（项目/人物）——仅 UI 组织用",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mp_list_topics",
        "description": "列出所有话题标签——仅 UI 组织用",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "可选，筛选某个 wing 下的话题"},
            },
        },
    },
    {
        "name": "mp_browse_wing",
        "description": "浏览某个 wing 下的记忆（UI 展示，不走向量检索）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["wing"],
        },
    },
    {
        "name": "mp_browse_topic",
        "description": "浏览某个话题下的记忆（UI 展示）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "wing": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["topic"],
        },
    },

    # ── 知识图谱类 ──
    {
        "name": "mp_link",
        "description": "建立两条记忆之间的跨 wing 关联",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "target_id": {"type": "string"},
                "link_type": {"type": "string", "default": "related"},
                "weight": {"type": "number", "default": 1.0},
            },
            "required": ["source_id", "target_id"],
        },
    },
    {
        "name": "mp_get_memory",
        "description": "按 ID 获取单条记忆（含原文和 ui_summary）",
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    },

    # ── Agent 日记类 ──
    {
        "name": "mp_diary_write",
        "description": "Agent 日记写入（每个 Agent 有独立 wing，互不干扰）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "description": "Agent 名称，如 reviewer / architect"},
                "content": {"type": "string"},
                "topic": {"type": "string", "default": "diary"},
            },
            "required": ["agent_name", "content"],
        },
    },
    {
        "name": "mp_diary_read",
        "description": "读取某个 Agent 的日记（语义检索）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "query": {"type": "string", "description": "留空则返回最新条目"},
                "n": {"type": "integer", "default": 10},
            },
            "required": ["agent_name"],
        },
    },

    # ── 会话存档类 ──
    {
        "name": "mp_archive_session",
        "description": "自动存档整段对话（Hooks 后台调用）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
                "session_id": {"type": "string"},
                "wing": {"type": "string"},
                "topic": {"type": "string", "default": "session"},
            },
            "required": ["messages"],
        },
    },

    # ── 统计 & 管理类 ──
    {
        "name": "mp_stats",
        "description": "系统状态：记忆总数、wing 数、跨关联数",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mp_health",
        "description": "健康检查：验证向量库和 SQLite 连接",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mp_export_wing",
        "description": "导出某个 wing 的全部记忆为 JSON",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["wing"],
        },
    },
    {
        "name": "mp_import_json",
        "description": "从 JSON 文件导入记忆",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mp_set_identity",
        "description": "更新 L0 身份层内容",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string"},
            },
            "required": ["identity"],
        },
    },
    {
        "name": "mp_set_wing",
        "description": "切换当前默认 wing",
        "inputSchema": {
            "type": "object",
            "properties": {"wing": {"type": "string"}},
            "required": ["wing"],
        },
    },
    {
        "name": "mp_set_l2_threshold",
        "description": "调整 L2 语义触发阈值（默认 0.55）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "description": "0.0-1.0，越高越严格"},
            },
            "required": ["threshold"],
        },
    },
    {
        "name": "mp_format_prompt",
        "description": "将 build_context 结果格式化为可直接注入的 prompt 字符串",
        "inputSchema": {
            "type": "object",
            "properties": {
                "current_query": {"type": "string"},
                "load_l2": {"type": "boolean", "default": True},
            },
        },
    },
]


# ─────────────────────────────────────────────
# 服务器主体
# ─────────────────────────────────────────────

class MCPServer:

    def __init__(self, base_dir: str, wing: str = "default"):
        self._palace = VecRecall(
            base_dir=base_dir,
            wing=wing,
            identity_prompt=f"[VecRecall] Wing: {wing}",
        )
        self._wing = wing

    def handle(self, req: dict) -> None:
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "initialize":
            _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vecrecall", "version": "2.0.0"},
            })
        elif method == "tools/list":
            _ok(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            self._dispatch(req_id, params.get("name", ""), params.get("arguments", {}))
        elif method == "notifications/initialized":
            pass  # 无需响应
        else:
            _err(req_id, -32601, f"未知方法: {method}")

    def _dispatch(self, req_id, name: str, args: dict) -> None:
        p = self._palace
        try:
            result = self._call(name, args)
            _ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            })
        except Exception as e:
            _err(req_id, -32000, str(e))

    def _call(self, name: str, args: dict) -> Any:
        p = self._palace

        if name == "mp_add":
            m = p.add(**{k: v for k, v in args.items()})
            return {"id": m.id, "wing": m.wing, "topic": m.topic,
                    "importance": m.importance, "ok": True}

        elif name == "mp_add_batch":
            ms = p.add_batch(args["items"])
            return {"added": len(ms), "ids": [m.id for m in ms]}

        elif name == "mp_update_importance":
            # 更新重要性：重新 save 到 SQLite
            m = p._kg.get(args["memory_id"])
            if not m:
                raise ValueError(f"记忆不存在: {args['memory_id']}")
            m.importance = args["importance"]
            p._kg.save(m)
            return {"ok": True}

        elif name == "mp_search":
            results = p.search(args["query"], args.get("n", 10),
                               args.get("min_score", 0.0))
            return _format_results(results)

        elif name == "mp_build_context":
            ctx = p.build_context(
                args.get("current_query", ""),
                args.get("load_l2", True),
                args.get("load_l3", False),
            )
            return {
                "l0": ctx.l0_identity,
                "l1": _format_results(ctx.l1_key_moments),
                "l2": _format_results(ctx.l2_topic_context),
                "l3": _format_results(ctx.l3_deep_results),
                "total_tokens_estimate": ctx.total_tokens_estimate,
            }

        elif name == "mp_l1_moments":
            top = p._kg.top_by_importance(None, args.get("n", 15))
            return {"moments": [_mem_dict(m) for m in top]}

        elif name == "mp_l2_context":
            old_threshold = p.L2_TRIGGER_THRESHOLD
            if "threshold" in args:
                p.L2_TRIGGER_THRESHOLD = args["threshold"]
            results = p._semantic_l2(args["query"])
            p.L2_TRIGGER_THRESHOLD = old_threshold
            return _format_results(results)

        elif name == "mp_l3_deep":
            results = p._deep_search(args["query"], args.get("n", 20))
            return _format_results(results)

        elif name == "mp_fuzzy_recall":
            # 低阈值宽松匹配
            vec = p._emb.embed(args["hint"])
            raw = p._vec.query(vec, n=args.get("n", 5) * 3, min_score=0.1)
            ids = [r[0] for r in raw]
            scores = {r[0]: r[1] for r in raw}
            mem_map = p._kg.get_many(ids)
            results = [
                {"id": mid, "score": round(scores[mid], 4),
                 "content": mem_map[mid].content[:200],
                 "wing": mem_map[mid].wing, "topic": mem_map[mid].topic}
                for mid in ids if mid in mem_map
            ]
            return {"results": results[:args.get("n", 5)]}

        elif name == "mp_list_wings":
            return {"wings": p.list_wings()}

        elif name == "mp_list_topics":
            return {"topics": p.list_topics(args.get("wing"))}

        elif name == "mp_browse_wing":
            mems = p._kg.top_by_importance(args["wing"], args.get("limit", 20))
            return {"wing": args["wing"], "memories": [_mem_dict(m) for m in mems]}

        elif name == "mp_browse_topic":
            conn = p._kg._conn
            q = "SELECT * FROM memories WHERE topic=?"
            params = [args["topic"]]
            if "wing" in args:
                q += " AND wing=?"
                params.append(args["wing"])
            q += f" ORDER BY importance DESC LIMIT {args.get('limit', 20)}"
            rows = conn.execute(q, params).fetchall()
            mems = [p._kg._row_to_memory(r) for r in rows]
            return {"memories": [_mem_dict(m) for m in mems]}

        elif name == "mp_link":
            p.link(args["source_id"], args["target_id"],
                   args.get("link_type", "related"), args.get("weight", 1.0))
            return {"ok": True}

        elif name == "mp_get_memory":
            m = p._kg.get(args["memory_id"])
            if not m:
                raise ValueError("记忆不存在")
            return _mem_dict(m, full=True)

        elif name == "mp_diary_write":
            agent_wing = f"agent:{args['agent_name']}"
            m = p.add(content=args["content"], topic=args.get("topic", "diary"),
                      wing=agent_wing)
            return {"id": m.id, "agent": args["agent_name"]}

        elif name == "mp_diary_read":
            agent_wing = f"agent:{args['agent_name']}"
            query = args.get("query", "")
            if query:
                results = p.search(query, args.get("n", 10))
                # 过滤到 agent wing（组织层过滤，不影响向量搜索本身）
                results = [r for r in results if r.memory.wing == agent_wing]
            else:
                mems = p._kg.top_by_importance(agent_wing, args.get("n", 10))
                results = [type("R", (), {"memory": m, "score": m.importance, "layer": "L1"})()
                           for m in mems]
            return {"agent": args["agent_name"],
                    "entries": [{"content": r.memory.content,
                                 "topic": r.memory.topic,
                                 "score": getattr(r, "score", 0)} for r in results]}

        elif name == "mp_archive_session":
            messages = args["messages"]
            sid = args.get("session_id", str(int(time.time())))
            combined = "\n".join(
                f"[{m['role']}] {m['content']}" for m in messages
            )
            mem = p.add(content=combined, topic=args.get("topic", "session"),
                        wing=args.get("wing", self._wing), session_id=sid)
            return {"archived_id": mem.id, "chars": len(combined)}

        elif name == "mp_stats":
            return p.stats()

        elif name == "mp_health":
            stats = p.stats()
            return {"status": "ok", "stats": stats, "backends": {
                "vector": type(p._vec).__name__,
                "embedding": type(p._emb).__name__,
            }}

        elif name == "mp_export_wing":
            mems = p._kg.top_by_importance(args["wing"], 99999)
            data = [_mem_dict(m, full=True) for m in mems]
            out = args.get("output_path", f"{args['wing']}_export.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"exported": len(data), "path": out}

        elif name == "mp_import_json":
            with open(args["path"], encoding="utf-8") as f:
                data = json.load(f)
            items = [{"content": d["content"], "topic": d.get("topic", "general"),
                      "wing": d.get("wing"), "importance": d.get("importance"),
                      "ui_summary": d.get("ui_summary", "")} for d in data]
            ms = p.add_batch(items)
            return {"imported": len(ms)}

        elif name == "mp_set_identity":
            p.identity_prompt = args["identity"]
            return {"ok": True}

        elif name == "mp_set_wing":
            p.wing = args["wing"]
            self._wing = args["wing"]
            return {"ok": True, "wing": args["wing"]}

        elif name == "mp_set_l2_threshold":
            p.L2_TRIGGER_THRESHOLD = args["threshold"]
            return {"ok": True, "threshold": args["threshold"]}

        elif name == "mp_format_prompt":
            ctx = p.build_context(
                args.get("current_query", ""),
                args.get("load_l2", True),
                False,
            )
            lines = [f"## 身份\n{ctx.l0_identity}\n"]
            if ctx.l1_key_moments:
                lines.append("## 关键记忆")
                for r in ctx.l1_key_moments:
                    lines.append(f"- [{r.memory.topic}] {r.memory.content[:150]}")
            if ctx.l2_topic_context:
                lines.append("\n## 相关上下文")
                for r in ctx.l2_topic_context:
                    lines.append(f"- [相似度 {r.score:.2f}] {r.memory.content[:150]}")
            lines.append(f"\n<!-- 估计 token 用量: {ctx.total_tokens_estimate} -->")
            return {"prompt": "\n".join(lines)}

        else:
            raise ValueError(f"未知工具: {name}")

    def run(self):
        """标准 stdio 模式"""
        import sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                self.handle(req)
            except json.JSONDecodeError as e:
                _err(None, -32700, f"JSON 解析失败: {e}")
            except Exception as e:
                _err(None, -32000, str(e))


# ─────────────────────────────────────────────
# 格式化辅助
# ─────────────────────────────────────────────

def _mem_dict(m, full: bool = False) -> dict:
    d = {
        "id": m.id,
        "wing": m.wing,
        "topic": m.topic,
        "importance": round(m.importance, 3),
        "timestamp": m.timestamp,
        "ui_summary": m.ui_summary,
        "content_preview": m.content[:200],
    }
    if full:
        d["content"] = m.content
        d["session_id"] = m.session_id
        d["metadata"] = m.metadata
    return d

def _format_results(results) -> list[dict]:
    return [
        {
            "id": r.memory.id,
            "score": round(r.score, 4),
            "layer": r.layer,
            "wing": r.memory.wing,
            "topic": r.memory.topic,
            "importance": round(r.memory.importance, 3),
            "ui_summary": r.memory.ui_summary,
            "content_preview": r.memory.content[:300],
        }
        for r in results
    ]


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="VecRecall MCP Server")
    parser.add_argument("--dir", default=os.path.expanduser("~/.vecrecall"),
                        help="数据存储目录")
    parser.add_argument("--wing", default="default", help="默认 wing")
    args = parser.parse_args()

    server = MCPServer(base_dir=args.dir, wing=args.wing)
    server.run()


if __name__ == "__main__":
    main()
