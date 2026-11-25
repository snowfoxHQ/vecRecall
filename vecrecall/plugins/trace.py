"""本地 Trace 插件：把写入 / 删除 / 召回事件记成 JSONL，用于排查问题。

移植自 HMR（hmr/observability/trace.py），改动：
  - 通过扩展钩子挂载（after_write / on_delete / recall_filter），不改核心代码
  - 脱敏复用 vecrecall.core.redaction，不再维护第二套规则
  - 默认只记元数据（capture_content=False），需要原文时显式打开，打开后先脱敏再截断
  - 过期清理按文件名里的日期判断，不依赖文件修改时间
  - 时间统一用 UTC 带时区的 ISO 格式

设计原则（沿用 HMR）：Trace 失败绝不影响记忆主链路。所有异常在插件内部吞掉，
计入 TraceStore.errors，可通过 stats() 查看。

用法：
    from vecrecall.plugins import enable_trace
    trace = enable_trace(palace)                    # 写到 <base_dir>/traces/
    ...
    trace.store.read_events(limit=20)
    trace.store.stats()

已知限制：
  - recall_filter 只在检索有结果时才会被调用，所以「零结果的检索」不会留下记录。
  - 钩子拿不到查询原文，召回事件只记录命中的记忆 id 与数量。
  - event_id 去重只在本进程内有效（内存集合，有上限）。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vecrecall.core.redaction import redact_memory_payload

SCHEMA_VERSION = "vecrecall_trace.v1"
SENSITIVE_KEYS = (
    "authorization", "api-key", "api_key", "apikey",
    "token", "secret", "password", "passwd", "credential", "cookie",
)
DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_FIELD_CHARS = 2000
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_SEEN_IDS = 4096
_FILE_DATE_RE = re.compile(r"events-(\d{4}-\d{2}-\d{2})")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TraceEvent:
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""
    timestamp: str = ""
    operation: str = ""          # write / merge / delete / recall / 自定义
    ok: bool = True
    memory_ids: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    content: Any = None          # 仅 capture_content=True 时有值（已脱敏、已截断）
    error: Any = None


def sanitize(value: Any, max_chars: int = DEFAULT_MAX_FIELD_CHARS) -> Any:
    """脱敏并截断：敏感键整体替换；字符串先脱敏再截断，避免密钥残片跨截断边界泄漏。"""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in SENSITIVE_KEYS)
                    else sanitize(v, max_chars))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, max_chars) for v in value]
    if isinstance(value, str):
        text = redact_memory_payload(value).text
        if len(text) > max_chars:
            text = text[:max_chars] + "[truncated]"
        return text
    return value


class TraceStore:
    """按天分文件写 JSONL：<dir>/events-YYYY-MM-DD.jsonl。"""

    def __init__(self, directory: str | Path, *,
                 capture_content: bool = False,
                 retention_days: int = DEFAULT_RETENTION_DAYS,
                 max_field_chars: int = DEFAULT_MAX_FIELD_CHARS,
                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
                 enabled: bool = True):
        self.directory = Path(directory)
        self.capture_content = capture_content
        self.retention_days = retention_days
        self.max_field_chars = max_field_chars
        self.max_file_bytes = max_file_bytes
        self.enabled = enabled
        self.errors = 0
        self.last_error: str = ""
        self._lock = threading.Lock()
        self._seen: dict[str, None] = {}          # 有序集合，便于按插入顺序淘汰
        self._last_prune_day = ""
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    # ── 写入 ──

    def emit(self, operation: str, *, event_id: str | None = None, ok: bool = True,
             memory_ids: list[str] | None = None, attrs: dict | None = None,
             content: Any = None, error: Any = None) -> str | None:
        """写一条事件，返回 event_id；禁用、去重命中或写入失败时返回 None（不抛异常）。"""
        if not self.enabled:
            return None
        try:
            eid = event_id or uuid.uuid4().hex
            with self._lock:
                if eid in self._seen:
                    return None
                self._seen[eid] = None
                if len(self._seen) > MAX_SEEN_IDS:
                    for old in list(self._seen)[:MAX_SEEN_IDS // 2]:
                        del self._seen[old]
                now = _utcnow()
                event = TraceEvent(
                    event_id=eid,
                    timestamp=now.isoformat(timespec="milliseconds"),
                    operation=operation,
                    ok=ok,
                    memory_ids=list(memory_ids or []),
                    attrs=sanitize(dict(attrs or {}), self.max_field_chars),
                    content=(sanitize(content, self.max_field_chars)
                             if self.capture_content and content is not None else None),
                    error=sanitize(error, self.max_field_chars) if error is not None else None,
                )
                self._prune_locked(now)
                target = self._target_locked(now)
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
                return eid
        except Exception as exc:  # noqa: BLE001 —— Trace 失败不能影响主链路
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _target_locked(self, now: datetime) -> Path:
        day = now.strftime("%Y-%m-%d")
        target = self.directory / f"events-{day}.jsonl"
        if target.exists() and target.stat().st_size >= self.max_file_bytes:
            n = 1
            while True:
                cand = self.directory / f"events-{day}-{n}.jsonl"
                if not cand.exists() or cand.stat().st_size < self.max_file_bytes:
                    return cand
                n += 1
        return target

    def _prune_locked(self, now: datetime) -> None:
        """每天最多清理一次：删除文件名日期早于保留期的文件。"""
        day = now.strftime("%Y-%m-%d")
        if day == self._last_prune_day:
            return
        self._last_prune_day = day
        cutoff = (now - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        for path in self.directory.glob("events-*.jsonl"):
            m = _FILE_DATE_RE.match(path.name)
            if m and m.group(1) < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    # ── 读取 ──

    def read_events(self, limit: int = 100, operation: str | None = None) -> list[dict]:
        """按时间倒序返回最近的事件；损坏的行跳过。"""
        events: list[dict] = []
        if not self.directory.exists():
            return events
        for path in sorted(self.directory.glob("events-*.jsonl"), reverse=True):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if operation is None or ev.get("operation") == operation:
                            events.append(ev)
            except OSError:
                continue
        events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return events[:limit]

    def stats(self) -> dict:
        events = self.read_events(limit=100000)
        by_op: dict[str, int] = {}
        for ev in events:
            op = ev.get("operation", "unknown")
            by_op[op] = by_op.get(op, 0) + 1
        return {
            "enabled": self.enabled,
            "directory": str(self.directory),
            "capture_content": self.capture_content,
            "retention_days": self.retention_days,
            "events": len(events),
            "failures": sum(1 for ev in events if not ev.get("ok", True)),
            "by_operation": by_op,
            "write_errors": self.errors,
            "last_error": self.last_error,
        }


class TracePlugin:
    """把 TraceStore 接到 VecRecall 的扩展钩子上。

    trace_recall=True 时会注册一个「只观察、不过滤」的 recall_filter，原样返回结果。
    """

    def __init__(self, directory: str | Path, *, trace_recall: bool = True,
                 **store_kwargs):
        self.store = TraceStore(directory, **store_kwargs)
        self.trace_recall = trace_recall

    def register(self, registry) -> None:
        registry.register("after_write", self._on_write)
        registry.register("on_delete", self._on_delete)
        if self.trace_recall:
            registry.register("recall_filter", self._on_recall)

    def _on_write(self, memory, merged: bool) -> None:
        try:
            attrs = {
                "wing": memory.wing,
                "topic": memory.topic,
                "session_id": memory.session_id,
                "memory_type": memory.memory_type,
                "visibility": memory.visibility,
                "owner": memory.owner,
                "retention_policy": memory.retention_policy,
                "topic_key": memory.topic_key,
                "importance": round(float(memory.importance), 3),
                "content_chars": len(memory.content or ""),
            }
            if merged:
                attrs["occurrences"] = memory.metadata.get("occurrences")
            redacted = memory.metadata.get("redaction_counts")
            if redacted:
                attrs["redaction_counts"] = redacted
            self.store.emit("merge" if merged else "write",
                            memory_ids=[memory.id], attrs=attrs, content=memory.content)
        except Exception as exc:  # noqa: BLE001
            self.store.errors += 1
            self.store.last_error = f"{type(exc).__name__}: {exc}"

    def _on_delete(self, memory_id: str) -> None:
        self.store.emit("delete", memory_ids=[memory_id])

    def _on_recall(self, memories, viewer):
        try:
            self.store.emit("recall", memory_ids=[m.id for m in memories],
                            attrs={"count": len(memories), "viewer": viewer or ""})
        except Exception as exc:  # noqa: BLE001
            self.store.errors += 1
            self.store.last_error = f"{type(exc).__name__}: {exc}"
        return memories


def enable_trace(palace, directory: str | Path | None = None, **kwargs) -> TracePlugin:
    """为一个 VecRecall 实例开启 Trace，默认写到 <base_dir>/traces/，返回插件对象。"""
    plugin = TracePlugin(directory or Path(palace.base_dir) / "traces", **kwargs)
    palace.hooks.install(plugin)
    return plugin
