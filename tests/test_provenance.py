"""v2.5 来源追踪：写入来源、合并记录、蒸馏来源、各入口标识、旧数据兼容。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, VecRecall,
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


def make(d, **kw):
    kw.setdefault("dedup", False)
    return VecRecall(base_dir=d, wing="pv", embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend(), **kw)


def test_basic():
    print("\n[写入来源]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d, owner="alice")
        m = p.add("我们决定用 PostgreSQL 做主库", session_id="s-1")
        pv = m.metadata.get("provenance", {})
        test("默认入口 python", pv.get("via") == "python", str(pv))
        test("记录 agent（实例 owner）", pv.get("agent") == "alice", str(pv))
        test("记录 session_id", pv.get("session_id") == "s-1", str(pv))
        test("记录 created_at", isinstance(pv.get("created_at"), float))

        # shared：实例 owner 是 alice，bob 的 private 记忆 alice 本来就看不到
        m2 = p.add("评审意见：索引要加", owner="bob", visibility="shared",
                   provenance={"source_ref": "PR#42", "channel": "github", "via": "webhook"})
        pv2 = m2.metadata["provenance"]
        test("owner 参数优先于实例 owner", pv2.get("agent") == "bob", str(pv2))
        test("调用方补充字段", pv2.get("source_ref") == "PR#42" and pv2.get("channel") == "github")
        test("调用方可覆盖 via", pv2.get("via") == "webhook")

        m3 = p.add("x 内容", provenance={"created_at": 1.0})
        test("created_at 由引擎维护，不能伪造", m3.metadata["provenance"]["created_at"] > 1.0)

        m4 = p.add("y 内容", metadata={"provenance": {"source_ref": "doc.md"}})
        test("metadata.provenance 也会合并", m4.metadata["provenance"].get("source_ref") == "doc.md")
        try:
            p.add("z", provenance="bad")
            test("provenance 类型校验", False)
        except TypeError:
            test("provenance 类型校验", True)

        test("持久化后可读回", p._kg.get(m2.id).metadata["provenance"]["source_ref"] == "PR#42")
        pq = p.provenance(m2.id)
        test("provenance() 返回 origin", pq["origin"]["source_ref"] == "PR#42" and pq["occurrences"] == 1)
        p.close()


def test_archive():
    print("\n[会话存档]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        ms = p.archive_session([{"role": "user", "content": "我想周末去爬山"},
                                {"role": "assistant", "content": "推荐香山"}], session_id="chat-9")
        pv = [m.metadata["provenance"] for m in ms]
        test("每轮记录轮次", [x.get("turn_index") for x in pv] == [0, 1], str(pv))
        test("每轮记录角色", [x.get("role") for x in pv] == ["user", "assistant"])
        test("共用 session_id", all(x.get("session_id") == "chat-9" for x in pv))
        p.close()


def test_merge():
    print("\n[去重合并]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d, dedup=True)
        a = p.add("决定采用微服务架构", session_id="s-a", provenance={"agent": "architect"})
        b = p.add("决定采用微服务架构", session_id="s-b", provenance={"agent": "reviewer"})
        test("合并到同一条", a.id == b.id)
        pv = p.provenance(a.id)
        test("首次来源保留", pv["origin"]["agent"] == "architect" and pv["origin"]["session_id"] == "s-a")
        test("重复来源记入 merged_from", len(pv["merged_from"]) == 1
             and pv["merged_from"][0].get("agent") == "reviewer"
             and pv["merged_from"][0].get("session_id") == "s-b", str(pv["merged_from"]))
        test("occurrences 计数", pv["occurrences"] == 2)

        p.PROVENANCE_MAX_MERGES = 2
        for i in range(4):
            p.add("决定采用微服务架构", session_id=f"s-{i}")
        pv = p.provenance(a.id)
        test("merged_from 有上限", len(pv["merged_from"]) == 2, str(len(pv["merged_from"])))
        test("超出部分计数", pv.get("merged_dropped") == 3, str(pv.get("merged_dropped")))
        test("occurrences 仍完整", pv["occurrences"] == 6)
        p.close()


def test_relations():
    print("\n[取代与蒸馏]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        old = p.add("我们决定用 PostgreSQL 做主库")
        new = p.add("主库从 PostgreSQL 换成 MySQL")
        test("provenance 含 superseded_by", p.provenance(old.id)["superseded_by"] == new.id)
        test("provenance 含 supersedes", p.provenance(new.id)["supersedes"] == [old.id])

        ids = [p.add(f"碎片笔记 {i}", topic="frag", importance=0.3).id for i in range(3)]
        plans = p.distill(topic="frag", dry_run=False)
        dist = [m for m in p._kg.iter_all() if m.metadata.get("distilled")]
        test("蒸馏产生一条", len(plans) == 1 and len(dist) == 1)
        pv = p.provenance(dist[0].id)
        test("蒸馏记忆 via=distill", pv["origin"].get("via") == "distill", str(pv["origin"]))
        test("蒸馏记忆 derived_from", sorted(pv["derived_from"]) == sorted(ids))
        test("源记忆 distilled_into", p.provenance(ids[0]).get("distilled_into") == dist[0].id)
        p.close()


def test_legacy_and_visibility():
    print("\n[旧数据与可见性]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        m = p.add("旧版本写入的记忆", session_id="old-s")
        m = p._kg.get(m.id)
        m.metadata.pop("provenance")
        p._kg.save(m)
        pv = p.provenance(m.id)
        test("旧记忆标记 legacy", pv["origin"].get("legacy") is True)
        test("旧记忆从字段推断 session", pv["origin"].get("session_id") == "old-s")
        try:
            p.provenance("not-exist")
            test("不存在时报错", False)
        except ValueError:
            test("不存在时报错", True)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        alice = make(d, owner="alice")
        m = alice.add("alice 的私密记忆", owner="alice", visibility="private")
        bob = make(d, owner="bob")
        try:
            bob.provenance(m.id)
            test("看不到的 private 记忆不泄露来源", False)
        except ValueError:
            test("看不到的 private 记忆不泄露来源", True)
        alice.close()
        bob.close()


def test_entrypoints():
    print("\n[入口标识]")
    from vecrecall.mcp.server import MCPServer
    with tempfile.TemporaryDirectory() as d:
        s = MCPServer(base_dir=d, wing="pv", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=NumpyVectorBackend())
        r = s._call("mp_add", {"content": "通过 MCP 写入", "provenance": {"source_ref": "chat-1"}})
        pv = s._call("mp_provenance", {"memory_id": r["id"]})
        test("MCP 入口 via=mcp", pv["origin"].get("via") == "mcp", json.dumps(pv["origin"], ensure_ascii=False))
        test("MCP 透传 provenance", pv["origin"].get("source_ref") == "chat-1")
        r = s._call("mp_diary_write", {"agent_name": "reviewer", "content": "发现 SQL 注入"})
        pv = s._call("mp_provenance", {"memory_id": r["id"]})
        test("日记记录 agent 与 kind", pv["origin"].get("agent") == "reviewer"
             and pv["origin"].get("kind") == "diary")
        s._palace.close()


if __name__ == "__main__":
    test_basic()
    test_archive()
    test_merge()
    test_relations()
    test_legacy_and_visibility()
    test_entrypoints()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
