"""写回治理：幂等、缓冲、重试与可恢复 outbox。

参照 MemoraX Code 的 automatic-writeback / writeback-buffer 设计，但把云端
API 换成可注入的 flush 回调，保持 vecRecall 的本地优先定位。

典型用法::

    outbox = SqliteOutbox(str(base_dir / "knowledge.db"))
    gov = WritebackGovernor(flush=outbox.enqueue_payload)
    gov.enqueue(WritebackDecision(...))
    gov.drain()
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class WritebackDecision:
    client: str
    session_key: str
    scope: str
    idempotency_key: str
    text: str
    metadata: dict | None = None

    def buffer_key(self) -> str:
        return json.dumps(
            [self.client, self.session_key, self.scope],
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass
class BufferedDecision:
    turns: list[WritebackDecision]
    flush_reason: str

    @property
    def text(self) -> str:
        return "\n\n".join(turn.text for turn in self.turns if turn.text.strip())

    @property
    def dedupe_keys(self) -> list[str]:
        return [turn.idempotency_key for turn in self.turns]

    def as_dict(self) -> dict:
        return {
            "turn_count": len(self.turns),
            "flush_reason": self.flush_reason,
            "dedupe_keys": self.dedupe_keys,
            "text": self.text,
        }


@dataclass
class EnqueueResult:
    accepted: bool
    reason: str = ""
    buffered: bool = False


@dataclass
class FlushResult:
    ok: bool
    reason: str = ""
    attempts: int = 0
    error: str = ""


class WritebackGovernor:
    """按 turn 缓冲并治理自动写回，不绑定任何远端服务。"""

    def __init__(
        self,
        flush: Callable[[BufferedDecision], None] | None = None,
        *,
        max_turns: int = 8,
        max_age_ms: int = 10 * 60 * 1000,
        max_chars: int = 12000,
        max_messages: int = 100,
        dedupe_ttl_ms: int = 5 * 60 * 1000,
        dedupe_max: int = 1024,
        max_attempts: int = 2,
        retry_delay_ms: int = 100,
        max_retry_delay_ms: int = 5000,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._flush = flush
        self._max_turns = max(1, int(max_turns))
        self._max_age_ms = max(1, int(max_age_ms))
        self._max_chars = max(1, int(max_chars))
        self._max_messages = max(1, int(max_messages))
        self._dedupe_ttl_ms = max(0, int(dedupe_ttl_ms))
        self._dedupe_max = max(1, int(dedupe_max))
        self._max_attempts = max(1, int(max_attempts))
        self._retry_delay_ms = max(0, int(retry_delay_ms))
        self._max_retry_delay_ms = max(self._retry_delay_ms, int(max_retry_delay_ms))
        self._now = now or (lambda: time.time() * 1000)
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))
        self._buffers: dict[str, list[WritebackDecision]] = {}
        self._updated: dict[str, float] = {}
        self._chars: dict[str, int] = {}
        self._seen: dict[str, float] = {}
        self._closed = False
        self.stats: dict[str, int] = {
            "accepted": 0,
            "buffered": 0,
            "flushed": 0,
            "duplicates": 0,
            "failures": 0,
            "retries": 0,
        }

    def enqueue(self, decision: WritebackDecision) -> EnqueueResult:
        if self._closed:
            return EnqueueResult(False, "runtime_closed")
        if not decision.idempotency_key.strip():
            return EnqueueResult(False, "idempotency_key_missing")
        now = self._now()
        self._expire_seen(now)
        key = decision.idempotency_key
        if key in self._seen:
            self.stats["duplicates"] += 1
            return EnqueueResult(False, "duplicate_turn")
        bucket = decision.buffer_key()
        turns = self._buffers.setdefault(bucket, [])
        if any(turn.idempotency_key == key for turn in turns):
            self.stats["duplicates"] += 1
            return EnqueueResult(False, "buffer_duplicate_turn")

        turns.append(decision)
        self._updated[bucket] = now
        self._chars[bucket] = self._chars.get(bucket, 0) + len(decision.text)
        self.stats["accepted"] += 1
        self.stats["buffered"] += 1

        if len(turns) >= self._max_turns:
            self._flush_bucket(bucket, "turn_limit")
        elif self._chars[bucket] > self._max_chars:
            self._flush_bucket(bucket, "char_limit")
        elif len(turns) >= self._max_messages:
            self._flush_bucket(bucket, "message_limit")
        return EnqueueResult(True, buffered=bucket in self._buffers)

    def drain_due(self) -> int:
        """刷新idle超时的桶，返回刷出的桶数。"""
        now = self._now()
        due = [key for key, updated in self._updated.items()
               if key in self._buffers and now - updated >= self._max_age_ms]
        for key in due:
            self._flush_bucket(key, "idle")
        return len(due)

    def drain(self, flush_reason: str = "manual") -> int:
        buckets = list(self._buffers)
        for bucket in buckets:
            self._flush_bucket(bucket, flush_reason)
        return len(buckets)

    def close(self) -> None:
        if self._closed:
            return
        self.drain("shutdown")
        self._closed = True

    def _expire_seen(self, now: float) -> None:
        if not self._dedupe_ttl_ms:
            self._seen.clear()
            return
        expired = [key for key, at in self._seen.items() if now - at >= self._dedupe_ttl_ms]
        for key in expired:
            self._seen.pop(key, None)
        while len(self._seen) > self._dedupe_max:
            self._seen.pop(next(iter(self._seen)))

    def _flush_bucket(self, bucket: str, reason: str) -> bool:
        turns = self._buffers.pop(bucket, None)
        self._updated.pop(bucket, None)
        self._chars.pop(bucket, None)
        if not turns:
            return False
        batch = BufferedDecision(turns=turns, flush_reason=reason)
        result = self._dispatch(batch)
        if result.ok:
            for key in batch.dedupe_keys:
                self._seen[key] = self._now()
            self.stats["flushed"] += 1
            return True
        self.stats["failures"] += 1
        return False

    def _dispatch(self, batch: BufferedDecision) -> FlushResult:
        if self._flush is None:
            return FlushResult(True, reason="no_flush_callback")
        delay = self._retry_delay_ms
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._flush(batch)
                return FlushResult(True, attempts=attempt)
            except Exception as exc:  # noqa: BLE001 - flush callback 可为任意后端
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_attempts:
                    self.stats["retries"] += 1
                    self._sleep(min(delay, self._max_retry_delay_ms) / 1000.0)
                    delay *= 2
        return FlushResult(False, reason="flush_failed", attempts=self._max_attempts, error=last_error)


class SqliteOutbox:
    """以主库为事实源的持久写回队列；derivative 索引后续消费。"""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS writeback_outbox (
                id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                client TEXT NOT NULL,
                scope TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writeback_status ON writeback_outbox(status, created_at)"
        )
        self._conn.commit()

    def enqueue_payload(self, batch: BufferedDecision) -> int:
        """适配 WritebackGovernor.flush 的回调签名。"""
        written = 0
        now = time.time()
        for turn in batch.turns:
            payload = json.dumps(
                {
                    "text": turn.text,
                    "metadata": turn.metadata or {},
                    "session_key": turn.session_key,
                    "flush_reason": batch.flush_reason,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO writeback_outbox
                    (id, dedupe_key, client, scope, payload, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    str(uuid.uuid4()), turn.idempotency_key, turn.client,
                    turn.scope, payload, now, now,
                ),
            )
            written += cur.rowcount
        self._conn.commit()
        return written

    def claim(self, limit: int = 32) -> list[dict]:
        now = time.time()
        rows = self._conn.execute(
            "SELECT id, dedupe_key, payload, attempts FROM writeback_outbox "
            "WHERE status = 'pending' ORDER BY created_at LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        claimed = []
        for row in rows:
            cur = self._conn.execute(
                "UPDATE writeback_outbox SET status = 'in_flight', attempts = attempts + 1, "
                "updated_at = ? WHERE id = ? AND status = 'pending'",
                (now, row[0]),
            )
            if cur.rowcount:
                claimed.append({"id": row[0], "key": row[1], "payload": row[2], "attempts": row[3] + 1})
        self._conn.commit()
        return claimed

    def ack(self, item_id: str) -> None:
        self._conn.execute(
            "UPDATE writeback_outbox SET status = 'done', updated_at = ? WHERE id = ?",
            (time.time(), item_id),
        )
        self._conn.commit()

    def fail(self, item_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE writeback_outbox SET status = 'pending', error = ?, updated_at = ? WHERE id = ?",
            (str(error)[:1000], time.time(), item_id),
        )
        self._conn.commit()

    def recover_stale(self, timeout_seconds: float = 300.0) -> int:
        cur = self._conn.execute(
            "UPDATE writeback_outbox SET status = 'pending', updated_at = ? "
            "WHERE status = 'in_flight' AND updated_at < ?",
            (time.time(), time.time() - max(1.0, float(timeout_seconds))),
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM writeback_outbox GROUP BY status"
        ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def close(self) -> None:
        self._conn.close()
