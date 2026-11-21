"""阶段2 回归：检索决策、查询意图、三项验收、MCP 入口。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend,
    NumpyVectorBackend,
    VecRecall,
)
from vecrecall.core.recall_policy import (  # noqa: E402
    build_query_intent,
    decide_recall,
    evaluate_memory,
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


def test_decision() -> None:
    print("\n[decision]")
    d = decide_recall("你好")
    test("greeting skipped", not d.should_recall and d.reason == "self_contained_or_trivial", d.as_dict().__str__())

    d = decide_recall("之前那个 API schema 迁移为什么失败了？")
    test("history + decision triggers recall", d.should_recall, d.as_dict().__str__())
    test("history signal recorded", "history_dependency" in d.signals, str(d.signals))

    d = decide_recall("之前的 parser 改动", context="parser 改动已经完成并验证")
    test("known anchor with no decision skips", not d.should_recall, d.as_dict().__str__())

    d = decide_recall("修复这个 typo", context=None)
    test("skip phrase wins without decision", not d.should_recall, d.as_dict().__str__())

    d = decide_recall("api 设计需要考虑什么", decider=lambda _q, _c: 0.9)
    test("decider override positive", d.should_recall and d.reason == "model_decision", d.as_dict().__str__())
    d = decide_recall("之前的设计理由是什么", decider=lambda _q, _c: 0.1)
    test("decider override negative", not d.should_recall, d.as_dict().__str__())

    def broken(_q, _c):
        raise RuntimeError("offline")

    d = decide_recall("之前的修复为什么失败", decider=broken)
    test("decider failure falls back", d.should_recall, d.as_dict().__str__())

    intent = build_query_intent("之前那个 API schema 迁移失败")
    test("query intent structured", ":" in intent and "？" in intent, intent)


def test_acceptance() -> None:
    print("\n[three checks]")
    good = {
        "content": "api schema migration timeout: root cause was stale index; fix by reindex; verify with explain",
    }
    result = evaluate_memory(good, "api schema migration timeout")
    test("all three checks pass", result.accepted and all(result.checks.values()), result.as_dict().__str__())

    weak = {"content": "api schema migration happened"}
    result = evaluate_memory(weak, "api schema migration timeout")
    test("missing actionability rejected", not result.accepted and not result.checks["actionable"], result.as_dict().__str__())

    result = evaluate_memory("完全无关的一句话", "api schema migration timeout")
    test("unrelated memory rejected", not result.accepted, result.as_dict().__str__())


def test_engine_and_mcp() -> None:
    print("\n[engine + mcp]")
    with tempfile.TemporaryDirectory() as tmp:
        engine = VecRecall(
            tmp,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        engine.add(
            "api schema migration timeout: root cause was stale index; "
            "fix by reindex; verify with explain",
            topic="工程经验",
        )
        results = engine.search("api schema migration timeout", acceptance_query="api schema migration timeout")
        test("acceptance attached", bool(results) and results[0].acceptance is not None)
        test("acceptance has three checks", set(results[0].acceptance["checks"]) == {
            "same_boundary", "same_behavior", "actionable",
        }, str(results[0].acceptance))
        engine.close()

    from vecrecall.mcp.server import MCPServer, TOOLS
    names = {tool["name"] for tool in TOOLS}
    test("mp_recall_policy registered", "mp_recall_policy" in names)
    test("mp_accept_memory registered", "mp_accept_memory" in names)
    test("mp_search exposes acceptance_query", any(
        "acceptance_query" in tool["inputSchema"]["properties"]
        for tool in TOOLS if tool["name"] == "mp_search"
    ))

    with tempfile.TemporaryDirectory() as tmp:
        server = object.__new__(MCPServer)
        server._palace = VecRecall(
            tmp,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        out = server._call("mp_recall_policy", {"query": "之前那个 API 迁移为什么失败"})
        test("mcp policy returns decision", out.get("should_recall") is True, str(out))
        out = server._call("mp_accept_memory", {
            "candidate": '{"content": "api schema migration timeout: fix by reindex; verify with explain"}',
            "query": "api schema migration timeout",
        })
        test("mcp acceptance returns verdict", out.get("accepted") is True, str(out))
        server._palace.close()


def run_all() -> bool:
    test_decision()
    test_acceptance()
    test_engine_and_mcp()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
