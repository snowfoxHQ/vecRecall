"""v2.3 回归：记忆卡片门槛插件（移植自 HMR memory_card）。"""
from __future__ import annotations

import os
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, VecRecall,
)
from vecrecall.mcp.server import MCPServer  # noqa: E402
from vecrecall.plugins.memory_card import (  # noqa: E402
    MemoryCard, enable_memory_cards, is_card, list_cards, parse_card,
    render_card, validate_card,
)

PASS = 0
FAIL = 0


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def make(d: str, owner: str = "") -> VecRecall:
    return VecRecall(base_dir=d, wing="t", owner=owner,
                     embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend())


VALID = """CODE_AGENT_MEMORY
repo: snowfoxHQ/vecRecall
scope: vecrecall/core/engine.py::search
status: verified
signal: test_core 257/257 通过
problem: search 默认 min_score=0 丢弃负分结果
technical_context: 词法哈希嵌入的余弦相似度可以为负
surfaces: search / _deep_search / mp_search
failed_shape: 只改了 _deep_search，漏了 search 的默认值
validation: python tests/test_core.py
principle: 检索默认返回 top-n，阈值由调用方显式给出
anchors: tests/test_core.py::test_search_returns_topn_with_negative_scores"""

UNVERIFIED = """CODE_AGENT_MEMORY
repo: snowfoxHQ/vecRecall
status: verified
signal: unknown
problem: 没有实测过的修复
principle: 先验证再入库"""

FAILED = """CODE_AGENT_MEMORY
repo: snowfoxHQ/vecRecall
status: failed_attempt
problem: search 默认 min_score=0 丢弃负分结果
failed_shape: 只改了 _deep_search，漏了 search 的默认值"""


def test_parse_and_validate() -> None:
    print("\n[parse / validate]")
    test("识别卡片", is_card(VALID) and is_card("\n\n  " + VALID))
    test("普通文本不是卡片", not is_card("今天开会讨论 CODE_AGENT_MEMORY") and not is_card(""))
    c = parse_card(VALID)
    test("解析字段", c and c.status == "verified" and c.repo == "snowfoxHQ/vecRecall")
    test("值里的冒号保留", parse_card(VALID).problem.startswith("search 默认 min_score=0"))
    test("合格卡片通过", validate_card(c).ok and not validate_card(c).warnings)
    test("渲染后可再次解析", parse_card(render_card(c)) == c)

    u = validate_card(parse_card(UNVERIFIED))
    test("verified 缺 validation 被拒", any("validation" in e for e in u.errors), str(u.errors))
    test("verified 的 signal=unknown 被拒", any("signal" in e for e in u.errors))
    f = validate_card(MemoryCard(status="failed_attempt", problem="x"))
    test("failed_attempt 缺 failed_shape 被拒", any("failed_shape" in e for e in f.errors))
    test("problem 为空被拒", not validate_card(MemoryCard(status="candidate")).ok)
    test("未知状态被拒", not validate_card(MemoryCard(status="done", problem="x")).ok)
    w = validate_card(MemoryCard(status="candidate", problem="x"))
    test("缺 principle / anchors 只警告", w.ok and len(w.warnings) == 2, str(w.warnings))
    test("状态大小写不敏感", parse_card("CODE_AGENT_MEMORY\nstatus: Verified\nproblem: x").status
         == "verified")
    test("忽略未知字段与注释",
         parse_card("CODE_AGENT_MEMORY\n# 备注\nfoo: bar\nproblem: x").problem == "x")
    test("没有已知字段返回 None", parse_card("CODE_AGENT_MEMORY\nfoo: bar") is None)


def test_gate_strict() -> None:
    print("\n[gate strict]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        gate = enable_memory_cards(p)
        m = p.add(VALID)
        test("合格卡片入库", m.metadata.get("source") == "memory_card")
        test("结构化字段写入 metadata", m.metadata["card"]["repo"] == "snowfoxHQ/vecRecall"
             and m.metadata["card_status"] == "verified")
        test("证据提取", "python tests/test_core.py" in m.metadata["evidence"])
        test("默认 topic 与 importance", m.topic == "code_memory" and m.importance == 0.8)
        test("去重标记不落盘", "_no_dedup" not in p._kg.get(m.id).metadata)

        n_before = p._kg.count()
        try:
            p.add(UNVERIFIED)
            test("未验证的正面经验被拒绝", False)
        except ValueError as e:
            test("未验证的正面经验被拒绝", "validation" in str(e), str(e))
        test("被拒绝的卡片没有落库", p._kg.count() == n_before)
        try:
            p.add("CODE_AGENT_MEMORY\nfoo: bar")
            test("无法解析的卡片被拒绝", False)
        except ValueError:
            test("无法解析的卡片被拒绝", True)

        plain = p.add("周末计划去爬山")
        test("普通内容不受影响", plain.topic == "general"
             and "source" not in plain.metadata)

        forged = p.add(VALID, metadata={
            "card_status": "candidate", "source": "forged", "evidence": []})
        test("metadata 不能覆盖卡片字段", forged.metadata["source"] == "memory_card"
             and forged.metadata["card_status"] == "verified", str(forged.metadata))
        test("显式 topic / importance 保留",
             p.add(VALID, topic="incident", importance=0.95).topic == "incident")

        fail = p.add(FAILED)
        test("同一问题的失败卡片不与已验证卡片合并",
             fail.id != m.id and fail.metadata["card_status"] == "failed_attempt")
        test("统计", gate.stats()["accepted"] >= 4 and gate.stats()["rejected"] == 2,
             str(gate.stats()))
        p.close()


def test_gate_lenient() -> None:
    print("\n[gate lenient]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        gate = enable_memory_cards(p, strict=False)
        m = p.add(UNVERIFIED)
        test("宽松模式降级为 candidate", m.metadata["card_status"] == "candidate")
        test("记录降级前的状态", m.metadata.get("card_downgraded_from") == "verified")
        test("正文里的状态也改为 candidate", "status: candidate" in m.content)
        test("candidate 默认 importance", m.importance == 0.4)
        try:
            p.add("CODE_AGENT_MEMORY\nstatus: verified\nsignal: ok\nvalidation: x")
            test("宽松模式下 problem 为空仍被拒", False)
        except ValueError:
            test("宽松模式下 problem 为空仍被拒", True)
        test("降级计数", gate.stats()["downgraded"] == 1)
        p.close()


def test_list_and_entrypoints() -> None:
    print("\n[list / entry points]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        enable_memory_cards(p)
        p.add(VALID)
        p.add(FAILED)
        p.add(VALID.replace("snowfoxHQ/vecRecall", "snowfoxHQ/HMR"))
        p.add("普通记忆")
        test("列出全部卡片", len(list_cards(p)) == 3)
        test("按状态过滤", len(list_cards(p, status="failed_attempt")) == 1)
        test("按仓库过滤", len(list_cards(p, repo="snowfoxHQ/HMR")) == 1)
        hits = p.search("min_score 负分", n=5)
        test("卡片可以被检索到", any(h.memory.metadata.get("source") == "memory_card" for h in hits))
        p.close()

    # 门槛对 MCP 写入同样生效
    with tempfile.TemporaryDirectory() as d:
        mcp = MCPServer(base_dir=d, wing="t", embedding_backend=BagOfWordsEmbeddingBackend(),
                        vector_backend=NumpyVectorBackend())
        enable_memory_cards(mcp._palace)
        try:
            mcp._call("mp_add", {"content": UNVERIFIED})
            test("MCP 写入同样受门槛约束", False)
        except ValueError:
            test("MCP 写入同样受门槛约束", True)
        mcp._palace.close()

    # 可见性隔离
    with tempfile.TemporaryDirectory() as d:
        pa = make(d, owner="agent-a")
        enable_memory_cards(pa)
        pa.add(VALID, owner="agent-a")
        pa.close()
        pb = make(d, owner="agent-b")
        test("他人的私有卡片不可见", list_cards(pb) == [])
        pb.close()


def run_all() -> bool:
    test_parse_and_validate()
    test_gate_strict()
    test_gate_lenient()
    test_list_and_entrypoints()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
