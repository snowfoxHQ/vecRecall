"""v2.3 回归：BM25 词法索引、RRF 融合、引擎 mode=bm25/rrf 与索引同步。"""
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
from vecrecall.core.lexical import BM25Index, rrf_merge, tokenize  # noqa: E402

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
    return VecRecall(base_dir=d, wing="lex", owner=owner,
                     embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend(), dedup=False)


def test_tokenize() -> None:
    print("\n[tokenize]")
    t = tokenize("The dogs were running to the boxes")
    test("去英文停用词", "the" not in t and "were" not in t and "to" not in t, str(t))
    test("复数归一 dogs→dog", "dog" in t, str(t))
    test("进行时归一 running→run", "run" in t, str(t))
    test("es 复数 boxes→box", "box" in t, str(t))
    test("ss 结尾不误删", tokenize("class") == ["class"], str(tokenize("class")))
    z = tokenize("我们的数据库连接池")
    test("中文二字组合", "数据" in z and "据库" in z and "连接" in z, str(z))
    test("中文虚字单字去掉", "的" not in z and "我" not in z, str(z))
    test("虚字参与的二字仍保留", "我们" in z, str(z))
    test("空输入", tokenize("") == [] and tokenize(None) == [])


def test_bm25() -> None:
    print("\n[bm25]")
    idx = BM25Index()
    idx.add("a", "PostgreSQL connection pool size is 100")
    idx.add("b", "We adopted a golden retriever puppy named Max")
    idx.add("c", "The connection to the office wifi keeps dropping")
    r = idx.search("connection pool", 3)
    test("双词命中排第一", r and r[0][0] == "a", str(r))
    test("只返回有命中的文档", {d for d, _ in r} == {"a", "c"}, str(r))
    test("无命中返回空", idx.search("kubernetes", 5) == [])
    test("分数降序", all(r[i][1] >= r[i + 1][1] for i in range(len(r) - 1)))

    idx.add("a", "Redis cache eviction policy")          # 替换
    test("替换后旧词不再命中", all(d != "a" for d, _ in idx.search("pool", 5)))
    test("替换后新词命中", idx.search("redis", 1)[0][0] == "a")
    test("替换不增加文档数", len(idx) == 3)
    idx.remove("a")
    test("删除后不再命中", idx.search("redis", 5) == [] and "a" not in idx)
    idx.remove("missing")                                # 不存在不报错
    test("删除不存在的 id 不报错", len(idx) == 2)

    # IDF：常见词权重低
    idx2 = BM25Index()
    for i in range(10):
        idx2.add(f"d{i}", "project status update meeting")
    idx2.add("rare", "project budget approved by finance")
    top = idx2.search("project finance", 1)
    test("IDF：稀有词决定排序", top and top[0][0] == "rare", str(top))


def test_rrf() -> None:
    print("\n[rrf]")
    r = rrf_merge([["a", "b", "c"], ["b", "c", "d"]], k=60)
    ids = [d for d, _ in r]
    test("两路都靠前的排第一", ids[0] == "b", str(ids))
    test("并集", set(ids) == {"a", "b", "c", "d"})
    test("分数按公式", abs(dict(r)["b"] - (1 / 62 + 1 / 61)) < 1e-12)
    test("同分按首次出现的路与名次", rrf_merge([["x"], ["y"]])[0][0] == "x")
    test("单路内重复 id 只计一次",
         abs(dict(rrf_merge([["a", "a", "b"]]))["b"] - 1 / 62) < 1e-12)
    test("空输入", rrf_merge([]) == [] and rrf_merge([[], []]) == [])


def test_engine_modes() -> None:
    print("\n[engine modes]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        a = p.add("PostgreSQL connection pool size is 100")
        b = p.add("We adopted a golden retriever puppy named Max")
        c = p.add("The office wifi keeps dropping every afternoon")

        res = p.search("connection pool", n=3, mode="bm25")
        test("bm25 命中", res and res[0].memory.id == a.id, str([r.memory.content for r in res]))
        test("bm25 只返回有命中的", len(res) == 1, str(len(res)))
        res = p.search("connection pool", n=3, mode="rrf")
        test("rrf 返回并集、首位正确", res[0].memory.id == a.id and len(res) == 3, str(len(res)))
        test("rrf 层标记", all(r.layer == "L3" for r in res))
        test("默认是 rrf（返回两路并集）", len(p.search("connection pool", n=3)) == 3)

        try:
            p.search("x", mode="fuzzy")
            test("非法 mode 被拒绝", False)
        except ValueError:
            test("非法 mode 被拒绝", True)

        # 索引同步：首次检索后构建，之后 add / update / delete 增量维护
        n_new = p.add("Kubernetes cluster upgrade scheduled for Friday")
        test("add 后可被 bm25 检到", p.search("kubernetes", n=1, mode="bm25")[0].memory.id == n_new.id)
        p.update(b.id, content="We adopted a tabby cat named Luna")
        test("update 后旧词不命中", not p.search("retriever", n=3, mode="bm25"))
        test("update 后新词命中", p.search("tabby", n=1, mode="bm25")[0].memory.id == b.id)
        p.delete(c.id)
        test("delete 后不命中", not p.search("wifi", n=3, mode="bm25"))
        p.reindex()
        test("reindex 后重建", p.search("kubernetes", n=1, mode="bm25")[0].memory.id == n_new.id)

        ctx = p.build_context(current_query="connection pool", load_l2=False,
                              load_l3=True, mode="bm25")
        test("build_context 透传 mode", ctx.l3_deep_results
             and ctx.l3_deep_results[0].memory.id == a.id)

        hyb = p.search("connection pool", n=3, mode="rrf", hybrid=True)
        test("rrf + hybrid 分数在合理区间", all(0.0 <= r.score <= 1.0 for r in hyb),
             str([round(r.score, 3) for r in hyb]))
        p.close()

        # 重启后：索引从 SQLite 原文重建
        p2 = make(d)
        test("重启后 bm25 可用", p2.search("kubernetes", n=1, mode="bm25")[0].memory.id == n_new.id)
        p2.close()


def test_engine_isolation_and_hooks() -> None:
    print("\n[isolation & hooks]")
    with tempfile.TemporaryDirectory() as d:
        pa = make(d, owner="agent-a")
        priv = pa.add("secret roadmap for project phoenix", owner="agent-a")
        pub = pa.add("public roadmap for project phoenix", owner="agent-a",
                     visibility="shared")
        pa.close()

        pb = make(d, owner="agent-b")
        for mode in ("bm25", "rrf"):
            ids = {r.memory.id for r in pb.search("roadmap phoenix", n=5, mode=mode)}
            test(f"{mode}：看不到他人 private", priv.id not in ids, str(ids))
            test(f"{mode}：能看到 shared", pub.id in ids, str(ids))

        pb.hooks.register("recall_filter",
                          lambda mems, viewer: [m for m in mems if "public" not in m.content])
        ids = {r.memory.id for r in pb.search("roadmap phoenix", n=5, mode="rrf")}
        test("rrf：recall_filter 生效", pub.id not in ids, str(ids))
        pb.close()


def test_negative_score_case() -> None:
    """LongMemEval 样例：词法嵌入给答案负分，BM25 靠 'dog'→无命中，但 rrf 仍返回全部。"""
    print("\n[sample case]")
    docs = [
        "user: Can you suggest a quick pasta recipe for dinner?",
        "user: I just adopted a golden retriever puppy named Max, any training tips?",
        "user: What is the capital of Australia?",
        "user: Help me plan a weekend hiking trip near the mountains.",
    ]
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        ids = [p.add(x).id for x in docs]
        res = p.search("What breed is my dog?", n=5, mode="rrf")
        test("rrf 不丢向量路结果", len(res) == 4 and ids[1] in [r.memory.id for r in res])
        p.close()


def test_lexical_cache() -> None:
    """v2.4：BM25 词频磁盘缓存。新进程复用缓存，结果与不用缓存完全一致。"""
    print("\n[lexical cache]")
    docs = ["user: 我们决定用 PostgreSQL 做主库", "user: I adopted a puppy named Max",
            "assistant: The deploy rollback finished at 3pm", "user: 周末去爬山，天气不错"]
    qs = ["PostgreSQL 主库", "puppy Max", "rollback deploy", "爬山"]
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        ids = [p.add(x).id for x in docs]
        first = [[r.memory.id for r in p.search(q, n=4, mode="bm25")] for q in qs]
        test("首次构建：全部重新分词", p.last_lexical_build.get("miss") == 4, str(p.last_lexical_build))
        test("缓存文件已生成", os.path.exists(os.path.join(d, "lexical.db")))
        p.close()

        p = make(d)
        again = [[r.memory.id for r in p.search(q, n=4, mode="bm25")] for q in qs]
        test("新进程：全部命中缓存", p.last_lexical_build.get("hit") == 4
             and p.last_lexical_build.get("miss") == 0, str(p.last_lexical_build))
        test("命中缓存时结果不变", again == first, f"{again} vs {first}")

        p.update(ids[0], content="user: 改用 MySQL 做主库")
        p.delete(ids[1])
        p.close()

        p = make(d)
        r = [x.memory.id for x in p.search("MySQL", n=4, mode="bm25")]
        st = p.last_lexical_build
        test("原文修改后重新分词", st.get("miss") == 1 and st.get("hit") == 2, str(st))
        test("已删除的记忆从缓存清除", st.get("stale_removed") == 1, str(st))
        test("修改后的内容可检索", r[:1] == [ids[0]], str(r))
        test("已删除的记忆不再返回",
             ids[1] not in [x.memory.id for x in p.search("puppy Max", n=4, mode="bm25")])

        p.lexical_cache = False
        p._bm25 = None
        off = [[x.memory.id for x in p.search(q, n=4, mode="bm25")] for q in qs]
        p.lexical_cache = True
        p._bm25 = None
        on = [[x.memory.id for x in p.search(q, n=4, mode="bm25")] for q in qs]
        test("开关缓存结果一致", on == off, f"{on} vs {off}")
        p.close()

    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("  -    未安装 cryptography，跳过加密用例")
        return
    with tempfile.TemporaryDirectory() as d:
        p = VecRecall(base_dir=d, wing="lex", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=NumpyVectorBackend(), dedup=False,
                      encryption_key="test-key-for-lexical-cache")
        p.add("user: 机密项目代号 Phoenix")
        res = p.search("Phoenix", n=1, mode="bm25")
        test("加密库：检索正常", bool(res) and "Phoenix" in res[0].memory.content)
        test("加密库：不写明文词频缓存", not os.path.exists(os.path.join(d, "lexical.db")))
        p.close()


def run_all() -> bool:
    test_tokenize()
    test_bm25()
    test_rrf()
    test_engine_modes()
    test_engine_isolation_and_hooks()
    test_negative_score_case()
    test_lexical_cache()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
