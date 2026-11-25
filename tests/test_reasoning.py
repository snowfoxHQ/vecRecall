"""v2.3 回归：推理链与反思（移植自 HMR ThoughtChain）。"""
from __future__ import annotations

import os
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, SqliteVectorBackend, VecRecall,
)
from vecrecall.reasoning import (  # noqa: E402
    INSIGHT_TOPIC, ChainStatus, ReasoningEngine, Reflector, ThoughtType,
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


def make(d: str, owner: str = "", persistent: bool = False) -> VecRecall:
    vec = SqliteVectorBackend(os.path.join(d, "vectors.db")) if persistent else NumpyVectorBackend()
    return VecRecall(base_dir=d, wing="t", owner=owner,
                     embedding_backend=BagOfWordsEmbeddingBackend(), vector_backend=vec)


def ipc_chain(eng, rating=0.9, outcome="延迟从 800ms 降到 50ms"):
    c = eng.start("排查 IPC 队列超时", expected_outcome=None, agent_id="ops")
    eng.think(c.chain_id, "队列深度持续超过 800", ThoughtType.OBSERVATION)
    eng.think(c.chain_id, "消费者线程不足", ThoughtType.HYPOTHESIS)
    eng.think(c.chain_id, "扩容消费者线程 x3", ThoughtType.DECISION)
    eng.think(c.chain_id, "已部署", ThoughtType.ACTION)
    return c, eng.reflect(c.chain_id, outcome, rating=rating)


def test_lifecycle() -> None:
    print("\n[lifecycle]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        eng = ReasoningEngine(p)
        c = eng.start("排查 IPC 队列超时", agent_id="ops")
        test("新链状态 open", c.status == ChainStatus.OPEN.value)
        t1 = eng.think(c.chain_id, "队列深度持续超过 800", ThoughtType.OBSERVATION)
        t2 = eng.think(c.chain_id, "消费者线程不足", "hypothesis")
        test("字符串类型也可以", t2 and t2.thought_type == "hypothesis")
        test("parent_id 自动接上一节点", t2.parent_id == t1.thought_id)
        eng.think(c.chain_id, "扩容消费者线程 x3", ThoughtType.DECISION)
        eng.think(c.chain_id, "已部署", ThoughtType.ACTION)
        test("行动后状态 active", eng.get(c.chain_id).status == ChainStatus.ACTIVE.value)

        r = eng.reflect(c.chain_id, "延迟从 800ms 降到 50ms", rating=0.9)
        got = eng.get(c.chain_id)
        test("反思后关闭", got.status == ChainStatus.CLOSED.value and got.closed_at)
        test("高分：决策判为正确", all(t.was_correct for t in got.by_type("decision")))
        test("观察节点不评判", all(t.was_correct is None for t in got.by_type("observation")))
        test("准确率 100%", r.accuracy == 1.0, str(r.accuracy))
        test("高分有可写入的结论", r.suggested_memory and "扩容消费者线程" in r.suggested_memory)

        test("关闭后不能再追加", eng.think(c.chain_id, "x") is None)
        test("关闭后不能重复反思", eng.reflect(c.chain_id, "again") is None)
        test("不存在的链", eng.think("nope", "x") is None and eng.get("nope") is None)
        try:
            eng.start("   ")
            test("空目标被拒绝", False)
        except ValueError:
            test("空目标被拒绝", True)
        try:
            eng.think(eng.start("另一个目标").chain_id, "x", "guess")
            test("非法节点类型被拒绝", False)
        except ValueError:
            test("非法节点类型被拒绝", True)
        eng.close(); p.close()


def test_insight_written_to_main_store() -> None:
    print("\n[insight → main store]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        eng = ReasoningEngine(p)
        _c, r = ipc_chain(eng)
        test("结论写入主库", r.insight_memory_id is not None)
        m = p._kg.get(r.insight_memory_id)
        test("结论是 semantic 记忆", m and m.memory_type == "semantic" and m.topic == INSIGHT_TOPIC)
        test("结论带来源链", m.metadata.get("chain_id") == _c.chain_id)
        hits = p.search("IPC 队列超时 怎么处理", n=5)
        test("主库检索能找到结论", any(h.memory.id == r.insight_memory_id for h in hits))
        test("推理过程不进主库", all(h.memory.topic != "reasoning_chain" for h in hits))

        # 中等评分不写结论
        c2 = eng.start("调整日志级别")
        eng.think(c2.chain_id, "改为 warn", ThoughtType.DECISION)
        r2 = eng.reflect(c2.chain_id, "日志量少了一些", rating=0.6)
        test("中等评分不写入长期记忆", r2.suggested_memory is None and r2.insight_memory_id is None)
        eng.close(); p.close()


def test_low_rating() -> None:
    print("\n[low rating]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        eng = ReasoningEngine(p)
        _c, r = ipc_chain(eng, rating=0.1, outcome="扩容后仍然超时，最后回滚")
        got = eng.get(_c.chain_id)
        test("低分：决策判为错误", all(t.was_correct is False for t in got.by_type("decision")))
        test("低分：假设判为错误", all(t.was_correct is False for t in got.by_type("hypothesis")))
        test("低分：行动仍判为正确", all(t.was_correct for t in got.by_type("action")))
        test("记录主要错误", r.key_mistakes and "扩容消费者线程" in " ".join(r.key_mistakes))
        test("失败结论也写入长期记忆", r.insight_memory_id is not None)
        test("失败链不会被当成最佳决策", eng.best_decision_for("IPC 队列超时") is None)
        eng.close(); p.close()


def test_rating_inference() -> None:
    print("\n[rating inference]")
    rf = Reflector()
    test("无预期：成功词", rf.infer_rating(None, "部署成功，延迟降低") >= 0.9)
    test("无预期：失败词", rf.infer_rating(None, "仍然超时，失败") <= 0.2)
    test("无预期：中性", rf.infer_rating(None, "观察中") == 0.5)
    test("有预期：中文重叠可计算",
         rf.infer_rating("延迟降到 100ms 以下", "延迟降到 50ms") > 0.5,
         str(rf.infer_rating("延迟降到 100ms 以下", "延迟降到 50ms")))
    test("有预期：完全不相关", rf.infer_rating("延迟降低", "磁盘满了") == 0.0)


def test_similarity_and_best_decision() -> None:
    print("\n[similar / best decision]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        eng = ReasoningEngine(p)
        good, _ = ipc_chain(eng, rating=0.9)
        c = eng.start("前端页面白屏排查")
        eng.think(c.chain_id, "回滚到上一版本", ThoughtType.DECISION)
        eng.reflect(c.chain_id, "页面恢复正常", rating=0.95)
        open_chain = eng.start("IPC 队列超时第二次排查")      # 未关闭

        sim = eng.find_similar("IPC 队列积压超时")
        test("找到相似的已关闭链", sim and sim[0].chain_id == good.chain_id,
             str([x.goal for x in sim]))
        test("默认排除未关闭的链", all(x.chain_id != open_chain.chain_id for x in sim))
        test("不相关的链不返回", all(x.goal != "前端页面白屏排查" for x in sim))
        test("可包含未关闭的链",
             any(x.chain_id == open_chain.chain_id
                 for x in eng.find_similar("IPC 队列超时", closed_only=False, top_k=5)))
        test("完全不相关的目标返回空", eng.find_similar("周末去哪里爬山") == [])

        best = eng.best_decision_for("IPC 队列积压超时")
        test("最佳决策", best and best["decision"] == "扩容消费者线程 x3", str(best))
        test("最佳决策带准确率与来源", best["accuracy"] >= 0.7 and best["chain_id"] == good.chain_id)
        test("不相关目标没有最佳决策", eng.best_decision_for("数据库备份策略") is None)

        st = eng.stats()
        test("统计", st["chains"] == 3 and st["by_status"].get("closed") == 2, str(st))
        test("按 agent 过滤", [x.chain_id for x in eng.chains(agent_id="ops")] == [good.chain_id])
        eng.close(); p.close()


def test_persistence_and_isolation() -> None:
    print("\n[persistence / isolation]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d, persistent=True)
        eng = ReasoningEngine(p)
        c, _ = ipc_chain(eng)
        eng.close(); p.close()

        p2 = make(d, persistent=True)
        eng2 = ReasoningEngine(p2)
        got = eng2.get(c.chain_id)
        test("重启后链完整恢复", got and len(got.thoughts) == 4 and got.reflection, str(got))
        test("重启后仍能查到最佳决策",
             (eng2.best_decision_for("IPC 队列超时") or {}).get("decision") == "扩容消费者线程 x3")
        eng2.close(); p2.close()

    with tempfile.TemporaryDirectory() as d:
        pa = make(d, owner="agent-a", persistent=True)
        ea = ReasoningEngine(pa)
        priv, _ = ipc_chain(ea)
        ea.close(); pa.close()
        pb = make(d, owner="agent-b", persistent=True)
        eb = ReasoningEngine(pb)
        test("他人的私有链不可见", eb.get(priv.chain_id) is None)
        test("他人的私有链不出现在相似结果里", eb.find_similar("IPC 队列超时") == [])
        test("他人的私有链不出现在列表里", eb.chains() == [])
        eb.close(); pb.close()


def test_pending_insight_retry() -> None:
    print("\n[pending insight]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        eng = ReasoningEngine(p)

        def deny(fields):
            if fields.get("topic") == INSIGHT_TOPIC:
                raise RuntimeError("主库暂时不可写")
        p.hooks.register("before_write", deny)
        c, r = ipc_chain(eng)
        test("主库写失败不影响反思", r is not None and eng.get(c.chain_id).status == "closed")
        test("结论留在链上待补写", eng.get(c.chain_id).pending_insight and r.insight_memory_id is None)
        test("统计显示待补写", eng.stats()["pending_insights"] == 1)

        p.hooks.unregister("before_write", deny)
        test("补写成功", eng.retry_pending_insights() == 1)
        got = eng.get(c.chain_id)
        test("补写后清除待处理", got.pending_insight is None and got.reflection.insight_memory_id)
        test("补写只做一次", eng.retry_pending_insights() == 0)
        eng.close(); p.close()


def run_all() -> bool:
    test_lifecycle()
    test_insight_written_to_main_store()
    test_low_rating()
    test_rating_inference()
    test_similarity_and_best_decision()
    test_persistence_and_isolation()
    test_pending_insight_retry()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
