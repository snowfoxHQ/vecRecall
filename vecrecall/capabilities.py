"""MCP / REST / CLI 能力对照表（v2.6.1 起作为唯一来源）。

每一项能力在三个入口里叫什么；None 表示这个入口刻意不提供，并在 reason 里写明原因。
tests/test_capabilities.py 会解析三个入口的实际注册项，确保：
  1. 每个入口实际注册的工具 / 路由 / 子命令都出现在这张表里（新增入口必须登记）；
  2. 表里写了的入口确实存在（没有写了却没实现的）。
新增能力时先改这张表，再实现，测试会提醒漏掉的入口。
"""

from __future__ import annotations

# 每项：capability -> {"mcp": 工具名, "rest": "方法 路径", "cli": 子命令, "reason": 缺席原因}
CAPABILITIES: dict[str, dict] = {
    # ── 写入 ──
    "add":            {"mcp": "mp_add", "rest": "POST /memories", "cli": "add"},
    "add_batch":      {"mcp": "mp_add_batch", "rest": "POST /memories/batch", "cli": None,
                       "reason": "CLI 用 import 从文件批量导入"},
    "add_file":       {"mcp": None, "rest": None, "cli": "add-file",
                       "reason": "读本机文件，只在 CLI 有意义"},
    "update":         {"mcp": "mp_update", "rest": "PUT /memories/{id}", "cli": "update"},
    "update_importance": {"mcp": "mp_update_importance", "rest": None, "cli": None,
                          "reason": "update 已覆盖；保留给旧 MCP 客户端"},
    "delete":         {"mcp": "mp_delete", "rest": "DELETE /memories/{id}", "cli": "delete"},
    "prune":          {"mcp": "mp_prune", "rest": "POST /prune", "cli": "prune"},
    "archive_session": {"mcp": "mp_archive_session", "rest": "POST /archive", "cli": "archive"},
    "diary_write":    {"mcp": "mp_diary_write", "rest": None, "cli": "diary",
                       "reason": "REST 用 POST /memories + wing=agent:<名字>"},
    "diary_read":     {"mcp": "mp_diary_read", "rest": None, "cli": "diary",
                       "reason": "REST 用 POST /search 后按 wing 过滤"},
    "import":         {"mcp": "mp_import_json", "rest": None, "cli": "import",
                       "reason": "REST 用 POST /memories/batch 直接传数据，不读服务端文件"},
    "export":         {"mcp": "mp_export_wing", "rest": "GET /export", "cli": "export"},

    # ── 检索 ──
    "search":         {"mcp": "mp_search", "rest": "POST /search", "cli": "search"},
    "context":        {"mcp": "mp_build_context", "rest": "POST /context", "cli": "context"},
    "l1":             {"mcp": "mp_l1_moments", "rest": None, "cli": None,
                       "reason": "context / search --layer l1 已覆盖"},
    "l2":             {"mcp": "mp_l2_context", "rest": None, "cli": None,
                       "reason": "context / search --layer l2 已覆盖"},
    "l3":             {"mcp": "mp_l3_deep", "rest": None, "cli": None,
                       "reason": "search 默认就是 L3"},
    "fuzzy_recall":   {"mcp": "mp_fuzzy_recall", "rest": None, "cli": None,
                       "reason": "等同 search(min_score=低阈值)，REST / CLI 传参即可"},
    "format_prompt":  {"mcp": "mp_format_prompt", "rest": None, "cli": None,
                       "reason": "给模型直接注入用，只在 MCP 有意义"},
    "graph_search":   {"mcp": "mp_graph_search", "rest": "POST /graph-search", "cli": "graph-search"},
    "recall_policy":  {"mcp": "mp_recall_policy", "rest": None, "cli": "recall-policy",
                       "reason": "Agent 侧决策工具，REST 调用方自行判断"},
    "accept_memory":  {"mcp": "mp_accept_memory", "rest": None, "cli": None,
                       "reason": "POST /search 传 acceptance_query 已覆盖"},

    # ── 浏览 ──
    "get":            {"mcp": "mp_get_memory", "rest": "GET /memories/{id}", "cli": "get"},
    "list":           {"mcp": "mp_list_memories", "rest": "GET /memories", "cli": "list"},
    "wings":          {"mcp": "mp_list_wings", "rest": "GET /wings", "cli": "wings"},
    "topics":         {"mcp": "mp_list_topics", "rest": "GET /topics", "cli": "topics"},
    "browse_wing":    {"mcp": "mp_browse_wing", "rest": None, "cli": "browse",
                       "reason": "REST 用 GET /memories?wing="},
    "browse_topic":   {"mcp": "mp_browse_topic", "rest": None, "cli": None,
                       "reason": "REST 用 GET /memories?topic=，CLI 用 list --topic"},
    "provenance":     {"mcp": "mp_provenance", "rest": "GET /memories/{id}/provenance", "cli": "get",
                       "note": "vr get 一并显示来源"},

    # ── 图谱 ──
    "entity":         {"mcp": "mp_entity", "rest": "GET /entities/{name}", "cli": "entity"},
    "graph_list":     {"mcp": None, "rest": None, "cli": "graph",
                       "reason": "全量实体列表是浏览用，MCP / REST 用 entity 按名查询"},
    "link":           {"mcp": "mp_link", "rest": None, "cli": None,
                       "reason": "跨 wing 关联是 Agent 编排操作，暂只给 MCP"},

    # ── 记忆质量 ──
    "contradictions": {"mcp": "mp_contradictions", "rest": "GET /contradictions", "cli": "contradictions"},
    "supersede":      {"mcp": "mp_supersede", "rest": "POST /supersede", "cli": "supersede"},
    "decay":          {"mcp": None, "rest": None, "cli": "decay",
                       "reason": "运维评估工具，只在 CLI"},
    "distill":        {"mcp": "mp_distill", "rest": None, "cli": "distill",
                       "reason": "REST 暂不开放会批量改写数据的操作"},

    # ── 运维 ──
    "stats":          {"mcp": "mp_stats", "rest": "GET /stats", "cli": "stats"},
    "health":         {"mcp": "mp_health", "rest": "GET /health", "cli": None,
                       "reason": "CLI 用 stats"},
    "check":          {"mcp": "mp_check", "rest": "POST /check", "cli": "check"},
    "verify":         {"mcp": "mp_verify", "rest": "POST /verify", "cli": "verify"},
    "reindex":        {"mcp": "mp_reindex", "rest": "POST /reindex", "cli": "reindex"},
    "encryption":     {"mcp": None, "rest": None, "cli": "encryption",
                       "reason": "密钥操作只在本机 CLI，不经网络"},
    "writeback":      {"mcp": None, "rest": None, "cli": "writeback",
                       "reason": "本机队列运维"},
    "repo_map":       {"mcp": None, "rest": None, "cli": "repo-map",
                       "reason": "读写本地仓库目录"},

    # ── 会话状态 / 服务启动（不是记忆能力） ──
    "set_identity":   {"mcp": "mp_set_identity", "rest": None, "cli": None,
                       "reason": "MCP 会话内状态"},
    "set_wing":       {"mcp": "mp_set_wing", "rest": None, "cli": None,
                       "reason": "MCP 会话内状态；CLI 用 init --wing"},
    "set_l2_threshold": {"mcp": "mp_set_l2_threshold", "rest": None, "cli": None,
                         "reason": "MCP 会话内状态"},
    "init":           {"mcp": None, "rest": None, "cli": "init", "reason": "本机配置"},
    "serve_mcp":      {"mcp": None, "rest": None, "cli": "mcp", "reason": "启动服务"},
    "serve_api":      {"mcp": None, "rest": None, "cli": "api", "reason": "启动服务"},
}


def entries(kind: str) -> set[str]:
    """表里登记的某个入口的全部名字（kind = mcp / rest / cli）。"""
    return {v[kind] for v in CAPABILITIES.values() if v.get(kind)}


def matrix_markdown() -> str:
    """生成 docs 用的 Markdown 对照表。"""
    rows = ["| 能力 | MCP | REST | CLI |", "|---|---|---|---|"]
    for cap, v in CAPABILITIES.items():
        def cell(k):
            return f"`{v[k]}`" if v.get(k) else "—"
        rows.append(f"| {cap} | {cell('mcp')} | {cell('rest')} | {cell('cli')} |")
    return "\n".join(rows)
