"""v2.6 遗留问题：级联删除、写入回滚、一致性检查、REST 收紧、启发式重要性。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend, NumpyVectorBackend, SqliteVectorBackend, VecRecall,
)
from vecrecall.core.extractor import HeuristicExtractor, heuristic_importance  # noqa: E402

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
    kw.setdefault("vector_backend", NumpyVectorBackend())
    return VecRecall(base_dir=d, wing="m", embedding_backend=BagOfWordsEmbeddingBackend(), **kw)


def test_cascade_delete():
    print("\n[级联删除]")
    with tempfile.TemporaryDirectory() as d:
        p = make(d, extractor=HeuristicExtractor())
        keep = p.add("Alice joined the Phoenix project as tech lead")
        gone = p.add("Bob reviewed the Phoenix rollout with Carol")
        g = p._graph
        before = g.get_entity("carol")
        test("删除前图谱有 Carol", before is not None)
        test("删除前 Phoenix 被 2 条记忆提及", g.get_entity("phoenix")["mentions"] == 2)

        p.delete(gone.id)
        test("只被删除记忆提及的实体被清掉", g.get_entity("carol") is None and g.get_entity("bob") is None)
        ph = g.get_entity("phoenix")
        test("仍被其他记忆提及的实体保留", ph is not None and ph["mentions"] == 1, str(ph))
        test("图谱不再引用被删记忆", gone.id not in g.entity_memories("phoenix"))
        test("保留的记忆仍可查", keep.id in g.entity_memories("phoenix"))
        test("没有派生清理错误", p.last_delete_errors == {}, str(p.last_delete_errors))
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        old = p.add("我们决定用 PostgreSQL 做主库")
        new = p.add("主库从 PostgreSQL 换成 MySQL")
        test("前置：已标记取代", p._kg.get(old.id).metadata.get("superseded_by") == new.id)
        p.delete(new.id)
        test("取代它的记忆删除后，旧记忆恢复正常权重",
             "superseded_by" not in p._kg.get(old.id).metadata)
        p.close()


def test_write_rollback():
    print("\n[写入回滚]")

    class BrokenVec(NumpyVectorBackend):
        def upsert(self, *a, **k):
            raise IOError("disk full")

    with tempfile.TemporaryDirectory() as d:
        p = make(d, vector_backend=BrokenVec())
        try:
            p.add("这条写不进去")
            test("向量写入失败时抛出异常", False)
        except IOError:
            test("向量写入失败时抛出异常", True)
        test("主库没有留下孤儿记忆", p._kg.count() == 0)
        p.close()

    class BrokenEmb(BagOfWordsEmbeddingBackend):
        def embed(self, text):
            raise ConnectionError("model offline")

    with tempfile.TemporaryDirectory() as d:
        p = VecRecall(base_dir=d, wing="m", embedding_backend=BrokenEmb(),
                      vector_backend=NumpyVectorBackend(), dedup=False, detect_contradictions=False)
        try:
            p.add("嵌入失败")
            test("嵌入失败时抛出异常", False)
        except ConnectionError:
            test("嵌入失败时抛出异常", True)
        test("嵌入失败什么都没写", p._kg.count() == 0)
        p.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d, extractor=HeuristicExtractor())

        def boom(*a, **k):
            raise RuntimeError("graph locked")
        p._graph.ingest = boom
        m = p.add("Alice deployed Phoenix")
        test("图谱失败不影响写入", p._kg.get(m.id) is not None)
        test("图谱失败记录在 metadata", "graph_error" in p._kg.get(m.id).metadata)
        p.close()


def test_consistency():
    print("\n[一致性检查]")
    with tempfile.TemporaryDirectory() as d:
        vec = SqliteVectorBackend(os.path.join(d, "vectors.db"))
        p = make(d, vector_backend=vec)
        a = p.add("第一条记忆 alpha")
        b = p.add("第二条记忆 beta")
        r = p.check_consistency()
        test("正常库检查通过", r["ok"], json.dumps(r, ensure_ascii=False))

        vec.upsert("ghost-id", [0.1] * 512, {"id": "ghost-id"})   # 孤儿向量
        vec.delete(b.id)                                         # 缺失向量
        m = p._kg.get(a.id)
        m.metadata["superseded_by"] = "deleted-id"                 # 悬空引用
        p._kg.save(m)
        r = p.check_consistency()
        test("发现孤儿向量", r["orphan_vectors"] == ["ghost-id"], str(r))
        test("发现缺失向量", r["missing_vectors"] == [b.id], str(r))
        test("发现悬空引用", r["dangling_refs"] == [a.id], str(r))
        test("检查不修改数据", not r["ok"] and "repaired" not in r)

        r = p.check_consistency(repair=True)
        test("repair 标记", r.get("repaired") is True)
        r2 = p.check_consistency()
        test("修复后检查通过", r2["ok"], json.dumps(r2, ensure_ascii=False))
        test("修复后缺失记忆可检索", any(x.memory.id == b.id for x in p.search("beta", n=5, mode="vector")))
        p.close()


def test_rest_hardening():
    print("\n[REST 收紧]")
    from vecrecall.api import server as api
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        srv = api.VecRecallHTTPServer(("127.0.0.1", 0), p, api_key="secret")
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)
        base = f"http://127.0.0.1:{port}"
        auth = {"Authorization": "Bearer secret", "Content-Type": "application/json"}

        def call(path, data=None, headers=None, method=None):
            req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        code, body = call("/memories?key=secret")
        test("URL ?key= 被拒绝", code == 401 and "Authorization" in body.get("error", ""), str(body))
        code, _ = call("/memories?key=secret", headers=auth)
        test("带了正确 Bearer 但 URL 里也有 key 仍拒绝", code == 401)
        code, _ = call("/memories", headers=auth)
        test("Bearer 正常", code == 200)
        code, body = call("/memories", data=b"{not json", headers=auth, method="POST")
        test("非法 JSON 返回 400", code == 400 and "JSON" in body.get("error", ""), str(body))
        code, body = call("/memories", data=b"[1,2]", headers=auth, method="POST")
        test("非对象 JSON 返回 400", code == 400, str(body))
        old = api.MAX_BODY
        api.MAX_BODY = 100
        code, body = call("/memories", data=json.dumps({"content": "x" * 500}).encode(),
                          headers=auth, method="POST")
        api.MAX_BODY = old
        test("超过上限返回 413", code == 413, str(body))
        code, body = call("/memories", data=json.dumps({"content": "正常写入"}).encode(),
                          headers=auth, method="POST")
        test("正常写入", code == 200 and body.get("ok"), str(body))
        code, body = call("/search", data=json.dumps({"query": "x", "n": 100000}).encode(),
                          headers=auth, method="POST")
        test("n 超出上限返回 400", code == 400 and "n" in body.get("error", ""), str(body))
        code, body = call("/memories?limit=abc", headers=auth)
        test("limit 非整数返回 400", code == 400, str(body))
        code, body = call("/memories", data=json.dumps({"content": ""}).encode(), headers=auth, method="POST")
        test("空 content 返回 400", code == 400, str(body))
        code, body = call("/memories", data=json.dumps({"content": "x", "importance": 5}).encode(),
                          headers=auth, method="POST")
        test("importance 超出 [0,1] 返回 400", code == 400, str(body))
        srv.shutdown()
        srv.server_close()
        p.close()


def test_importance():
    print("\n[启发式重要性]")
    f = heuristic_importance
    test("寒暄很低", f("好的") <= 0.1 and f("ok") <= 0.1 and f("thanks!") <= 0.1)
    test("普通闲聊中等", 0.3 <= f("今天天气不错") <= 0.45)
    test("决策明显更高", f("我们决定用 PostgreSQL 做主库") >= 0.6)
    test("偏好、规则高于闲聊", f("用户偏好深色主题") > f("今天天气不错"))
    test("多组信号叠加", f("密码不要提交到仓库，记住 API key 放环境变量") >= 0.8)
    test("长度只小幅加分", f("这是一段很长的内容，" * 50) < f("我们决定用 PostgreSQL 做主库"))
    test("范围在 0–1", all(0 <= f(x) <= 1 for x in ("", "a", "critical " * 100)))
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        m = p.add("我们决定用 PostgreSQL 做主库")
        test("写入时使用新评分", m.importance >= 0.6, str(m.importance))
        test("显式 importance 仍优先", p.add("好的", importance=0.9).importance == 0.9)
        p.close()


def test_fingerprint():
    print("\n[嵌入指纹]")
    import warnings
    from vecrecall.core.embeddings import HashEmbeddingBackend
    with tempfile.TemporaryDirectory() as d:
        vp = os.path.join(d, "vectors.db")
        p = VecRecall(base_dir=d, wing="m", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=SqliteVectorBackend(vp), dedup=False)
        p.add("第一条记忆")
        fp = json.load(open(os.path.join(d, "index.json"), encoding="utf-8"))["embedding"]
        test("写入 index.json", fp.get("backend") == "BagOfWordsEmbeddingBackend" and fp.get("dim") == 512, str(fp))
        test("stats 带指纹", p.stats()["embedding"] == fp)
        p.close()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            p = VecRecall(base_dir=d, wing="m", embedding_backend=BagOfWordsEmbeddingBackend(),
                          vector_backend=SqliteVectorBackend(vp), dedup=False)
        test("同一模型重新打开无警告", p.index_mismatch is None and not w)
        p.close()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            p = VecRecall(base_dir=d, wing="m", embedding_backend=HashEmbeddingBackend(),
                          vector_backend=SqliteVectorBackend(vp), dedup=False)
        test("换模型时给出警告", p.index_mismatch is not None and any("reindex" in str(x.message) for x in w))
        test("check_consistency 报告模型不一致", "index_mismatch" in p.check_consistency())
        n = p.reindex()
        test("reindex 后指纹更新", n == 1 and p.index_mismatch is None
             and json.load(open(os.path.join(d, "index.json"), encoding="utf-8"))["embedding"]["backend"]
             == "HashEmbeddingBackend")
        p.close()

        try:
            VecRecall(base_dir=d, wing="m", embedding_backend=BagOfWordsEmbeddingBackend(),
                      vector_backend=SqliteVectorBackend(vp), dedup=False, strict_index=True)
            test("strict_index 拒绝打开", False)
        except RuntimeError:
            test("strict_index 拒绝打开", True)

    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        p.add("内存后端")
        test("内存向量后端不写指纹", not os.path.exists(os.path.join(d, "index.json")))
        p.close()


def test_encryption_maintenance():
    print("\n[加密维护]")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("  -  未安装 cryptography，跳过")
        return
    from vecrecall.core.crypto import Cipher
    with tempfile.TemporaryDirectory() as d:
        p = make(d)
        p.add("加密前写入的明文旧记忆")
        p.close()
        k1 = Cipher.generate_key()
        p = make(d, encryption_key=k1)
        p.add("加密后写入的记忆")
        st = p.encryption_status()
        test("发现 1 条明文残留", st["enabled"] and st["plaintext_rows"] == 1, str(st))
        test("列出不加密的文件", any(x["file"] == "graph.db" for x in st["not_encrypted"]))
        k2 = Cipher.generate_key()
        n = p.rotate_key(k2)
        test("轮换处理全部记忆", n == 2)
        test("轮换后无明文残留", p.encryption_status()["plaintext_rows"] == 0)
        test("轮换后本实例仍可读", len(list(p._kg.iter_all())) == 2)
        p.close()

        p = make(d, encryption_key=k2)
        test("新密钥能打开", sorted(m.content for m in p._kg.iter_all())
             == ["加密前写入的明文旧记忆", "加密后写入的记忆"])
        p.close()
        bad = make(d, encryption_key=k1)
        try:
            list(bad._kg.iter_all())
            test("旧密钥打不开", False)
        except Exception:
            test("旧密钥打不开", True)
        finally:
            bad.close()

    with tempfile.TemporaryDirectory() as d:
        p = make(d, encryption_key="my passphrase")
        salt = os.path.join(d, "salt.key")
        test("密码模式生成 salt.key", os.path.exists(salt))
        if os.name == "posix":
            test("salt.key 权限 0600", (os.stat(salt).st_mode & 0o777) == 0o600)
        before = open(salt, "rb").read()
        p.close()
        make(d, encryption_key="my passphrase").close()
        test("salt.key 不会被覆盖", open(salt, "rb").read() == before)
        plain = make(d)
        try:
            plain.rotate_key(Cipher.generate_key())
            test("未加密库不能轮换", False)
        except ValueError:
            test("未加密库不能轮换", True)
        finally:
            plain.close()


if __name__ == "__main__":
    test_cascade_delete()
    test_write_rollback()
    test_consistency()
    test_rest_hardening()
    test_importance()
    test_fingerprint()
    test_encryption_maintenance()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
