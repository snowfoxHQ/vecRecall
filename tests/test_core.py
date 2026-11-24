"""
VecRecall — 测试套件

验证核心设计原则：
  1. 检索路径不经过结构过滤
  2. L2 语义触发（阈值 ≥ 0.55）
  3. AAAK 摘要不参与检索
  4. 四层栈正确构建
"""

import sys
import os
import json
import tempfile
import math
import threading
import time
import urllib.error
import urllib.request

# Windows GBK 控制台修正：强制 UTF-8 输出，避免 ✓/✗ 等字符报错
if sys.stdout.encoding and sys.stdout.encoding.upper() not in ('UTF-8', 'UTF8'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.upper() not in ('UTF-8', 'UTF8'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vecrecall.core.engine import (
    VecRecall, Memory, KnowledgeGraph, NumpyVectorBackend, SqliteVectorBackend,
    HashEmbeddingBackend, BagOfWordsEmbeddingBackend,
    OllamaEmbeddingBackend, APIEmbeddingBackend,
    build_embedding_backend, memory_to_dict,
)
from vecrecall.api.server import VecRecallHTTPServer, resolve_api_key
from vecrecall.mcp.server import MCPServer
from vecrecall.core.extractor import (
    HeuristicExtractor, LLMExtractor, Extraction, build_extractor,
)
from vecrecall.core.crypto import Cipher
from vecrecall.core.forgetting import ForgettingCurve
from vecrecall.core.temporal_graph import TemporalGraph, normalize_entity

# ─────────────────────────────────────────────
# 测试辅助
# ─────────────────────────────────────────────

PASS = 0
FAIL = 0

def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def make_palace(tmp_dir: str, use_blockchain: bool = False) -> VecRecall:
    """创建测试用实例（词法嵌入 + 内存向量，确定性、无外部依赖）"""
    return VecRecall(
        base_dir=tmp_dir,
        wing="test-project",
        identity_prompt="测试身份层",
        embedding_backend=BagOfWordsEmbeddingBackend(),
        vector_backend=NumpyVectorBackend(),
        use_blockchain=use_blockchain,
    )


# ─────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────

def test_basic_add_and_retrieve():
    print("\n── 基础写入 & 检索 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        m = p.add(content="用户偏好深色主题界面", topic="preferences", importance=0.8)
        test("写入返回 Memory 对象", isinstance(m, Memory))
        test("ID 非空", bool(m.id))
        test("importance 正确", m.importance == 0.8)
        test("topic 正确", m.topic == "preferences")

        results = p.search("深色主题", n=5)
        test("搜索返回结果", len(results) > 0)
        test("结果有 score 字段", hasattr(results[0], "score"))
        test("score 在 0-1 之间", 0 <= results[0].score <= 1)

        p.close()


def test_no_structural_filter_in_retrieval():
    """核心测试：检索路径不按 wing/topic 过滤
    注：哈希嵌入无语义，用相同内容跨 wing 写入来验证无结构过滤机制"""
    print("\n── 核心：检索无结构过滤 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        # 三个 wing 写入完全相同的内容（哈希嵌入下向量相同，确保能被检索到）
        # 注：dedup=False 关闭语义去重，本测试只关注检索机制而非去重
        same_content = "数据库连接池配置优化"
        m1 = p.add(content=same_content, wing="project-a", topic="arch", dedup=False)
        m2 = p.add(content=same_content, wing="project-b", topic="deploy", dedup=False)
        m3 = p.add(content=same_content, wing="personal", topic="habit", dedup=False)

        # L3 全量搜索——三条记录都应命中，不应因 wing 不同被过滤
        results = p._deep_search(same_content, 20)
        found_ids = {r.memory.id for r in results}
        found_wings = {r.memory.wing for r in results}

        test("L3 能跨 wing 检索到 project-a", m1.id in found_ids)
        test("L3 能跨 wing 检索到 project-b", m2.id in found_ids)
        test("L3 覆盖所有 wing（≥ 2 个）", len(found_wings) >= 2,
             f"wings found: {found_wings}")

        # 验证向量库中没有按 wing 做过滤（核心机制验证）
        vec = p._emb.embed(same_content)
        raw_results = p._vec.query(vec, n=20, min_score=0.0)
        test("向量库原始查询返回所有 wing 的记录",
             len(raw_results) == 3,
             f"raw count: {len(raw_results)}")

        p.close()


def test_aaak_not_in_retrieval():
    """AAAK 摘要只存 UI 层，不参与向量检索"""
    print("\n── AAAK 不参与检索 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        # 写入时带 AAAK 摘要
        m = p.add(
            content="完整原文内容：讨论了数据库迁移方案，最终选择 PostgreSQL",
            ui_summary="DB迁移→PG",  # AAAK 压缩摘要
            topic="database",
        )

        # 向量库只索引 content，不索引 ui_summary
        # 用 ui_summary 的内容搜索，不应该影响召回率
        r1 = p.search("数据库迁移", n=5)
        r2 = p.search("PostgreSQL", n=5)

        # ui_summary 通过 SQLite 正确保存
        retrieved = p._kg.get(m.id)
        test("ui_summary 正确存入 SQLite", retrieved.ui_summary == "DB迁移→PG")
        test("content 未被 ui_summary 替换", "完整原文内容" in retrieved.content)

        # 确认检索走的是 content，不是 ui_summary
        test("按原文内容可检索到", len(r1) > 0 or len(r2) > 0,
             "内容检索无结果（可能是哈希嵌入不支持语义，正常）")

        p.close()


def test_four_layer_stack():
    """四层记忆栈结构正确"""
    print("\n── 四层记忆栈 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        # 写入足够数量的记忆
        for i in range(20):
            p.add(
                content=f"记忆条目 {i}：{'重要' if i % 3 == 0 else '普通'}内容",
                topic=f"topic-{i % 5}",
                importance=0.9 if i % 3 == 0 else 0.3,
            )

        ctx = p.build_context(current_query="重要记忆", load_l2=True, load_l3=False)

        test("L0 身份层非空", bool(ctx.l0_identity))
        test("L1 关键时刻 ≤ 15 条", len(ctx.l1_key_moments) <= 15)
        test("L1 结果有 score", all(hasattr(r, "score") for r in ctx.l1_key_moments))
        test("L1 结果标记为 L1 层", all(r.layer == "L1" for r in ctx.l1_key_moments))
        test("L2 结果标记为 L2 层", all(r.layer == "L2" for r in ctx.l2_topic_context))
        test("L3 未触发时为空", len(ctx.l3_deep_results) == 0)
        test("token 估算 > 0", ctx.total_tokens_estimate > 0)
        test("token 估算合理（< 5000）", ctx.total_tokens_estimate < 5000)

        # 验证 L1 按 importance 排序
        if len(ctx.l1_key_moments) >= 2:
            scores = [r.score for r in ctx.l1_key_moments]
            test("L1 按 importance 降序", scores == sorted(scores, reverse=True))

        p.close()


def test_l2_semantic_trigger():
    """L2 语义触发：相似度阈值控制"""
    print("\n── L2 语义触发 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p.add(content="认证模块的 JWT 实现细节", topic="auth", importance=0.7)

        # 低阈值应该触发更多结果
        p.L2_TRIGGER_THRESHOLD = 0.0
        low_results = p._semantic_l2("认证")

        # 高阈值应该更严格
        p.L2_TRIGGER_THRESHOLD = 0.99
        high_results = p._semantic_l2("认证")

        test("低阈值返回结果 ≥ 高阈值", len(low_results) >= len(high_results))
        test("L2 结果标记正确", all(r.layer == "L2" for r in low_results))

        # 恢复默认
        p.L2_TRIGGER_THRESHOLD = 0.55
        p.close()


def test_l1_importance_ranking():
    """L1 按 importance 排序，不是按 wing 过滤"""
    print("\n── L1 重要性排序 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        # 在不同 wing 下写入不同重要性的内容
        high = p.add("关键架构决策", wing="project-a", importance=0.95)
        low = p.add("日常闲聊记录", wing="project-b", importance=0.1)
        mid = p.add("普通代码注释", wing="personal", importance=0.5)

        top = p._kg.top_by_importance(wing=None, n=10)
        test("top_by_importance 按 importance 降序", top[0].importance >= top[-1].importance)
        test("高 importance 排在前面", top[0].id == high.id,
             f"top[0].id={top[0].id[:8]}, high.id={high.id[:8]}")
        test("跨 wing 都出现在 L1 候选中",
             len(set(m.wing for m in top)) >= 2)

        p.close()


def test_knowledge_graph():
    """KnowledgeGraph SQLite 正确存取"""
    print("\n── 知识图谱 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        m1 = p.add("微服务架构设计", wing="proj", topic="arch")
        m2 = p.add("部署流水线配置", wing="proj", topic="devops")
        p.link(m1.id, m2.id, link_type="depends_on", weight=0.9)

        stats = p.stats()
        test("记忆总数正确", stats["total_memories"] == 2)
        test("跨关联数正确", stats["cross_links"] == 1)
        test("wing 数正确", stats["wings"] == 1)

        wings = p.list_wings()
        test("list_wings 返回正确", "proj" in wings)

        topics = p.list_topics("proj")
        test("list_topics 返回正确", set(topics) == {"arch", "devops"})

        retrieved = p._kg.get(m1.id)
        test("get() 返回正确记忆", retrieved.content == "微服务架构设计")

        p.close()


def test_batch_add():
    """批量写入"""
    print("\n── 批量写入 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        items = [
            {"content": f"批量记忆 {i}", "topic": "batch", "importance": 0.5}
            for i in range(10)
        ]
        ms = p.add_batch(items)
        test("批量写入数量正确", len(ms) == 10)
        test("所有记忆有 ID", all(m.id for m in ms))
        stats = p.stats()
        test("SQLite 记录数正确", stats["total_memories"] == 10)
        p.close()


def test_diary_isolation():
    """Agent 日记隔离：不同 Agent 的 wing 互不干扰"""
    print("\n── Agent 日记隔离 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        reviewer_wing = "agent:reviewer"
        architect_wing = "agent:architect"

        p.add("发现 SQL 注入漏洞 #bug-123", wing=reviewer_wing, topic="diary")
        p.add("决定采用六边形架构", wing=architect_wing, topic="diary")

        # 按 wing 浏览（组织层功能，不是检索）
        reviewer_mems = p._kg.top_by_importance(reviewer_wing, 10)
        architect_mems = p._kg.top_by_importance(architect_wing, 10)

        test("reviewer 日记只有自己的内容",
             all(m.wing == reviewer_wing for m in reviewer_mems))
        test("architect 日记只有自己的内容",
             all(m.wing == architect_wing for m in architect_mems))
        test("两个 Agent 日记互不干扰",
             not any(m.wing == architect_wing for m in reviewer_mems))

        p.close()


def test_heuristic_importance():
    """启发式重要性评分"""
    print("\n── 启发式重要性 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        short = p.add("ok")
        long_content = "这是一段很长的内容，" * 50
        long = p.add(long_content)
        critical = p.add("critical 架构 architecture 决定 decision deploy")

        test("短内容重要性低", short.importance < long.importance)
        test("关键词提升重要性", critical.importance > short.importance)
        test("重要性在 0-1 范围", all(0 <= m.importance <= 1 for m in [short, long, critical]))

        p.close()


def test_session_archive():
    """会话存档：完整对话合并存储"""
    print("\n── 会话存档 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        messages = [
            {"role": "user", "content": "我们的数据库挂了怎么办？"},
            {"role": "assistant", "content": "先检查连接池，然后重启服务"},
            {"role": "user", "content": "好的，已修复"},
        ]
        combined = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
        m = p.add(content=combined, topic="session", session_id="sess-001")

        test("会话存档成功", bool(m.id))
        test("原文完整保存", m.content == combined)
        test("session_id 正确", m.session_id == "sess-001")

        p.close()


def test_archive_session_granularity():
    """v2.3：会话存档默认每轮一条记忆；session 粒度保留整段存档的旧行为。"""
    print("\n── 会话存档粒度 ──")
    messages = [
        {"role": "user", "content": "我刚领养了一只金毛幼犬，叫 Max"},
        {"role": "assistant", "content": "恭喜！先从笼内训练开始"},
        {"role": "user", "content": ""},                       # 空轮次应跳过
        {"role": "user", "content": "好的，谢谢"},
    ]
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        mems = p.archive_session(messages, session_id="s-turn")
        test("默认 turn：每个非空轮次一条", len(mems) == 3, str(len(mems)))
        test("turn：共用 session_id", all(m.session_id == "s-turn" for m in mems))
        test("turn：记录 turn_index",
             [m.metadata.get("turn_index") for m in mems] == [0, 1, 3])
        test("turn：记录 role",
             [m.metadata.get("role") for m in mems] == ["user", "assistant", "user"])
        test("turn：内容带角色前缀", mems[0].content.startswith("user: "))

        back = p.session_memories("s-turn")
        test("session_memories 按顺序还原", [m.id for m in back] == [m.id for m in mems])

        hit = p.search("金毛幼犬", n=1)
        test("turn：检索命中具体那一轮", hit and hit[0].memory.id == mems[0].id)

        # 不同会话里的同一句话不应被去重合并
        again = p.archive_session([{"role": "user", "content": "好的，谢谢"}],
                                  session_id="s-other")
        test("turn：跨会话相同内容不合并", again[0].id != mems[2].id)

        one = p.archive_session(messages, session_id="s-whole", granularity="session",
                                dedup=False)
        test("session 粒度：只写一条", len(one) == 1)
        test("session 粒度：沿用旧格式",
             one[0].content.startswith("[user] ") and "[assistant] " in one[0].content)

        test("空会话返回空列表", p.archive_session([], session_id="empty") == [])
        try:
            p.archive_session(messages, granularity="paragraph")
            test("非法粒度被拒绝", False)
        except ValueError:
            test("非法粒度被拒绝", True)

        meta = p.archive_session([{"role": "user", "content": "自定义元数据测试一"},
                                  {"role": "user", "content": "自定义元数据测试二"}],
                                 session_id="s-meta", metadata={"src": "hook"})
        test("turn：自定义 metadata 每轮都保留",
             all(m.metadata.get("src") == "hook" for m in meta))
        p.close()

    # MCP 工具：默认逐轮，granularity=session 走旧行为，返回兼容字段
    with tempfile.TemporaryDirectory() as d:
        mcp = MCPServer(base_dir=d, wing="test",
                        embedding_backend=BagOfWordsEmbeddingBackend(),
                        vector_backend=NumpyVectorBackend())
        r = mcp._call("mp_archive_session", {"messages": messages, "session_id": "m1"})
        test("MCP 默认逐轮", r["archived"] == 3 and r["granularity"] == "turn", str(r))
        test("MCP 保留 archived_id 字段", r["archived_id"] == r["ids"][0])
        r2 = mcp._call("mp_archive_session", {"messages": messages, "session_id": "m2",
                                              "granularity": "session"})
        test("MCP session 粒度写一条", r2["archived"] == 1, str(r2))
        mcp._palace.close()


def test_persistence_across_restart():
    """默认后端升级：SqliteVectorBackend 重启不丢向量索引"""
    print("\n── 持久化：重启后仍可检索 ──")
    with tempfile.TemporaryDirectory() as d:
        vec_path = os.path.join(d, "vectors.db")
        p = VecRecall(
            base_dir=d, wing="test-project",
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=SqliteVectorBackend(vec_path),
        )
        p.add(content="数据库连接池最大连接数设为 100", topic="config")
        p.close()

        # 模拟重启：重新打开，向量索引应从 SQLite 恢复
        p2 = VecRecall(
            base_dir=d, wing="test-project",
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=SqliteVectorBackend(vec_path),
        )
        results = p2.search("数据库连接池", n=5)
        test("重启后向量索引仍在", len(results) > 0,
             "重启后检索无结果（持久化失效）")
        p2.close()


def test_bow_lexical_similarity():
    """词法嵌入后端：相似文本相似度显著高于无关文本"""
    print("\n── 词法语义嵌入 ──")
    emb = BagOfWordsEmbeddingBackend()
    a = emb.embed("数据库连接池配置优化")
    b = emb.embed("数据库 连接池")
    c = emb.embed("今天天气不错适合出去玩")

    sim_ab = sum(x * y for x, y in zip(a, b))
    sim_ac = sum(x * y for x, y in zip(a, c))
    test("相似文本相似度高（> 0.3）", sim_ab > 0.3, f"sim_ab={sim_ab:.3f}")
    test("无关文本相似度低（< 0.2）", sim_ac < 0.2, f"sim_ac={sim_ac:.3f}")
    test("相似 > 无关", sim_ab > sim_ac)


def test_subword_embedding():
    """词法嵌入 subword 增强：捕获词形变化与拼写变体"""
    print("\n── 词法嵌入 subword 增强 ──")
    emb = BagOfWordsEmbeddingBackend()

    def sim(x, y):
        a, b = emb.embed(x), emb.embed(y)
        return sum(p * q for p, q in zip(a, b))

    # 词形变化：running / runs（char 3-gram 重叠）
    s_run = sim("the service is running", "the service runs")
    s_cat = sim("the service is running", "the cat is sleeping")
    test("词形变化相似（running/runs > 0.4）", s_run > 0.4, f"sim={s_run:.3f}")
    test("词形变化 > 无关", s_run > s_cat)

    # 拼写变体：database / databases
    s_db = sim("configure the database", "configure the databases")
    test("拼写变体相似（database/databases > 0.5）", s_db > 0.5, f"sim={s_db:.3f}")

    # 中文三元组：子串重叠更强
    s_pool = sim("数据库连接池", "连接池")
    test("中文子串重叠（连接池 > 0.4）", s_pool > 0.4, f"sim={s_pool:.3f}")


def test_update_and_delete_lifecycle():
    """记忆生命周期：更新自动重向量化，删除同步清理"""
    print("\n── 生命周期：更新/删除 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        m = p.add(content="旧版本：使用 Redis 缓存", topic="cache", importance=0.5)

        # 更新内容
        p.update(m.id, content="新版本：改用内存缓存", importance=0.9)
        got = p._kg.get(m.id)
        test("内容已更新", got.content == "新版本：改用内存缓存")
        test("重要性已更新", got.importance == 0.9)

        # 更新后的内容应能被检索到（重向量化生效）
        r_new = p.search("内存缓存", n=5)
        r_old = p.search("Redis", n=5)
        test("新内容可检索", len(r_new) > 0)
        test("旧内容不再命中", all("Redis" not in x.memory.content for x in r_old)
             or r_old[0].score < 0.5)

        # 删除
        ok = p.delete(m.id)
        test("删除返回 True", ok)
        test("删除后 get 返回空", p._kg.get(m.id) is None)
        test("删除后向量索引为空", p._vec.count() == 0)
        p.close()


def test_prune():
    """清理低价值记忆"""
    print("\n── 清理 prune ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p.add(content="重要决策：采用微服务架构", importance=0.9)
        p.add(content="临时笔记：随手记一下", importance=0.1)
        p.add(content="另一条重要记录", importance=0.8)

        # 预览模式
        candidates = p.prune(min_importance=0.5, dry_run=True)
        test("预览找到 1 条低价值", len(candidates) == 1)

        # 真正删除
        p.prune(min_importance=0.5, dry_run=False)
        test("清理后剩余 2 条", p._kg.count() == 2)
        p.close()


def test_reindex():
    """从原文重建向量索引（后端迁移/损坏恢复）"""
    print("\n── 重建索引 reindex ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p.add(content="向量索引重建测试内容", topic="test")
        # 清空向量索引（模拟损坏）
        p._vec.clear()
        test("清空后索引为 0", p._vec.count() == 0)

        n = p.reindex()
        test("重建数量正确", n == 1)
        results = p.search("向量索引", n=5)
        test("重建后可检索", len(results) > 0)
        p.close()


def test_hybrid_scoring():
    """混合评分：融合重要性 + 时间新鲜度重排"""
    print("\n── 混合评分 hybrid ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        # 相同语义内容，一条高重要性、一条低重要性
        p.add(content="认证模块使用 JWT 令牌", topic="auth", importance=0.95)
        p.add(content="认证模块也使用 JWT 令牌", topic="auth", importance=0.1)

        pure = p.search("JWT 认证", n=2, hybrid=False)
        hybrid = p.search("JWT 认证", n=2, hybrid=True)

        test("纯向量返回 2 条", len(pure) == 2)
        test("hybrid 也返回 2 条（不改变召回集合）", len(hybrid) == 2)
        test("hybrid 首位是高重要性记忆",
             hybrid[0].memory.importance >= hybrid[-1].memory.importance)
        p.close()


def test_blockchain_integration():
    """哈希链集成：写入生成区块，可验证完整性"""
    print("\n── 哈希链集成 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d, use_blockchain=True)
        p.add(content="区块链防篡改测试记忆", topic="audit")
        p.add(content="第二条审计记录", topic="audit")

        result = p.verify_integrity()
        test("区块链已启用", result.get("enabled") is True)
        all_valid = all(info["valid"] for info in result["wings"].values())
        test("链完整有效", all_valid)

        stats = p.stats()
        test("区块链统计已挂载", "blockchain" in stats)
        p.close()

    # 未启用时给出明确提示
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d, use_blockchain=False)
        r = p.verify_integrity()
        test("未启用时 enabled=False", r["enabled"] is False)
        p.close()


# ─────────────────────────────────────────────
# 运行所有测试
# ─────────────────────────────────────────────

def test_embedding_backend_factory():
    """嵌入后端工厂：bow/ollama/api 构造正确（不实际联网）"""
    print("\n── 嵌入后端工厂 ──")
    b = build_embedding_backend("bow")
    test("bow 后端", isinstance(b, BagOfWordsEmbeddingBackend))

    o = build_embedding_backend("ollama")
    test("ollama 默认模型", isinstance(o, OllamaEmbeddingBackend)
         and o.model == "nomic-embed-text")
    test("ollama 默认地址", o.base_url == "http://localhost:11434")

    o2 = build_embedding_backend("ollama", model="bge-m3",
                                 base_url="http://127.0.0.1:9999")
    test("ollama 自定义模型/地址", o2.model == "bge-m3"
         and o2.base_url == "http://127.0.0.1:9999")

    a = build_embedding_backend("api", model="text-embedding-3-small",
                                base_url="https://x/v1", api_key="sk-123")
    test("api 后端", isinstance(a, APIEmbeddingBackend)
         and a.model == "text-embedding-3-small"
         and a.base_url == "https://x/v1" and a.api_key == "sk-123")


def test_list_memories():
    """回吐记忆列表：分页/过滤/原文/排序"""
    print("\n── 记忆列表 list_memories ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p.add(content="重要：数据库选型 PostgreSQL", topic="db", importance=0.95)
        p.add(content="普通：认证用 JWT", topic="auth", importance=0.3)
        p.add(content="另一条 db 记录", topic="db", importance=0.6)
        last = p.add(content="最新一条记录", topic="misc", importance=0.5)

        r = p.list_memories(limit=10)
        test("总数正确", r["total"] == 4)
        test("返回 4 条", len(r["memories"]) == 4)
        test("含 ID/预览/重要性字段", all(
            k in r["memories"][0]
            for k in ("id", "content_preview", "importance", "wing", "topic")))

        r_db = p.list_memories(topic="db")
        test("按话题过滤", r_db["total"] == 2)

        r_full = p.list_memories(topic="auth", full=True)
        test("full 包含原文", "content" in r_full["memories"][0])

        test("默认按重要性排序（首位最高）",
             r["memories"][0]["importance"] > 0.9)

        r_recent = p.list_memories(sort="recent")
        test("按时间排序首位是最新", r_recent["memories"][0]["id"] == last.id)

        m = p._kg.get(r["memories"][0]["id"])
        dd = memory_to_dict(m)
        test("memory_to_dict 字段一致", dd["id"] == m.id and dd["wing"] == m.wing)
        p.close()


def test_security():
    """安全防护：REST 认证 / CORS 收紧 / 文件路径隔离"""
    print("\n── 安全防护 ──")

    # resolve_api_key
    test("本机免认证", resolve_api_key(None, "127.0.0.1") is None)
    test("指定 token", resolve_api_key("abc", "127.0.0.1") == "abc")
    rejected = False
    try:
        resolve_api_key(None, "0.0.0.0")
    except SystemExit:
        rejected = True
    test("非本机无 token 拒绝启动", rejected)

    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p.add(content="秘密记忆", topic="auth", importance=0.8)

        srv = VecRecallHTTPServer(("127.0.0.1", 0), p, api_key="secret")
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        time.sleep(0.2)
        base = f"http://127.0.0.1:{port}"

        def get(path, headers=None):
            req = urllib.request.Request(base + path, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, dict(resp.headers)
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers)

        code, _ = get("/memories")
        test("无 token 返回 401", code == 401)
        code, _ = get("/memories", {"Authorization": "Bearer secret"})
        test("Bearer token 返回 200", code == 200)
        code, _ = get("/memories?key=secret")
        test("?key= 查询参数返回 200", code == 200)
        code, _ = get("/health")
        test("/health 免认证", code == 200)
        code, headers = get("/memories", {"Authorization": "Bearer secret"})
        test("CORS 默认关闭", "Access-Control-Allow-Origin" not in headers)

        srv.shutdown()
        srv.server_close()

        # MCP 文件路径隔离
        mcp = MCPServer(base_dir=d, wing="test",
                        embedding_backend=BagOfWordsEmbeddingBackend(),
                        vector_backend=NumpyVectorBackend())
        test("数据目录内放行", bool(mcp._safe_path(os.path.join(d, "a.json"))))
        blocked = False
        try:
            mcp._safe_path(os.path.join(tempfile.gettempdir(), "x.json"))
        except ValueError:
            blocked = True
        test("越界路径拒绝", blocked)
        mcp._palace.close()
        p.close()


def test_extractor():
    """认知层：启发式抽取 + LLM 抽取（mock）+ 失败降级"""
    print("\n── 认知层抽取器 ──")

    h = HeuristicExtractor()
    e1 = h.extract("决定采用微服务架构方案，将单体拆分为多个模块")
    test("启发式话题推断", e1.topic in ("architecture", "decision"))
    test("启发式重要性 > 0", e1.importance > 0.0)
    test("启发式摘要非空", bool(e1.ui_summary))

    e2 = h.extract("Alice works at Google on the Kubernetes project")
    test("英文实体抽取", any("Google" in x or "Kubernetes" in x for x in e2.entities))

    class MockLLM(LLMExtractor):
        def _chat(self, prompt):
            return ('{"topic": "architecture", "importance": 0.9, '
                    '"summary": "采用微服务架构", '
                    '"entities": ["VecRecall", "微服务"], '
                    '"relations": [{"source": "VecRecall", '
                    '"target": "微服务", "type": "uses"}]}')

    llm = MockLLM(provider="ollama")
    e3 = llm.extract("任意内容")
    test("LLM 抽取话题", e3.topic == "architecture")
    test("LLM 抽取重要性", e3.importance == 0.9)
    test("LLM 抽取摘要", e3.ui_summary == "采用微服务架构")
    test("LLM 抽取实体", e3.entities == ["VecRecall", "微服务"])
    test("LLM 抽取关系", len(e3.relations) == 1
         and e3.relations[0]["type"] == "uses")

    class FailingLLM(LLMExtractor):
        def _chat(self, prompt):
            raise OSError("网络不可用")

    e4 = FailingLLM(provider="ollama").extract("部署了一个 bug 修复")
    test("LLM 失败降级启发式",
         e4.topic in ("ops", "general") and e4.importance > 0.0)

    test("工厂 heuristic", isinstance(build_extractor("heuristic"), HeuristicExtractor))
    test("工厂 ollama", isinstance(build_extractor("ollama"), LLMExtractor))
    test("工厂 api", isinstance(build_extractor("api", model="gpt-4o-mini"), LLMExtractor))
    test("工厂 none", isinstance(build_extractor("none"), HeuristicExtractor))


def test_dedup():
    """认知层：语义去重合并 + extractor 集成到 add"""
    print("\n── 语义去重合并 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)

        m1 = p.add(content="决定采用微服务架构")
        m2 = p.add(content="决定采用微服务架构")
        test("重复内容合并为同一条", m1.id == m2.id)
        test("合并后 occurrences=2", m2.metadata.get("occurrences") == 2)
        test("合并后库内仅一条", p._kg.count() == 1)

        m3 = p.add(content="数据库索引需要重建以提高查询性能")
        test("不同内容新增一条", m3.id != m1.id and p._kg.count() == 2)

        m4 = p.add(content="决定采用微服务架构", dedup=False)
        test("关闭去重后新增一条", m4.id != m1.id and p._kg.count() == 3)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p._extractor = HeuristicExtractor()
        m = p.add(content="决定采用微服务架构方案")
        test("extractor 自动推断话题", m.topic in ("architecture", "decision"))
        test("extractor 自动生成摘要", bool(m.ui_summary))
        p.close()


def test_temporal_graph():
    """时序知识图谱：实体 + 状态演化 + 关系去重 + 集成"""
    print("\n── 时序知识图谱 ──")
    with tempfile.TemporaryDirectory() as d:
        g = TemporalGraph(os.path.join(d, "graph.db"))

        # Alice 的 team 属性随时间演化：搜索 → 微服务
        g.ingest(
            entities=["Alice", "Google"],
            relations=[{"source": "Alice", "target": "Google", "type": "works_at"}],
            entity_states=[{"entity": "Alice", "attribute": "team", "value": "搜索"}],
            timestamp=100.0,
        )
        g.ingest(
            entities=["Alice", "Google"],
            relations=[{"source": "Alice", "target": "Google", "type": "works_at"}],
            entity_states=[{"entity": "Alice", "attribute": "team", "value": "微服务"}],
            timestamp=200.0,
        )

        e = g.get_entity("Alice")
        test("实体画像存在", e is not None)
        test("实体提及次数=2", e["mentions"] == 2)
        test("实体当前属性快照为最新值", e["attributes"].get("team") == "微服务")
        tl = e["timeline"]
        test("状态时间线有两条", len(tl) == 2)
        test("时间线按时间升序",
             tl[0]["value"] == "搜索" and tl[1]["value"] == "微服务")

        rels = g.entity_relations("Alice")
        test("关系 works_at 存在", any(r["type"] == "works_at" for r in rels))
        test("同边去重仅保留一条", g.stats()["relations"] == 1)

        ents = g.list_entities()
        test("实体列表含 Alice", any(x["id"] == "alice" for x in ents))
        test("Alice 提及次数最高", ents[0]["id"] == "alice")
        test("实体名规范化", normalize_entity("  Alice   Smith ") == "alice smith")
        g.close()

    # VecRecall 集成：add 时自动写图（HeuristicExtractor 抽英文实体）
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p._extractor = HeuristicExtractor()
        p.add(content="Alice works at Google on Kubernetes")
        p.add(content="Bob joined Google this month")
        stats = p.graph_stats()
        test("集成后图谱有实体", stats["entities"] >= 3)
        alice = p.entity("Alice")
        test("集成后可查实体 Alice", alice is not None and alice["mentions"] >= 1)
        google = p.entity("Google")
        test("集成后 Google 提及 2 次", google is not None and google["mentions"] == 2)
        p.close()


def test_forgetting_and_distill():
    """遗忘曲线：强度衰减 + 复习增强 + 主动蒸馏"""
    print("\n── 遗忘曲线 & 主动蒸馏 ──")
    fc = ForgettingCurve(base_half_life_days=7.0)

    now = 1_000_000.0
    old = Memory.create(wing="w", topic="t", content="x", importance=0.1)
    old.timestamp = now - 30 * 86400  # 30 天前、低重要性 → 已遗忘
    new = Memory.create(wing="w", topic="t", content="y", importance=0.9)
    new.timestamp = now  # 刚写入

    test("新记忆强度高", fc.strength(new, now) > 0.9)
    test("低重要性旧记忆强度低", fc.strength(old, now) < 0.2)
    test("旧记忆已遗忘", fc.is_forgotten(old, 0.2, now))
    test("重要记忆衰减慢于普通记忆", fc.stability(0.9) > fc.stability(0.1))

    # 复习增强：复习后距上次复习时间清零 + 稳定性倍增
    old2 = Memory.create(wing="w", topic="t", content="z", importance=0.1)
    old2.timestamp = time.time() - 30 * 86400
    s_before = fc.strength(old2)
    fc.review(old2)
    test("复习后强度提升", fc.strength(old2) > s_before)
    test("复习次数记录", old2.metadata.get("reviews") == 1)

    # VecRecall 集成：遗忘评估 + 复习 + 主动蒸馏
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        m1 = p.add("今天天气不错", topic="diary", importance=0.3)
        p.add("中午吃了面条", topic="diary", importance=0.3)
        p.add("晚上散步", topic="diary", importance=0.3)
        p.add("决定采用微服务架构", topic="architecture", importance=0.9)

        test("记忆强度可查询", isinstance(p.memory_strength(m1.id), float))
        p.recall(m1.id)
        test("复习后持久化", p._kg.get(m1.id).metadata.get("reviews") == 1)

        # 蒸馏：diary 组 3 条低重要性 → 蒸馏成 1 条；architecture 高价值不动
        plans = p.distill(topic="diary", min_cluster=2, dry_run=True)
        test("蒸馏计划识别出碎片簇", len(plans) == 1 and plans[0]["count"] == 3)
        test("蒸馏计划含摘要", bool(plans[0]["summary"]))

        p.distill(topic="diary", min_cluster=2, dry_run=False)
        test("蒸馏后总记忆数 = 4+1", p.stats()["total_memories"] == 5)

        distilled = None
        for m in p._kg.iter_all():
            if m.metadata.get("distilled"):
                distilled = m
        test("蒸馏记忆已生成", distilled is not None)
        test("蒸馏记忆指向 3 条源", len(distilled.metadata.get("sources", [])) == 3)
        src = p._kg.get(m1.id)
        test("源记忆标记 distilled_into", src.metadata.get("distilled_into") == distilled.id)
        p.close()


def test_graph_search():
    """多跳向量+图谱混合检索：向量种子 → 图谱扩展关联实体"""
    print("\n── 多跳混合检索 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p._extractor = HeuristicExtractor()
        p.add("Alice works at Google")
        p.add("Google acquired DeepMind")
        p.add("DeepMind developed AlphaGo")

        res = p.graph_search("Alice", n=3, hops=2)
        test("直接命中存在", len(res["direct"]) > 0)
        test("图谱扩展发现关联记忆", len(res["graph"]) >= 1)
        graph_contents = " ".join(h["memory"].content for h in res["graph"])
        test("多跳扩展到 DeepMind", "DeepMind" in graph_contents)
        test("扩展记忆带路径证据", any(h["paths"] for h in res["graph"]))
        p.close()


def test_at_rest_encryption():
    """静态加密：记忆内容磁盘加密 + 正确密钥读回 + 错误密钥拒绝"""
    print("\n── 静态加密（at-rest）──")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("  （跳过：未安装 cryptography，pip install vecrecall[crypto]）")
        return

    import sqlite3
    from pathlib import Path

    # Cipher 基本往返
    key = Cipher.generate_key()
    c = Cipher.from_key(key)
    tok = c.encrypt("敏感内容 secret-123")
    test("密文不等于明文", tok != "敏感内容 secret-123")
    test("解密还原原文", c.decrypt(tok) == "敏感内容 secret-123")
    test("明文旧数据兼容", c.decrypt("明文旧数据") == "明文旧数据")

    # 密码派生（盐持久化）
    with tempfile.TemporaryDirectory() as d:
        c2 = Cipher.resolve("我的密码", Path(d))
        test("密码派生往返", c2.decrypt(c2.encrypt("hello")) == "hello")
        test("盐文件已生成", (Path(d) / "salt.key").exists())

    # VecRecall 加密集成
    with tempfile.TemporaryDirectory() as d:
        p = VecRecall(
            base_dir=d, wing="enc", encryption_key=key,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        m = p.add("Alice 的银行卡号 6222-0000-0000-0000", topic="secret")
        p.close()

        # 直接读磁盘，确认是密文（不泄露原文）
        conn = sqlite3.connect(os.path.join(d, "knowledge.db"))
        raw = conn.execute(
            "SELECT content, ui_summary, metadata FROM memories").fetchone()
        conn.close()
        test("磁盘 content 为密文", raw[0].startswith("gAAAAA")
             and "6222" not in raw[0] and "银行卡" not in raw[0])
        test("磁盘 metadata 为密文", raw[2].startswith("gAAAAA"))

        # 正确密钥读回
        p2 = VecRecall(
            base_dir=d, wing="enc", encryption_key=key,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        got = p2._kg.get(m.id)
        test("正确密钥读回原文",
             got.content == "Alice 的银行卡号 6222-0000-0000-0000")
        test("元数据正确还原", got.metadata == m.metadata)
        p2.close()

        # 错误密钥应拒绝读取
        p3 = VecRecall(
            base_dir=d, wing="enc", encryption_key=Cipher.generate_key(),
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        try:
            list(p3._kg.iter_all())
            test("错误密钥拒绝读取", False)
        except Exception:
            test("错误密钥拒绝读取", True)
        p3.close()


def test_typed_memory_migration():
    """阶段 A：memory_type/visibility/owner/retention_policy 新字段 + 老库 migration"""
    import sqlite3
    print("\n── 阶段 A：类型化/隔离/留存字段 + migration ──")
    with tempfile.TemporaryDirectory() as d:
        # 1. 新写入：新字段可设置并持久化
        p = make_palace(d)
        m = p.add(
            content="付款审批决策:金额 50000,供应商 A,审批人 B",
            topic="audit",
            memory_type="episodic",
            visibility="private",
            owner="agent-1",
            retention_policy="immutable",
        )
        test("memory_type 可写入", m.memory_type == "episodic")
        test("visibility 可写入", m.visibility == "private")
        test("owner 可写入", m.owner == "agent-1")
        test("retention_policy 可写入", m.retention_policy == "immutable")

        # 2. 默认值
        m2 = p.add(content="普通对话记忆", topic="chat")
        test("默认 memory_type=episodic", m2.memory_type == "episodic")
        test("默认 visibility=private", m2.visibility == "private")
        test("默认 owner 为空", m2.owner == "")
        test("默认 retention_policy=decay", m2.retention_policy == "decay")
        p.close()

        # 3. 重启后持久化读回
        p2 = make_palace(d)
        got = p2._kg.get(m.id)
        test("重启后 memory_type 读回", got.memory_type == "episodic")
        test("重启后 retention_policy 读回", got.retention_policy == "immutable")
        test("重启后 owner 读回", got.owner == "agent-1")

        # 4. 序列化透出
        dd = memory_to_dict(got)
        test("memory_to_dict 含 memory_type", dd.get("memory_type") == "episodic")
        test("memory_to_dict 含 visibility", dd.get("visibility") == "private")
        test("memory_to_dict 含 retention_policy",
             dd.get("retention_policy") == "immutable")
        p2.close()

        # 5. 老库 migration：手工建 9 列旧结构表，打开后自动补列
        old_db = os.path.join(d, "old_memories.db")
        conn = sqlite3.connect(old_db)
        conn.executescript("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            wing TEXT NOT NULL,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            session_id TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            ui_summary TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}'
        );
        """)
        conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-1", "w", "t", "老记忆内容", 1234567890.0, "s", 0.7, "", "{}"),
        )
        conn.commit()
        conn.close()

        kg = KnowledgeGraph(old_db)
        got_old = kg.get("old-1")
        test("老库迁移后可读取", got_old is not None and got_old.content == "老记忆内容")
        test("老记录默认 memory_type", got_old.memory_type == "episodic")
        test("老记录默认 visibility", got_old.visibility == "private")
        test("老记录默认 retention_policy", got_old.retention_policy == "decay")
        # 迁移后新写入也应正常
        new_mem = Memory.create(wing="w", topic="t", content="迁移后新写入")
        kg.save(new_mem)
        test("迁移后新写入成功", kg.get(new_mem.id) is not None)
        kg.close()


def test_immutable_exemption():
    """阶段 B：retention_policy=immutable 豁免去重/遗忘/prune/蒸馏，始终可召回"""
    print("\n── 阶段 B：审计记忆豁免（immutable）──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        p._extractor = HeuristicExtractor()

        # 1. 去重豁免：语义高度相似的两笔审计记忆各存各的
        m1 = p.add(content="付款审批决策:金额 50000 元,供应商 A,审批人 B",
                   topic="audit", retention_policy="immutable")
        m2 = p.add(content="付款审批决策:金额 50001 元,供应商 A,审批人 B",
                   topic="audit", retention_policy="immutable")
        test("immutable 记忆不合并(1)", m1.id != m2.id)
        test("immutable 记忆不合并(2)", m2.metadata.get("occurrences", 1) == 1)

        # 对照组：decay 记忆语义相似应被去重合并
        c1 = p.add(content="用户喜欢在早晨阅读科技新闻", topic="chat")
        c2 = p.add(content="用户喜欢在早晨阅读科技新闻", topic="chat")
        test("对照组 decay 记忆仍去重合并", c1.id == c2.id)
        p.close()

        # 2. 重启后验证独立持久化
        p = make_palace(d)
        got1 = p._kg.get(m1.id)
        got2 = p._kg.get(m2.id)
        test("两条审计记忆独立存在",
             got1 is not None and got2 is not None
             and got1.content != got2.content)

        # 3. 遗忘豁免：把 last_review 拨到很久以前，decay 记忆会被遗忘，immutable 不会
        old_ts = time.time() - 86400 * 365  # 一年前
        decay_mem = p.add(content="随时间衰减的普通聊天记忆", topic="chat",
                          importance=0.1)
        for mem_id in (m1.id, decay_mem.id):
            mem = p._kg.get(mem_id)
            mem.timestamp = old_ts
            mem.metadata["last_review"] = old_ts
            p._kg.save(mem)
        forgotten = {m.id for m in p.forgotten(threshold=0.2)}
        test("decay 记忆会进入已遗忘", decay_mem.id in forgotten)
        test("immutable 记忆不被标记已遗忘", m1.id not in forgotten)

        # 4. prune 豁免：低 importance + 超龄都不能删掉 immutable
        victims = p.prune(min_importance=0.5, older_than_days=30,
                          dry_run=False)
        victim_ids = {m.id for m in victims}
        test("decay 记忆被 prune", decay_mem.id in victim_ids)
        test("immutable 记忆不被 prune", m1.id not in victim_ids)
        test("immutable 记忆仍在库", p._kg.get(m1.id) is not None)

        # 5. 蒸馏豁免：同 topic 下 immutable 不进蒸馏分组
        for i in range(4):
            p.add(content=f"审批流水记录第 {i} 号", topic="audit",
                  retention_policy="immutable", importance=0.3)
        # 对照组：同 topic 且彼此语义可区分（过近会被去重合并，凑不满 min_cluster）
        chat_fragments = [
            "今天早上的地铁人很多很拥挤",
            "中午吃的外卖送达时间有点晚",
            "下午的会议持续了两个小时",
            "晚上散步经过新开的咖啡店",
        ]
        for c in chat_fragments:
            p.add(content=c, topic="chat", importance=0.3)
        plans = p.distill(topic="audit", dry_run=True)
        test("immutable topic 无蒸馏计划", len(plans) == 0)
        plans_chat = p.distill(topic="chat", dry_run=True)
        test("对照组 decay topic 有蒸馏计划", len(plans_chat) >= 1)

        # 6. 召回豁免：immutable 记忆无条件进 L1（低 importance 也不被挤掉）
        bundle = p.build_context()
        l1_ids = {r.memory.id for r in bundle.l1_key_moments}
        test("immutable 记忆始终在 L1", m1.id in l1_ids)
        p.close()


def test_typed_memory_routing():
    """阶段 C：memory_type 路由——semantic 强制入图 / procedural 精确匹配 / 类型过滤"""
    print("\n── 阶段 C：类型化记忆 + 检索过滤 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)  # 无 extractor，验证 semantic 兜底抽取

        # 1. semantic：无 extractor 也强制走时序图谱
        sem = p.add(content="供应商 Acme 于 2026 年 3 月进入合格名录",
                    topic="knowledge", memory_type="semantic")
        test("semantic 记忆可写入", sem.memory_type == "semantic")
        test("semantic 记忆实体入图", p.entity("Acme") is not None)

        # 2. procedural：场景标签 + 版本写入 metadata
        proc = p.add(content="付款超过 50000 元需双人复核",
                     topic="policy", memory_type="procedural",
                     scenario_tags=["payment", "gatekeeping"],
                     policy_version="v1")
        test("procedural 标签已写入",
             proc.metadata.get("scenario_tags") == ["payment", "gatekeeping"])
        test("procedural 版本已写入",
             proc.metadata.get("policy_version") == "v1")

        # 3. query_procedural：标签子集匹配 + 版本精确匹配
        p.add(content="审批超过 100000 元需三级会签",
              topic="policy", memory_type="procedural",
              scenario_tags=["approval", "gatekeeping"], policy_version="v1")
        p.add(content="付款超过 80000 元需双人复核（旧版规则）",
              topic="policy", memory_type="procedural",
              scenario_tags=["payment"], policy_version="v0")

        hits = p.query_procedural(scenario_tags=["gatekeeping"])
        test("按场景标签命中两条", len(hits) == 2)
        hits = p.query_procedural(scenario_tags=["payment"], version="v1")
        test("标签+版本精确命中", len(hits) == 1 and "50000" in hits[0].content)
        hits = p.query_procedural(scenario_tags=["nonexistent"])
        test("无命中返回空", len(hits) == 0)
        hits = p.query_procedural(version="v0")
        test("仅按版本命中", len(hits) == 1)

        # 4. episodic 记忆不进 procedural 检索
        p.add(content="一次普通的付款审批记录", topic="audit")
        test("episodic 不混入 procedural 检索",
             all(m.memory_type == "procedural"
                 for m in p.query_procedural(scenario_tags=None)))

        # 5. build_context 按类型过滤（L1/L2/L3 统一生效）
        for i in range(6):
            p.add(content=f"日常闲聊话题 {i}：今天聊到天气和午饭",
                  topic="chat", importance=0.9)  # 高 importance 占据 L1
        bundle_all = p.build_context()
        test("默认不过滤：各类型都在 L1",
             {r.memory.memory_type for r in bundle_all.l1_key_moments}
             >= {"episodic", "semantic", "procedural"})

        bundle_sem = p.build_context(memory_types=["semantic"])
        l1_types = {r.memory.memory_type for r in bundle_sem.l1_key_moments}
        test("声明 semantic 后 L1 只含 semantic", l1_types == {"semantic"})
        test("声明 semantic 后 L2 只含 semantic",
             {r.memory.memory_type for r in bundle_sem.l2_topic_context}
             <= {"semantic"})
        p.close()


def test_visibility_isolation():
    """阶段 D：visibility/owner 双层隔离 + 原子操作 + 跨 wing link 限制"""
    print("\n── 阶段 D：双层隔离 ──")
    with tempfile.TemporaryDirectory() as d:
        # 隔离测试用持久化向量后端（Numpy 是内存后端，跨实例不共享；
        # Chroma 在 Windows 下文件句柄会卡住临时目录清理，故用 SQLite）
        from pathlib import Path
        vec_db = str(Path(d) / "vectors.db")

        def palace(wing_owner: str) -> VecRecall:
            return VecRecall(
                base_dir=d, wing="test-project", owner=wing_owner,
                embedding_backend=BagOfWordsEmbeddingBackend(),
                vector_backend=SqliteVectorBackend(vec_db),
            )

        # owner=A 写入：一条 private 一条 shared
        pa = palace("agent-A")
        priv = pa.add(content="A 的私有策略思考:阈值应设为 0.3",
                      topic="policy", owner="agent-A",
                      memory_type="procedural",
                      scenario_tags=["gatekeeping"], policy_version="v1")
        shared = pa.add(content="共享知识:供应商 Acme 合格",
                        topic="knowledge", visibility="shared",
                        owner="agent-A", memory_type="semantic")

        # owner=B 的实例查 A 的记忆
        pb = palace("agent-B")

        # 1. search 不可见 private，可见 shared
        res_b = pb.search("私有策略思考 阈值")
        test("B 搜不到 A 的 private 记忆",
             all(r.memory.id != priv.id for r in res_b))
        res_b2 = pb.search("供应商 Acme 合格")
        test("B 能搜到 shared 记忆",
             any(r.memory.id == shared.id for r in res_b2))

        # 2. query_procedural 同样隔离
        test("B 的 procedural 查询不含 A 的 private",
             all(m.id != priv.id for m in
                 pb.query_procedural(scenario_tags=["gatekeeping"])))

        # 3. build_context 隔离（L1/L2/L3）
        bundle_b = pb.build_context(current_query="策略 阈值 供应商",
                                    load_l2=True, load_l3=True)
        all_b_ids = ({r.memory.id for r in bundle_b.l1_key_moments}
                     | {r.memory.id for r in bundle_b.l2_topic_context}
                     | {r.memory.id for r in bundle_b.l3_deep_results})
        test("B 的上下文不含 private", priv.id not in all_b_ids)
        test("B 的上下文含 shared", shared.id in all_b_ids)
        pb.close()

        # 4. owner=A 自己可见自己的 private
        bundle_a = pa.build_context(load_l2=True, load_l3=True)
        all_a_ids = ({r.memory.id for r in bundle_a.l1_key_moments}
                     | {r.memory.id for r in bundle_a.l2_topic_context}
                     | {r.memory.id for r in bundle_a.l3_deep_results})
        test("A 自己可见自己的 private", priv.id in all_a_ids)

        # 5. 原子操作：update_visibility 后 B 可见
        pa.update_visibility(priv.id, "shared")
        pb2 = palace("agent-B")
        res = pb2.search("私有策略思考 阈值")
        test("update_visibility 后 B 可见",
             any(r.memory.id == priv.id for r in res))
        pb2.close()

        # 6. 原子操作：update_retention_policy
        proc = pa._kg.get(priv.id)
        test("初始为 decay", proc.retention_policy == "decay")
        pa.update_retention_policy(priv.id, "immutable")
        test("转 immutable 后豁免 prune",
             all(m.id != priv.id for m in
                 pa.prune(min_importance=0.99, dry_run=True)))
        # 非法值拒绝
        try:
            pa.update_retention_policy(priv.id, "bogus")
            test("非法 retention_policy 被拒绝", False)
        except ValueError:
            test("非法 retention_policy 被拒绝", True)
        try:
            pa.update_visibility(priv.id, "bogus")
            test("非法 visibility 被拒绝", False)
        except ValueError:
            test("非法 visibility 被拒绝", True)

        # 7. 跨 wing link：非 shared 拒绝，双方 shared 允许
        other = palace("agent-A")
        other_mem = other.add(content="另一个 wing 的记忆",
                              topic="misc", wing="other-wing")
        try:
            pa.link(shared.id, other_mem.id)
            test("跨 wing link 对 private 目标拒绝", False)
        except ValueError:
            test("跨 wing link 对 private 目标拒绝", True)
        other.update_visibility(other_mem.id, "shared")
        pa.link(shared.id, other_mem.id)
        test("跨 wing link 双方 shared 允许", True)
        other.close()
        pa.close()


def test_topic_key_chains():
    """阶段 E：哈希链按 topic_key 分链——独立验证 + 向后兼容"""
    print("\n── 阶段 E：哈希链分链 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d, use_blockchain=True)

        # 1. 两条不同 topic_key 的审计记忆 → 两条独立子链
        c1 = p.add(content="契约 C-001 决策一:批准付款 50000",
                   topic="audit", retention_policy="immutable",
                   topic_key="contract:C-001")
        c2 = p.add(content="契约 C-002 决策一:驳回付款 30000",
                   topic="audit", retention_policy="immutable",
                   topic_key="contract:C-002")
        # 同一契约第二条决策 → 同一条子链
        c3 = p.add(content="契约 C-001 决策二:批准付款 20000",
                   topic="audit", retention_policy="immutable",
                   topic_key="contract:C-001")

        test("记忆携带 topic_key", c1.topic_key == "contract:C-001")
        test("memory_to_dict 透出 topic_key",
             memory_to_dict(c1)["topic_key"] == "contract:C-001")

        # 2. 子链独立验证
        ok1, msg1 = p._chain.verify_chain("test-project", "contract:C-001")
        ok2, msg2 = p._chain.verify_chain("test-project", "contract:C-002")
        test("C-001 子链完整", ok1 and "2 个区块" in msg1)
        test("C-002 子链完整", ok2 and "1 个区块" in msg2)

        # 3. 分链互不影响：两条链各自从 GENESIS 起链
        b1 = p._chain.get_blocks_by_topic("test-project", "contract:C-001")
        b2 = p._chain.get_blocks_by_topic("test-project", "contract:C-002")
        test("C-001 链有 2 个区块", len(b1) == 2)
        test("C-002 链有 1 个区块", len(b2) == 1)
        test("两条子链首块都从创世起链",
             b1[0].prev_hash == "0" * 64 and b2[0].prev_hash == "0" * 64)
        test("同链第二块链接首块", b1[1].prev_hash == b1[0].hash)

        # 4. 引擎级验证接口
        res = p.verify_integrity(wing="test-project", topic_key="contract:C-001")
        test("verify_integrity 子链验证通过",
             res["wings"]["test-project"]["valid"] is True)

        # 5. 记忆内容篡改检测：改库里的原文后子链哈希不再匹配内容
        #    （链防的是链上原文被改；这里只验证区块内容与库内原文可对照）
        got = p._kg.get(c1.id)
        test("链上区块内容与记忆原文一致",
             p._chain.get_blocks_by_topic("test-project", "contract:C-001")[0]
             .content == got.content)
        p.close()

        # 6. 向后兼容：老库（无 topic_key 列）打开后默认链可验证
        import sqlite3
        old_db = os.path.join(d, "old_chain.db")
        conn = sqlite3.connect(old_db)
        conn.execute("""
        CREATE TABLE blocks (
            block_id TEXT PRIMARY KEY, idx INTEGER NOT NULL, timestamp REAL NOT NULL,
            prev_hash TEXT NOT NULL, content TEXT NOT NULL, token_count INTEGER DEFAULT 0,
            keywords TEXT DEFAULT '[]', date_label TEXT NOT NULL, wing TEXT NOT NULL,
            session_id TEXT NOT NULL, trigger TEXT DEFAULT 'manual',
            hash TEXT NOT NULL, group_id TEXT DEFAULT '', metadata TEXT DEFAULT '{}'
        )
        """)
        conn.commit()
        conn.close()

        from vecrecall.blockchain import BlockChain
        chain = BlockChain(old_db)  # migration 补列
        blk = chain.new_block(content="老链上的区块", wing="old-wing")
        ok, msg = chain.verify_chain("old-wing")
        test("老库 migration 后默认链可写可验", ok)
        test("默认链 topic_key 为空", blk.metadata.get("topic_key", "") == "")
        chain.close()



def test_hybrid_as_of():
    """v2.3：hybrid 的时间新鲜度可按指定时刻计算（as_of），旧记忆库不再退化为纯向量。"""
    print("\n── hybrid 时间基准 as_of ──")
    day = 86400.0
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        old = p.add("项目部署方案采用 Kubernetes 集群", importance=0.5, dedup=False)
        new = p.add("项目部署方案改用 Kubernetes 托管集群", importance=0.5, dedup=False)
        base = time.time() - 3 * 365 * day          # 两条记忆都在三年前
        old.timestamp = base - 300 * day             # 更早
        new.timestamp = base - 1 * day               # 接近提问时刻
        p._kg.save(old)
        p._kg.save(new)

        q = "项目部署方案"
        pure = p.search(q, n=2)
        now_based = p.search(q, n=2, hybrid=True)
        at_q = p.search(q, n=2, hybrid=True, as_of=base)

        def gap(res):
            s = {r.memory.id: r.score for r in res}
            return s[new.id] - s[old.id]

        sim = {r.memory.id: r.score for r in pure}
        sim_gap = sim[new.id] - sim[old.id]
        # 按当前时间：两条都很旧，新鲜度都≈0，hybrid 只是把相似度乘 0.6
        test("默认 as_of：新鲜度几乎无影响",
             abs(gap(now_based) - 0.6 * sim_gap) < 1e-3, f"{gap(now_based)} vs {0.6 * sim_gap}")
        # 按提问时刻：近期记忆的新鲜度明显更高
        test("as_of=提问时刻：近期记忆得分提升",
             gap(at_q) > gap(now_based) + 0.1, f"{gap(at_q)} vs {gap(now_based)}")
        test("as_of=提问时刻：近期记忆排第一", at_q[0].memory.id == new.id)
        test("as_of 不改变召回集合",
             {r.memory.id for r in at_q} == {r.memory.id for r in pure})

        ctx = p.build_context(current_query=q, load_l2=False, load_l3=True,
                              hybrid=True, as_of=base)
        test("build_context 透传 as_of", ctx.l3_deep_results[0].memory.id == new.id)
        p.close()


def test_search_returns_topn_with_negative_scores():
    """回归：词法嵌入的余弦相似度可以为负，search 默认不能按 0 截断。

    LongMemEval 样例「What breed is my dog?」暴露的问题：只有 4 条记忆、取 top-5，
    答案却没出现，因为答案的相似度是 -0.024，被默认 min_score=0.0 丢掉了。
    """
    print("\n── 回归：负相似度不被截断 ──")
    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        # 与 benchmarks/longmemeval_sample.json 的 sample_ssu_1 完全一致（含助手轮次），
        # 去掉助手轮次后相似度不再为负，复现不了问题
        docs = [
            "user: Can you suggest a quick pasta recipe for dinner?\n"
            "assistant: Try garlic butter spaghetti with parmesan.",
            "user: I just adopted a golden retriever puppy named Max, any training tips?\n"
            "assistant: Start with crate training and short sessions of basic commands.",
            "user: What is the capital of Australia?\nassistant: Canberra.",
            "user: Help me plan a weekend hiking trip near the mountains.\n"
            "assistant: Pack water, layers and check the trail map.",
        ]
        ids = [p.add(x, dedup=False).id for x in docs]
        q = "What breed is my dog?"
        raw = p._vec.query(p._emb.embed(q), n=10, min_score=float("-inf"))
        test("样例中确有负相似度", any(s < 0 for _, s, _ in raw),
             str([round(s, 3) for _, s, _ in raw]))

        res = p.search(q, n=5)
        test("默认 search 返回全部 4 条", len(res) == 4, str(len(res)))
        test("答案记忆在结果中", ids[1] in [r.memory.id for r in res])

        res_thr = p.search(q, n=5, min_score=0.0)
        test("显式 min_score=0.0 仍可截断", all(r.score >= 0.0 for r in res_thr))

        ctx = p.build_context(current_query=q, load_l2=False, load_l3=True)
        test("L3 也返回全部 4 条", len(ctx.l3_deep_results) == 4)
        p.close()


def test_extension_hooks():
    """v2.2：扩展钩子（企业版扩展包的挂载点），默认不改变行为"""
    print("\n── 扩展钩子 ──")
    from vecrecall.core.hooks import HookRegistry, HOOK_POINTS
    import vecrecall.core.engine as eng_mod
    from vecrecall.core import models, vectors, embeddings, storage
    test("拆分后 engine 仍导出 Memory", eng_mod.Memory is models.Memory)
    test("拆分后 engine 仍导出 SqliteVectorBackend",
         eng_mod.SqliteVectorBackend is vectors.SqliteVectorBackend)
    test("拆分后 engine 仍导出 build_embedding_backend",
         eng_mod.build_embedding_backend is embeddings.build_embedding_backend)
    test("拆分后 engine 仍导出 KnowledgeGraph",
         eng_mod.KnowledgeGraph is storage.KnowledgeGraph)
    test("四个调用点", set(HOOK_POINTS) ==
         {"before_write", "after_write", "recall_filter", "on_delete"})

    with tempfile.TemporaryDirectory() as d:
        p = make_palace(d)
        test("默认无钩子", not any(p.hooks.has(x) for x in HOOK_POINTS))

        # before_write：改写字段
        def tag(fields):
            fields["metadata"]["tenant"] = "t1"
            return fields
        p.hooks.register("before_write", tag)
        m = p.add("钩子写入测试：before_write 改写 metadata", dedup=False)
        test("before_write 可改写字段", m.metadata.get("tenant") == "t1")

        # before_write：抛异常拒绝写入
        def deny(fields):
            if "禁止" in fields["content"]:
                raise PermissionError("denied")
        p.hooks.register("before_write", deny)
        try:
            p.add("这条内容禁止写入", dedup=False)
            test("before_write 可拒绝写入", False)
        except PermissionError:
            test("before_write 可拒绝写入", True)
        test("被拒绝的内容未落库",
             all("禁止" not in x.content for x in p._kg.iter_all()))
        p.hooks.unregister("before_write", deny)
        p.hooks.unregister("before_write", tag)

        # after_write：审计 sink，含去重合并
        log = []
        p.hooks.register("after_write", lambda mem, merged: log.append((mem.id, merged)))
        a = p.add("after_write 审计 sink 测试内容")
        b = p.add("after_write 审计 sink 测试内容")
        test("after_write 新增时触发", log and log[0] == (a.id, False))
        test("after_write 合并时 merged=True", len(log) == 2 and log[1] == (b.id, True))

        # recall_filter：只能删减
        p.add("机密项目 Alpha 的预算", dedup=False)
        p.add("公开项目 Beta 的预算", dedup=False)
        before = [r.memory.content for r in p.search("项目 预算", n=10)]
        test("过滤前能搜到机密", any("Alpha" in c for c in before))

        def hide_alpha(memories, viewer):
            return [x for x in memories if "Alpha" not in x.content]
        p.hooks.register("recall_filter", hide_alpha)
        after = [r.memory.content for r in p.search("项目 预算", n=10)]
        test("recall_filter 作用于 search", not any("Alpha" in c for c in after))
        test("recall_filter 保留其余结果", any("Beta" in c for c in after))
        ctx = p.build_context(current_query="项目 预算", load_l3=True)
        allc = [r.memory.content for r in
                ctx.l1_key_moments + ctx.l2_topic_context + ctx.l3_deep_results]
        test("recall_filter 作用于 build_context", not any("Alpha" in c for c in allc))

        def inject(memories, viewer):
            fake = models.Memory.create(wing="x", topic="x", content="伪造记忆")
            return memories + [fake]
        p.hooks.register("recall_filter", inject)
        after2 = [r.memory.content for r in p.search("项目 预算", n=10)]
        test("recall_filter 不能新增结果", "伪造记忆" not in after2)

        # on_delete
        deleted = []
        p.hooks.register("on_delete", deleted.append)
        p.delete(a.id)
        test("on_delete 触发", deleted == [a.id])

        # 插件对象
        class Plugin:
            def register(self, registry):
                registry.register("after_write", lambda mem, merged: None)
        reg = HookRegistry()
        reg.install(Plugin())
        test("插件对象可安装", reg.has("after_write"))
        try:
            reg.register("no_such_point", lambda: None)
            test("未知调用点被拒绝", False)
        except ValueError:
            test("未知调用点被拒绝", True)
        p.close()

def test_core_has_no_scenario_schema():
    """核心无场景 schema：契约等业务适配器属于企业扩展包，核心源码不得出现。"""
    print("\n── 核心边界守护 ──")
    root = os.path.dirname(os.path.dirname(__file__))
    for rel in [("vecrecall", "core", "engine.py"), ("vecrecall", "mcp", "server.py"),
                ("vecrecall", "api", "server.py")]:
        src = open(os.path.join(root, *rel), encoding="utf-8").read()
        name = "/".join(rel)
        test(f"{name} 不含 contract_id", "contract_id" not in src)
        test(f"{name} 不引用 adapters", "vecrecall.adapters" not in src)
    test("核心包不含 adapters 目录",
         not os.path.isdir(os.path.join(root, "vecrecall", "adapters")))

def run_all():
    print("=" * 50)
    print("VecRecall — 测试套件")
    print("=" * 50)

    test_basic_add_and_retrieve()
    test_no_structural_filter_in_retrieval()
    test_aaak_not_in_retrieval()
    test_four_layer_stack()
    test_l2_semantic_trigger()
    test_l1_importance_ranking()
    test_knowledge_graph()
    test_batch_add()
    test_diary_isolation()
    test_heuristic_importance()
    test_session_archive()
    test_archive_session_granularity()
    test_persistence_across_restart()
    test_bow_lexical_similarity()
    test_subword_embedding()
    test_update_and_delete_lifecycle()
    test_prune()
    test_reindex()
    test_hybrid_scoring()
    test_blockchain_integration()
    test_embedding_backend_factory()
    test_list_memories()
    test_security()
    test_extractor()
    test_dedup()
    test_temporal_graph()
    test_forgetting_and_distill()
    test_graph_search()
    test_at_rest_encryption()
    test_typed_memory_migration()
    test_immutable_exemption()
    test_typed_memory_routing()
    test_visibility_isolation()
    test_topic_key_chains()
    test_hybrid_as_of()
    test_search_returns_topn_with_negative_scores()
    test_extension_hooks()
    test_core_has_no_scenario_schema()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过  {'✓ 全部通过' if FAIL == 0 else f'✗ {FAIL} 失败'}")
    print("=" * 50)
    return FAIL == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
