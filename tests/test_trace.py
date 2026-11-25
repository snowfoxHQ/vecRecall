"""v2.3 回归：Trace 插件（移植自 HMR）——事件记录、脱敏、去重、保留期、失败隔离。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, VecRecall,
)
from vecrecall.plugins import TracePlugin, TraceStore, enable_trace  # noqa: E402
from vecrecall.plugins import trace as trace_mod  # noqa: E402

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


def make(d: str) -> VecRecall:
    return VecRecall(base_dir=d, wing="t", embedding_backend=BagOfWordsEmbeddingBackend(),
                     vector_backend=NumpyVectorBackend())


SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


def test_engine_events() -> None:
    print("\n[engine events]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        tr = enable_trace(p)
        test("默认目录 <base_dir>/traces", tr.store.directory == Path(d) / "traces")

        m = p.add(f"部署脚本里的 token = '{SECRET}' 已经轮换", dedup=False)
        p.add("周末计划去爬山", dedup=False)
        dup = p.add("周末计划去爬山")                           # 去重合并
        p.search("爬山", n=3)
        p.delete(m.id)

        evs = tr.store.read_events()
        ops = [e["operation"] for e in reversed(evs)]
        test("写入 / 合并 / 召回 / 删除都有记录",
             ops == ["write", "write", "merge", "recall", "delete"], str(ops))
        w = [e for e in evs if e["operation"] == "write"][-1]
        test("写入事件带元数据", w["attrs"]["wing"] == "t" and w["attrs"]["content_chars"] > 0)
        test("默认不记录原文", all(e["content"] is None for e in evs))
        merge = [e for e in evs if e["operation"] == "merge"][0]
        test("合并事件记录 occurrences", merge["attrs"].get("occurrences") == 2, str(merge["attrs"]))
        test("合并事件指向原记忆", merge["memory_ids"] == [dup.id])
        rec = [e for e in evs if e["operation"] == "recall"][0]
        test("召回事件记录命中 id", rec["attrs"]["count"] == len(rec["memory_ids"]) > 0)
        test("召回观察器不改变结果", len(p.search("爬山", n=3)) >= 1)
        st = tr.store.stats()
        test("stats 统计", st["events"] >= 5 and st["write_errors"] == 0, str(st))
        p.close()


def test_capture_content_redacted() -> None:
    print("\n[capture_content]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        tr = enable_trace(p, capture_content=True, max_field_chars=40)
        p.add(f"token = '{SECRET}' 记得换掉，" + "后面是很长的说明" * 20, dedup=False)
        ev = tr.store.read_events(operation="write")[0]
        test("打开后记录原文", isinstance(ev["content"], str) and ev["content"])
        test("原文已脱敏", SECRET not in ev["content"] and "[REDACTED:" in ev["content"],
             ev["content"])
        test("超长截断", ev["content"].endswith("[truncated]"))
        raw = (tr.store.directory / os.listdir(tr.store.directory)[0]).read_text("utf-8")
        test("磁盘文件里没有密钥", SECRET not in raw)
        p.close()


def test_store_behaviour() -> None:
    print("\n[store]")
    with tempfile.TemporaryDirectory() as d:
        st = TraceStore(d)
        test("自定义事件", st.emit("custom", attrs={"k": 1}) is not None)
        test("event_id 去重", st.emit("x", event_id="e1") == "e1"
             and st.emit("x", event_id="e1") is None)
        st.emit("x", attrs={"api_key": "abc", "nested": {"password": "p", "ok": "v"}})
        xs = st.read_events(operation="x")
        test("同一毫秒内后写的排在前面", "api_key" in xs[0]["attrs"], str(xs))
        ev = next(e for e in xs if "api_key" in e["attrs"])
        test("敏感键脱敏",
             ev["attrs"]["api_key"] == "[REDACTED]"
             and ev["attrs"]["nested"]["password"] == "[REDACTED]"
             and ev["attrs"]["nested"]["ok"] == "v", str(ev["attrs"]))

        # 损坏的行跳过
        f = next(Path(d).glob("events-*.jsonl"))
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("{not json\n\n")
        test("损坏行跳过", len(st.read_events()) == 3)

        # 过期文件清理：放一个 30 天前的文件，下一次 emit 时删除
        old_day = (trace_mod._utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        old = Path(d) / f"events-{old_day}.jsonl"
        old.write_text(json.dumps({"operation": "old"}) + "\n", encoding="utf-8")
        st2 = TraceStore(d, retention_days=7)
        st2.emit("y")
        test("过期文件被清理", not old.exists())

        # 单文件大小上限：超过后写到 -1 文件
        st3 = TraceStore(Path(d) / "small", max_file_bytes=200)
        for i in range(10):
            st3.emit("z", attrs={"i": i, "pad": "x" * 50})
        test("超过大小上限后分文件", len(list((Path(d) / "small").glob("events-*.jsonl"))) >= 2)
        test("分文件后仍能全部读出", len(st3.read_events(operation="z")) == 10)

        off = TraceStore(Path(d) / "off", enabled=False)
        test("禁用时不写、不建目录", off.emit("x") is None and not (Path(d) / "off").exists())


def test_failure_isolation() -> None:
    print("\n[failure isolation]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        tr = enable_trace(p)
        # 把目录换成一个文件，让写入必然失败
        bad = Path(d) / "not_a_dir"
        bad.write_text("x", encoding="utf-8")
        tr.store.directory = bad
        try:
            m = p.add("写入失败也不能影响主链路", dedup=False)
            hits = p.search("主链路", n=1)
            ok = bool(m.id) and hits and hits[0].memory.id == m.id
        except Exception as exc:  # noqa: BLE001
            ok = False
            print("   ", exc)
        test("Trace 写失败时记忆照常写入与检索", ok)
        test("失败被计数", tr.store.errors >= 1 and tr.store.last_error, tr.store.last_error)
        p.close()

    # 插件对象可直接 install，trace_recall=False 不注册 recall_filter
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        plugin = TracePlugin(Path(d) / "tr", trace_recall=False)
        p.hooks.install(plugin)
        test("trace_recall=False 不注册召回观察器", not p.hooks.has("recall_filter"))
        p.close()


def run_all() -> bool:
    test_engine_events()
    test_capture_content_redacted()
    test_store_behaviour()
    test_failure_isolation()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
