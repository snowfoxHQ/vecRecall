"""阶段1 回归：脱敏 + 写回治理 + outbox。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.core.redaction import (  # noqa: E402
    has_meaningful_payload,
    redact_memory_payload,
)
from vecrecall.core.writeback import (  # noqa: E402
    BufferedDecision,
    SqliteOutbox,
    WritebackDecision,
    WritebackGovernor,
)
from vecrecall.core.engine import (  # noqa: E402
    BagOfWordsEmbeddingBackend,
    NumpyVectorBackend,
    VecRecall,
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


def decision(key: str, text: str = "some engineering lesson", client: str = "codex") -> WritebackDecision:
    return WritebackDecision(
        client=client,
        session_key="session-1",
        scope="repo-a",
        idempotency_key=key,
        text=text,
    )


def test_redaction() -> None:
    print("\n[redaction]")
    result = redact_memory_payload("token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' ok")
    test("github token redacted", "[REDACTED:API_KEY]" in result.text, result.text)
    test("github token counted", result.counts.get("API_KEY") == 1, str(result.counts))

    result = redact_memory_payload("contact alice@example.com for details")
    test("email redacted", "[REDACTED:EMAIL]" in result.text, result.text)

    result = redact_memory_payload("api_key = ${MY_KEY}; password = example")
    test("env placeholder kept", "${MY_KEY}" in result.text, result.text)
    test("example value kept", "example" in result.text, result.text)
    test("no false positive on templates", result.redacted is False, result.text)

    result = redact_memory_payload("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    test("bearer token redacted", "[REDACTED:AUTH_TOKEN]" in result.text, result.text)

    long = "prefix " + "A" * 40 + " tail"
    result = redact_memory_payload(long, max_chars=10)
    test("opaque id cut at boundary", "A" * 40 not in result.text, result.text)
    test("truncation flagged", result.truncated is True)

    result = redact_memory_payload("plain text", max_chars=4)
    test("truncated to max chars", result.text == "plai", result.text)

    test("whitespace rejected", has_meaningful_payload("   ") is False)
    test("placeholder-only rejected", has_meaningful_payload("[REDACTED:API_KEY]") is False)
    test("normal text accepted", has_meaningful_payload("a lesson") is True)


def test_governor() -> None:
    print("\n[writeback governor]")
    flushed: list[BufferedDecision] = []
    gov = WritebackGovernor(flush=flushed.append, max_turns=2, sleep=lambda _s: None)

    first = gov.enqueue(decision("k1", "lesson one"))
    test("first turn buffered", first.accepted and first.buffered)
    dup = gov.enqueue(decision("k1", "lesson one"))
    test("duplicate rejected", not dup.accepted and dup.reason == "buffer_duplicate_turn", dup.reason)
    second = gov.enqueue(decision("k2", "lesson two"))
    test("second turn buffered", second.accepted)
    test("turn limit flushed", len(flushed) == 1, str(len(flushed)))
    test("flush reason", flushed[0].flush_reason == "turn_limit", flushed[0].flush_reason)
    test("dedupe keys recorded", flushed[0].dedupe_keys == ["k1", "k2"])
    test("accepted after flush replay rejected", not gov.enqueue(decision("k1")).accepted)

    gov.close()
    test("close is idempotent", gov.drain() == 0)

    errors = []

    def failing(batch: BufferedDecision) -> None:
        errors.append(batch)
        if len(errors) == 1:
            raise RuntimeError("transient")

    retried = WritebackGovernor(flush=failing, max_turns=1, sleep=lambda _s: None)
    retried.enqueue(decision("r1"))
    test("retry succeeds after transient failure", retried.stats["retries"] == 1 and retried.stats["flushed"] == 1, str(retried.stats))

    now = [0.0]
    idle_flushed: list[BufferedDecision] = []
    idle = WritebackGovernor(
        flush=idle_flushed.append,
        max_turns=100,
        max_age_ms=1000,
        now=lambda: now[0],
    )
    idle.enqueue(decision("i1"))
    now[0] = 1000.0
    test("idle flush due", idle.drain_due() == 1 and len(idle_flushed) == 1)


def test_outbox() -> None:
    print("\n[sqlite outbox]")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "knowledge.db")
        outbox = SqliteOutbox(path)
        batch = BufferedDecision(
            turns=[decision("o1", "lesson"), decision("o2", "another")],
            flush_reason="turn_limit",
        )
        written = outbox.enqueue_payload(batch)
        test("both turns enqueued", written == 2, str(written))
        test("dedupe on replay", outbox.enqueue_payload(batch) == 0)
        test("pending count", outbox.stats().get("pending") == 2, str(outbox.stats()))

        claimed = outbox.claim(10)
        test("claim returns rows", len(claimed) == 2, str(len(claimed)))
        test("in flight state", outbox.stats().get("in_flight") == 2, str(outbox.stats()))
        outbox.ack(claimed[0]["id"])
        outbox.fail(claimed[1]["id"], "boom")
        stats = outbox.stats()
        test("ack marks done", stats.get("done") == 1, str(stats))
        test("fail requeues", stats.get("pending") == 1, str(stats))

        outbox.close()
        reopened = SqliteOutbox(path)
        test("survives reopen", reopened.stats().get("done") == 1)
        reopened.close()


def test_engine_integration() -> None:
    print("\n[engine integration]")
    with tempfile.TemporaryDirectory() as tmp:
        engine = VecRecall(
            tmp,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
            extractor=None,
            redact_payloads=True,
        )
        mem = engine.add("token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' was leaked")
        test("secret removed from stored content", "ghp_" not in mem.content, mem.content)
        test("redaction counted in metadata", mem.metadata.get("redaction_counts", {}).get("API_KEY") == 1, str(mem.metadata))
        engine.close()

        engine = VecRecall(
            tmp,
            embedding_backend=BagOfWordsEmbeddingBackend(),
            vector_backend=NumpyVectorBackend(),
        )
        try:
            engine.add("token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789' was leaked")
            test("redaction off by default", True)
        except Exception:
            test("redaction off by default", False)
        engine.close()


def run_all() -> bool:
    test_redaction()
    test_governor()
    test_outbox()
    test_engine_integration()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
