"""
VecRecall Blockchain — 测试套件

验证：
  1. Block 数据结构和哈希生成
  2. BlockGroup 大区块组合
  3. BlockChain 哈希链完整性
  4. 关键词提取（中英文）
  5. 自动大区块组合（4个小区块→1个L1）
  6. 按日期+关键词检索
  7. 三个平台 Hook（OpenClaw/Hermes/Claude Code）
  8. 新窗口上下文注入
  9. 哈希链防篡改验证
"""

import sys, os, tempfile, time, json
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from vecrecall.blockchain.block import Block, BlockGroup
from vecrecall.blockchain.chain import BlockChain, GENESIS_HASH
from vecrecall.blockchain.indexer import extract_keywords, extract_from_messages
from vecrecall.blockchain.hooks import (
    OpenClawBlockchainHook, HermesBlockchainHook,
    ClaudeCodeBlockchainHook, create_hook,
)

PASS = FAIL = 0

def test(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")

def make_chain(tmp_dir):
    return BlockChain(
        db_path=os.path.join(tmp_dir, "chain.db"),
        group_size=4,
        group_size_l2=4,
    )

SAMPLE_MESSAGES = [
    {"role": "user", "content": "我们讨论一下数据库架构的决策"},
    {"role": "assistant", "content": "建议使用 PostgreSQL，JSON 支持更好，我们决定立即迁移"},
    {"role": "user", "content": "好的，deploy 什么时候开始？"},
    {"role": "assistant", "content": "下周一开始 migration，这是关键的 architecture decision"},
]

# ─────────────────────────────────────────────

def test_block_hash():
    print("\n── Block 哈希生成 ──")
    b = Block.create(
        index=0, prev_hash=GENESIS_HASH,
        content="测试区块内容 test block content",
        wing="test", trigger="manual",
    )
    test("区块有哈希", bool(b.hash))
    test("哈希长度 64", len(b.hash) == 64)
    test("哈希验证通过", b.verify())
    test("block_id 非空", bool(b.block_id))
    test("date_label 格式正确", len(b.date_label) == 10)
    test("token_count > 0", b.token_count > 0)

    # 篡改检测
    b.content = "篡改后的内容"
    test("篡改后哈希验证失败", not b.verify())


def test_block_serialization():
    print("\n── Block 序列化 ──")
    b = Block.create(
        index=1, prev_hash="abc" * 20,
        content="序列化测试", wing="test",
        keywords=["序列化", "测试", "blockchain"],
        trigger="auto_75",
    )
    d = b.to_dict()
    b2 = Block.from_dict(d)
    test("to_dict 包含所有字段", all(k in d for k in ["block_id", "hash", "content", "keywords"]))
    test("from_dict 还原 block_id", b2.block_id == b.block_id)
    test("from_dict 还原 hash", b2.hash == b.hash)
    test("from_dict 还原 keywords", b2.keywords == b.keywords)
    test("from_dict 哈希验证通过", b2.verify())


def test_blockchain_add_and_chain():
    print("\n── 哈希链：添加区块 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)

        b0 = chain.new_block("第一个区块", wing="test", trigger="manual")
        test("区块 0 prev_hash 是创世哈希", b0.prev_hash == GENESIS_HASH)
        test("区块 0 index=0", b0.index == 0)

        b1 = chain.new_block("第二个区块", wing="test", trigger="auto_75")
        test("区块 1 prev_hash = 区块 0 的 hash", b1.prev_hash == b0.hash)
        test("区块 1 index=1", b1.index == 1)

        b2 = chain.new_block("第三个区块", wing="test", trigger="session_end")
        test("区块 2 prev_hash = 区块 1 的 hash", b2.prev_hash == b1.hash)

        ok, msg = chain.verify_chain("test")
        test("哈希链验证通过", ok, msg)
        test("验证消息包含区块数", "3" in msg)

        chain.close()


def test_blockchain_verify_tampering():
    print("\n── 哈希链：篡改检测 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)
        chain.new_block("区块 A", wing="test")
        chain.new_block("区块 B", wing="test")

        # 直接篡改数据库内容
        chain._conn.execute(
            "UPDATE blocks SET content='被篡改的内容' WHERE idx=0"
        )
        chain._conn.commit()

        ok, msg = chain.verify_chain("test")
        test("篡改后哈希链验证失败", not ok)
        test("错误消息指向被篡改区块", "篡改" in msg or "失败" in msg)
        chain.close()


def test_keyword_extraction():
    print("\n── 关键词提取（中英文）──")
    text = "我们决定采用 PostgreSQL 数据库架构，deploy 计划下周开始 migration"
    kws = extract_keywords(text)
    test("提取到关键词", len(kws) > 0)
    test("提取到英文关键词", any(k in kws for k in ["postgresql", "deploy", "migration"]))
    test("提取到中文关键词", any(k in kws for k in ["数据库", "架构", "决定"]))
    test("关键词数量合理（≤15）", len(kws) <= 15)

    # 纯英文
    en_text = "Critical architecture decision: use Redis for session storage"
    en_kws = extract_keywords(en_text)
    test("纯英文关键词提取正常", len(en_kws) > 0)
    test("高权重词 architecture 被提取", "architecture" in en_kws)

    # 消息列表
    msg_kws = extract_from_messages(SAMPLE_MESSAGES)
    test("消息列表关键词提取正常", len(msg_kws) > 0)


def test_search_by_keywords():
    print("\n── 按关键词检索 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)

        chain.new_block("PostgreSQL 数据库迁移方案", wing="test",
                        keywords=["postgresql", "数据库", "迁移"])
        chain.new_block("Redis 缓存架构设计", wing="test",
                        keywords=["redis", "缓存", "架构"])
        chain.new_block("Docker 部署配置", wing="test",
                        keywords=["docker", "部署", "deploy"])

        # 搜索数据库相关
        results = chain.search_by_keywords(["数据库", "postgresql"], wing="test")
        test("关键词搜索返回结果", len(results) > 0)
        test("搜索结果包含正确区块", any("PostgreSQL" in b.content for b in results))

        # 搜索部署相关
        results2 = chain.search_by_keywords(["docker", "deploy"], wing="test")
        test("部署关键词搜索正确", len(results2) > 0)

        # 日期过滤
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        results3 = chain.search_by_keywords(["架构"], wing="test",
                                             date_start=today, date_end=today)
        test("日期过滤正常", len(results3) > 0)

        chain.close()


def test_auto_grouping():
    print("\n── 自动大区块组合 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)  # group_size=4

        # 添加 3 个区块，还不够 4 个，不触发组合
        for i in range(3):
            chain.new_block(f"区块内容 {i}", wing="test",
                            keywords=[f"keyword{i}"])
        stats = chain.stats("test")
        test("3 个区块时不触发 L1 组合", stats["groups_l1"] == 0)

        # 添加第 4 个，触发 L1 组合
        chain.new_block("第四个区块", wing="test", keywords=["fourth"])
        stats2 = chain.stats("test")
        test("4 个区块后自动生成 L1 大区块", stats2["groups_l1"] == 1,
             f"groups_l1={stats2['groups_l1']}")

        # 被归组的区块 group_id 非空
        blocks = chain.get_blocks_by_wing("test", limit=10)
        grouped = [b for b in blocks if b.group_id]
        test("归组后区块 group_id 非空", len(grouped) == 4,
             f"grouped={len(grouped)}")

        chain.close()


def test_openclaw_hook():
    print("\n── OpenClaw Hook ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)
        hook = OpenClawBlockchainHook(chain=chain, wing="openclaw",
                                       context_window_size=1000)

        # 正常存档
        result = hook.on_before_compaction(
            SAMPLE_MESSAGES, session_id="sess-oc-001", token_count=800
        )
        test("on_before_compaction 返回 block_id", bool(result.get("block_id")))
        test("on_before_compaction 有 log", bool(result.get("log")))
        test("区块已存入链", chain.stats("openclaw")["total_blocks"] == 1)

        # 空消息不存档
        result2 = hook.on_before_compaction([], session_id="sess-empty")
        test("空消息不生成区块", result2.get("block_id") is None)

        # 75% 自动触发检测
        triggered = hook.check_auto_trigger(
            SAMPLE_MESSAGES, token_count=800, session_id="sess-auto"
        )
        test("token 800 / 窗口 1000 = 80% 触发自动存档", triggered is not None)

        not_triggered = hook.check_auto_trigger(
            SAMPLE_MESSAGES, token_count=500, session_id="sess-no"
        )
        test("token 500 / 窗口 1000 = 50% 不触发", not_triggered is None)

        # 历史上下文注入
        ctx = hook.get_context_for_new_window(keywords=["数据库"])
        test("历史上下文非空", bool(ctx))
        test("上下文包含区块信息", "区块" in ctx)

        chain.close()


def test_hermes_hook():
    print("\n── Hermes Hook ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)
        hook = HermesBlockchainHook(chain=chain, wing="hermes")

        result = hook.on_precompact(SAMPLE_MESSAGES, session_id="sess-h-001")
        test("on_precompact 返回 block_id", bool(result.get("block_id")))
        test("on_precompact 返回 inject_context", "inject_context" in result)

        result2 = hook.summarize_session(SAMPLE_MESSAGES, session_id="sess-h-002")
        test("summarize_session 返回 summary", bool(result2.get("summary")))
        test("summary 包含区块信息", "区块" in result2.get("summary", ""))
        test("两次存档均入链", chain.stats("hermes")["total_blocks"] == 2)

        chain.close()


def test_claude_code_hook():
    print("\n── Claude Code Hook ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)
        hook = ClaudeCodeBlockchainHook(chain=chain, wing="claude")

        # 手动存档
        result = hook.archive(SAMPLE_MESSAGES, session_id="sess-cc-001")
        test("archive 返回 ok=True", result.get("ok") is True)
        test("archive 返回 block_id", bool(result.get("block_id")))
        test("archive 返回 keywords", len(result.get("keywords", [])) > 0)
        test("archive 返回 hash 预览", "..." in result.get("hash", ""))

        # 获取上下文
        ctx = hook.get_context(query="数据库架构")
        test("get_context 返回字符串", isinstance(ctx, str))

        # 检索
        results = hook.search(keywords=["数据库", "架构"])
        test("search 返回结果列表", isinstance(results, list))

        # 统计
        stats = hook.stats()
        test("stats 返回 total_blocks=1", stats["total_blocks"] == 1)

        # 验证
        verify = hook.verify()
        test("verify 返回 ok=True", verify.get("ok") is True)

        # 日期列表
        dates = hook.list_dates()
        test("list_dates 返回日期列表", len(dates) > 0)

        chain.close()


def test_create_hook_factory():
    print("\n── create_hook 工厂函数 ──")

    def make_config(d, name):
        return {
            "db_path": os.path.join(d, f"{name}.db"),
            "wing": "factory-test",
            "context_window_size": 500_000,
            "auto_trigger_threshold": 0.8,
        }

    with tempfile.TemporaryDirectory() as d:
        oc = create_hook("openclaw", make_config(d, "oc"))
        test("创建 OpenClaw hook", isinstance(oc, OpenClawBlockchainHook))
        test("wing 正确", oc._wing == "factory-test")
        test("窗口大小正确", oc._context_window_size == 500_000)
        oc._chain.close()

    with tempfile.TemporaryDirectory() as d:
        h = create_hook("hermes", make_config(d, "h"))
        test("创建 Hermes hook", isinstance(h, HermesBlockchainHook))
        h._chain.close()

    with tempfile.TemporaryDirectory() as d:
        cc = create_hook("claude_code", make_config(d, "cc"))
        test("创建 Claude Code hook", isinstance(cc, ClaudeCodeBlockchainHook))
        cc._chain.close()

    try:
        create_hook("unknown", {"db_path": ":memory:", "wing": "x"})
        test("未知平台应抛出异常", False)
    except ValueError:
        test("未知平台正确抛出 ValueError", True)


def test_multi_wing_isolation():
    print("\n── 多 Wing 隔离 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)

        chain.new_block("OpenClaw 的上下文记录", wing="openclaw")
        chain.new_block("Hermes 的上下文记录", wing="hermes")
        chain.new_block("Claude Code 的上下文记录", wing="claude")

        oc_blocks = chain.get_blocks_by_wing("openclaw")
        h_blocks = chain.get_blocks_by_wing("hermes")
        cc_blocks = chain.get_blocks_by_wing("claude")

        test("OpenClaw wing 只有自己的区块",
             all(b.wing == "openclaw" for b in oc_blocks))
        test("Hermes wing 只有自己的区块",
             all(b.wing == "hermes" for b in h_blocks))
        test("各 wing 区块数各为 1",
             len(oc_blocks) == len(h_blocks) == len(cc_blocks) == 1)

        # 各 wing 有独立的哈希链
        ok_oc, _ = chain.verify_chain("openclaw")
        ok_h, _ = chain.verify_chain("hermes")
        test("各 wing 哈希链独立验证通过", ok_oc and ok_h)

        chain.close()


def test_stats_and_dates():
    print("\n── 统计和日期索引 ──")
    with tempfile.TemporaryDirectory() as d:
        chain = make_chain(d)
        for i in range(3):
            chain.new_block(f"测试区块 {i}", wing="test",
                            keywords=[f"tag{i}", "测试"])

        stats = chain.stats("test")
        test("total_blocks=3", stats["total_blocks"] == 3)
        test("total_tokens > 0", stats["total_tokens"] > 0)
        test("db_size_kb > 0", stats["db_size_kb"] > 0)

        dates = chain.list_dates("test")
        test("日期列表非空", len(dates) > 0)
        test("日期格式正确", all(len(d) == 10 for d in dates))

        chain.close()


# ─────────────────────────────────────────────

def run_all():
    print("=" * 55)
    print("VecRecall Blockchain — 测试套件")
    print("=" * 55)

    test_block_hash()
    test_block_serialization()
    test_blockchain_add_and_chain()
    test_blockchain_verify_tampering()
    test_keyword_extraction()
    test_search_by_keywords()
    test_auto_grouping()
    test_openclaw_hook()
    test_hermes_hook()
    test_claude_code_hook()
    test_create_hook_factory()
    test_multi_wing_isolation()
    test_stats_and_dates()

    print("\n" + "=" * 55)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过  {'✓ 全部通过' if FAIL == 0 else f'✗ {FAIL} 失败'}")
    print("=" * 55)
    return FAIL == 0

if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
