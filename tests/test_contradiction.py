"""v2.4 矛盾检测：规则判断、写入时标记、检索降权、手动取代与撤销。"""

from __future__ import annotations

import os
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.contradiction import (  # noqa: E402
    ContradictionDetector, RuleJudge, Verdict, has_change_cue,
)
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


# (旧, 新, 是否应判为取代)
CASES = [
    ("我们决定用 PostgreSQL 做主库", "我们改用 MySQL 做主库了", True),
    ("我们决定用 PostgreSQL 做主库", "主库从 PostgreSQL 换成 MySQL", True),
    ("会议定在周三下午", "会议改到周五上午", True),
    ("My favorite color is blue", "My favorite color is green now", True),
    ("We use React for the frontend", "We switched the frontend from React to Vue", True),
    ("I drink coffee every morning", "I no longer drink coffee in the morning, I drink tea", True),
    ("部署用 Docker Compose", "部署不再用 Docker Compose，换成 Kubernetes", True),
    ("The standup is at 9am", "The standup changed to 10am", True),
    ("我的手机号是 138xxxx", "手机号改成 139xxxx 了", True),
    ("We store sessions in Redis", "Sessions are no longer stored in Redis; we use Postgres", True),
    ("我们用 Jenkins 做 CI", "CI 不再用 Jenkins 了", True),
    # 补充、细化、无关：不应判为取代
    ("我们决定用 PostgreSQL 做主库", "PostgreSQL 主库的连接池设为 20", False),
    ("我们决定用 PostgreSQL 做主库", "现在 PostgreSQL 主库已经上线，连接池设为 20", False),
    ("I adopted a puppy named Max", "Max learned to sit today", False),
    ("We use React for the frontend", "We now use React Query for data fetching in the frontend", False),
    ("部署用 Docker", "今天天气很好", False),
    ("My sister lives in Paris", "I moved to a new apartment", False),
    ("The meeting is on Wednesday", "Now let's talk about the budget", False),
    ("项目用 Python 3.10", "现在 Python 3.12 也支持了", False),
    ("I like hiking", "I now also like swimming", False),
    ("We use PostgreSQL", "We use PostgreSQL instead of MySQL for analytics too", False),
    ("Deploy with Docker", "We migrated to Docker 25 and kept the same setup", False),
    ("用户偏好深色主题", "用户现在偏好深色主题", False),
    ("Max is a golden retriever", "Max is a golden retriever puppy", False),
    ("周会在周三", "周会在周三，地点改到 3 楼会议室", False),
    ("I run every morning", "I now run every morning and evening", False),
    ("API 用 REST", "现在 API 用 REST，文档在 wiki", False),
]


def test_rule():
    print("\n[规则判断]")
    j = RuleJudge()
    fp = [(a, b) for a, b, y in CASES if not y and j(a, b) is not None]
    fn = [(a, b) for a, b, y in CASES if y and j(a, b) is None]
    test(f"无误判（{sum(not y for *_, y in CASES)} 个反例）", not fp, str(fp))
    test(f"正例全部识别（{sum(y for *_, y in CASES)} 个）", not fn, str(fn))
    v = j("我们决定用 PostgreSQL 做主库", "我们改用 MySQL 做主库了")
    test("Verdict 带原因与置信度", v is not None and "改用" in v.reason and 0.5 <= v.confidence <= 0.95)
    test("没有变更信号直接放过", not has_change_cue("PostgreSQL 主库的连接池设为 20"))
    test("英文信号按整词匹配（knowledge 不含 now）", not has_change_cue("knowledge base"))
    test("已知局限：没有共同词的变更识别不了", j("I live in Seattle", "I moved to Boston last month") is None)


def test_detector():
    print("\n[外部判断]")
    calls = []

    def judge(old, new):
        calls.append((old, new))
        return Verdict(0.9, "llm says so") if "Boston" in new else None

    d = ContradictionDetector(judge=judge)
    test("提供 judge 时默认 either", d.mode == "either")
    test("either：规则判不了时问 judge", d.judge("I live in Seattle", "I moved to Boston") is not None)
    test("judge 结果标记 method=judge", d.judge("I live in Seattle", "I moved to Boston").method == "judge")
    test("either 模式总要找候选", d.needs_candidates("hello world"))
    d2 = ContradictionDetector()
    test("规则模式：无信号不找候选", not d2.needs_candidates("hello world"))
    try:
        ContradictionDetector(mode="judge")
        test("mode=judge 缺 judge 时报错", False)
    except ValueError:
        test("mode=judge 缺 judge 时报错", True)

    def bad(old, new):
        return "yes"
    try:
        ContradictionDetector(judge=bad, mode="judge").judge("a", "b")
        test("judge 返回类型错误时报错", False)
    except TypeError:
        test("judge 返回类型错误时报错", True)


def make(d, **kw):
    kw.setdefault("dedup", False)
    return VecRecall(base_dir=d, wing="c", embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend(), **kw)


def test_engine():
    print("\n[引擎]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        old = p.add("我们决定用 PostgreSQL 做主库")
        p.add("今天的站会讨论了发布计划")
        new = p.add("主库从 PostgreSQL 换成 MySQL")
        o = p._kg.get(old.id)
        test("旧记忆标记 superseded_by", o.metadata.get("superseded_by") == new.id, str(o.metadata))
        test("新记忆记录 supersedes", p._kg.get(new.id).metadata.get("supersedes") == [old.id])
        test("旧记忆没有被删除", p._kg.get(old.id) is not None)

        for mode in ("vector", "bm25", "rrf"):
            ids = [r.memory.id for r in p.search("主库 PostgreSQL", n=3, mode=mode)]
            test(f"{mode}：新记忆排在旧记忆前", ids.index(new.id) < ids.index(old.id), str(ids))
        raw = {r.memory.id: r.score for r in p.search("主库 PostgreSQL", n=3, mode="vector",
                                                       include_superseded=True)}
        down = {r.memory.id: r.score for r in p.search("主库 PostgreSQL", n=3, mode="vector")}
        test("降权系数 0.5", abs(down[old.id] - raw[old.id] * 0.5) < 1e-9)
        test("include_superseded=True 不降权", abs(raw[old.id] - down[old.id] * 2) < 1e-9)

        b = p.build_context("主库 PostgreSQL", load_l3=True)
        l3 = [r.memory.id for r in b.l3_deep_results]
        test("build_context L3 也降权", old.id in l3 and l3.index(new.id) < l3.index(old.id), str(l3))

        lst = p.contradictions()
        test("contradictions() 列出记录", len(lst) == 1 and lst[0]["old_id"] == old.id
             and lst[0]["new_id"] == new.id and lst[0]["method"] == "rule", str(lst))

        p.unsupersede(old.id)
        o = p._kg.get(old.id)
        test("撤销：清除标记", "superseded_by" not in o.metadata and o.metadata.get("supersede_dismissed") == new.id)
        test("撤销：新记忆 supersedes 移除", p._kg.get(new.id).metadata.get("supersedes") == [])
        test("撤销后列表为空", p.contradictions() == [])

        p.supersede(old.id, new.id, reason="人工确认")
        lst = p.contradictions()
        test("手动取代", len(lst) == 1 and lst[0]["method"] == "manual" and lst[0]["reason"] == "人工确认")
        try:
            p.supersede(old.id, old.id)
            test("不能取代自己", False)
        except ValueError:
            test("不能取代自己", True)

        third = p.add("主库从 MySQL 换成 TiDB")
        test("已被取代的记忆不再作为候选",
             p._kg.get(old.id).metadata.get("superseded_by") == new.id)
        test("链式取代", p._kg.get(new.id).metadata.get("superseded_by") == third.id,
             str(p._kg.get(new.id).metadata))
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        a = p.add("我们决定用 PostgreSQL 做主库")
        p.add("PostgreSQL 主库的连接池设为 20")
        test("补充信息不触发", "superseded_by" not in p._kg.get(a.id).metadata)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d, detect_contradictions=False)
        a = p.add("我们决定用 PostgreSQL 做主库")
        p.add("主库从 PostgreSQL 换成 MySQL")
        test("detect_contradictions=False 关闭", "superseded_by" not in p._kg.get(a.id).metadata)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        a = p.add("审批：合同 C-001 金额 100 万", retention_policy="immutable")
        p.add("审批改成：合同 C-001 金额 120 万")
        test("immutable 旧记忆不被取代", "superseded_by" not in p._kg.get(a.id).metadata)
        b = p.add("我们决定用 PostgreSQL 做主库")
        p.add("主库从 PostgreSQL 换成 MySQL", retention_policy="immutable")
        test("immutable 新记忆不做检测", "superseded_by" not in p._kg.get(b.id).metadata)
        c = p.add("部署用 Docker Compose")
        p.add("部署不再用 Docker Compose，换成 Kubernetes", metadata={"_no_contradiction": True})
        test("metadata._no_contradiction 跳过", "superseded_by" not in p._kg.get(c.id).metadata)
        test("_no_contradiction 标记不落盘",
             all("_no_contradiction" not in m.metadata for m in p._kg.iter_all()))
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        a = p.add("我们决定用 PostgreSQL 做主库", wing="proj-a")
        p.add("主库从 PostgreSQL 换成 MySQL", wing="proj-b")
        test("不跨 wing", "superseded_by" not in p._kg.get(a.id).metadata)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        alice = make(d, owner="alice")
        a = alice.add("我们决定用 PostgreSQL 做主库", visibility="private")
        bob = make(d, owner="bob")
        bob.add("主库从 PostgreSQL 换成 MySQL")
        test("看不到的 private 记忆不会被别人取代", "superseded_by" not in alice._kg.get(a.id).metadata)
        alice.close()
        bob.close()

    with tempfile.TemporaryDirectory() as d:
        def judge(old, new):
            return Verdict(0.8, "llm") if "Boston" in new and "Seattle" in old else None
        p = make(d, contradiction_judge=judge)
        a = p.add("I live in Seattle near the lake")
        p.add("I moved to Boston, I no longer live near the lake")
        test("外部 judge 接入引擎", p._kg.get(a.id).metadata.get("supersede_method") in ("rule", "judge"))
        p.close()


def test_llm_judge():
    print("\n[LLM 判断]")
    from vecrecall.core.contradiction import LLMJudge
    seen = []

    def fake_chat(prompt):
        seen.append(prompt)
        if "Boston" in prompt and "Seattle" in prompt:
            return '说明：{"contradicts": true, "confidence": 0.9, "reason": "住址变更"}'
        if "Tokyo" in prompt:
            return '{"contradicts": true, "confidence": 0.4, "reason": "不太确定"}'
        if "boom" in prompt:
            raise TimeoutError("timeout")
        if "garbage" in prompt:
            return "not json"
        return '{"contradicts": false, "confidence": 0.9, "reason": "补充"}'

    j = LLMJudge(fake_chat)
    v = j("I live in Seattle", "I moved to Boston last month")
    test("LLM 识别规则盲区", v is not None and v.method == "llm" and "住址" in v.reason, str(v and v.reason))
    test("输出前后有多余文字也能解析", v is not None and v.confidence == 0.9)
    test("prompt 里带新旧两条", "Seattle" in seen[0] and "Boston" in seen[0])
    test("置信度低于阈值不算", j("I live in Osaka", "I moved to Tokyo") is None)
    test("contradicts=false 不算", j("我们用 PostgreSQL", "PostgreSQL 连接池设为 20") is None)
    test("网络异常返回 None", j("a", "boom") is None and j.errors == 1)
    test("非 JSON 返回 None", j("a", "garbage") is None and j.errors == 2)

    class FakeExtractor:
        def _chat(self, prompt):
            return fake_chat(prompt)

        def extract(self, content):
            from vecrecall.core.extractor import HeuristicExtractor
            return HeuristicExtractor().extract(content)

        def summarize(self, texts):
            return "；".join(texts)

    test("from_extractor 复用 LLM 配置", LLMJudge.from_extractor(FakeExtractor())("I live in Seattle", "I moved to Boston") is not None)
    try:
        LLMJudge.from_extractor(object())
        test("非 LLM 抽取器报错", False)
    except TypeError:
        test("非 LLM 抽取器报错", True)

    # 引擎：contradiction_judge="llm"
    with tempfile.TemporaryDirectory() as d:
        try:
            make(d, contradiction_judge="llm").close()
            test('contradiction_judge="llm" 缺 extractor 时报错', False)
        except ValueError:
            test('contradiction_judge="llm" 缺 extractor 时报错', True)
    with tempfile.TemporaryDirectory() as d:
        seen.clear()
        p = make(d, extractor=FakeExtractor(), contradiction_judge="llm")
        a = p.add("I live in Seattle near the lake")
        p.add("I moved to Boston, I live near the harbor now")
        st = p._kg.get(a.id).metadata
        test("引擎接入 LLM：没有共同变更信号也能标记", st.get("superseded_by") is not None
             and st.get("supersede_method") in ("llm", "rule"), str(st))
        n = len(seen)
        p.add("Totally unrelated: the cafeteria serves noodles on Friday")
        test("相似度太低的候选不调用 LLM", len(seen) == n, f"{n} -> {len(seen)}")
        p.close()

    # 每次写入最多调用 max_judge_calls 次
    calls = []

    def count_judge(old, new):
        calls.append(1)
        return None
    with tempfile.TemporaryDirectory() as d:
        p = make(d, contradiction_judge=count_judge)
        for i in range(6):
            p.add(f"project phoenix deploy note number {i} uses docker")
        calls.clear()
        p.add("project phoenix deploy note uses docker compose")
        test("每次写入最多调用 2 次外部 judge", len(calls) <= 2, str(len(calls)))
        p.close()


def test_result_fields():
    print("\n[检索结果字段]")
    from vecrecall.core.models import memory_to_dict
    from vecrecall.mcp.server import MCPServer
    with tempfile.TemporaryDirectory() as d:
        s = MCPServer(base_dir=d, wing="c", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=NumpyVectorBackend())
        old = s._call("mp_add", {"content": "我们决定用 PostgreSQL 做主库", "importance": 0.9})["id"]
        new = s._call("mp_add", {"content": "主库从 PostgreSQL 换成 MySQL", "importance": 0.9,
                                 "provenance": {"source_ref": "ADR-7"}})["id"]
        out = s._call("mp_search", {"query": "主库 PostgreSQL", "n": 5})
        rows = out["results"] if isinstance(out, dict) else out   # mp_search 直接返回列表
        res = {r["id"]: r for r in rows}
        test("MCP 检索结果带 superseded_by", res[old].get("superseded_by") == new, str(res[old]))
        test("未被取代的结果不带 superseded_by", "superseded_by" not in res[new])
        test("MCP 检索结果带 supersedes", res[new].get("supersedes") == [old])
        test("MCP 检索结果带来源", res[new].get("source", {}).get("via") == "mcp"
             and res[new]["source"].get("source_ref") == "ADR-7", str(res[new].get("source")))
        prompt = s._call("mp_format_prompt", {"current_query": "主库 PostgreSQL"})["prompt"]
        line = [x for x in prompt.split("\n") if "PostgreSQL 做主库" in x]
        test("prompt 里标记已过时", bool(line) and "已过时" in line[0], prompt)
        test("prompt 里新记忆不标记", all("已过时" not in x for x in prompt.split("\n") if "MySQL" in x))
        d2 = memory_to_dict(s._palace._kg.get(old))
        test("memory_to_dict（REST 共用）带 superseded_by", d2.get("superseded_by") == new)
        s._palace.close()


if __name__ == "__main__":
    test_rule()
    test_detector()
    test_engine()
    test_llm_judge()
    test_result_fields()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
