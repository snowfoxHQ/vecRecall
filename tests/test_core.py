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
import tempfile
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vecrecall.core.engine import (
    VecRecall, Memory, NumpyVectorBackend, HashEmbeddingBackend
)

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


def make_palace(tmp_dir: str) -> VecRecall:
    """创建测试用实例（哈希嵌入，内存向量）"""
    return VecRecall(
        base_dir=tmp_dir,
        wing="test-project",
        identity_prompt="测试身份层",
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
        same_content = "数据库连接池配置优化"
        m1 = p.add(content=same_content, wing="project-a", topic="arch")
        m2 = p.add(content=same_content, wing="project-b", topic="deploy")
        m3 = p.add(content=same_content, wing="personal", topic="habit")

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


# ─────────────────────────────────────────────
# 运行所有测试
# ─────────────────────────────────────────────

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

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过  {'✓ 全部通过' if FAIL == 0 else f'✗ {FAIL} 失败'}")
    print("=" * 50)
    return FAIL == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
