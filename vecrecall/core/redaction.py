"""写入前敏感信息脱敏。

设计参照 MemoraX Code 的 payload-redaction：带优先级的规则表 + 白名单 +
重叠区间合并 + 截断边界防半截密钥。纯标准库实现，不改动存储层。

脱敏是不可逆操作，默认不启用；启用时应在 MemoryExtractor 之前、
或 VecRecall.add() 落库之前调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class RedactionRule:
    kind: str
    pattern: re.Pattern
    priority: int
    group: int = 0
    accept: Callable[[str], bool] | None = None


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    redacted: bool = False
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "counts": dict(self.counts),
            "redacted": self.redacted,
            "truncated": self.truncated,
        }


_SENSITIVE_KEY = (
    r"(?:[A-Za-z_$][A-Za-z0-9_$-]*)?"
    r"(?:password|passwd|pwd|client[-_$]?secret|consumer[-_$]?secret|"
    r"api[-_$]?key|access[-_$]?token|refresh[-_$]?token|auth[-_$]?token|"
    r"id[-_$]?token|private[-_$]?key|secret[-_$]?access[-_$]?key|"
    r"secret[-_$]?key|credential|secret|token)"
)
_CREDENTIAL_VALUE = (
    r"(?:\[REDACTED:[A-Z_]+\]|\$\{\{[^\r\n]*?\}\}|\{\{[^\r\n]*?\}\}|"
    r"\$\{[^\}\r\n]+\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|[^,;#}\]\)\r\n]+)"
)

_SAFE_VALUE = re.compile(
    r"^(?:\[REDACTED:[A-Z_]+\]|\$\{\{[^\r\n]*\}\}|\{\{[^\r\n]*\}\}|"
    r"\$\{[A-Z_][A-Z0-9_]*\}|\$[A-Z_][A-Z0-9_]*|"
    r"(?:process\.)?env\.[A-Z_][A-Z0-9_]*|<[^>]+>|--\S+|x{2,}|\*{2,}|\.{3}|"
    r"sk[_-]x+|example|sample|change-?me|(?:your|replace)[-_][a-z0-9_-]+|"
    r"string|str|number|boolean|unknown|any|null|undefined|none)$",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\[REDACTED:[A-Z_]+\]")
_PARTIAL_PLACEHOLDER = re.compile(r"^\[REDACTED:[A-Z_]+(?:\]|$)")


def _is_credential(value: str) -> bool:
    return not bool(_SAFE_VALUE.match(value.strip()))


def _is_opaque_id(value: str) -> bool:
    letters = sum(ch.isalpha() for ch in value)
    digits = sum(ch.isdigit() for ch in value)
    if letters < 2 or digits < 2:
        return False
    return len(set(value.lower())) >= 12


def _is_long_number(value: str) -> bool:
    digits = sum(ch.isdigit() for ch in value)
    if digits < 9:
        return False
    normalized = value.strip().replace("(", "").replace(")", "")
    return not re.match(r"^(?:19|20)\d{2}-\d{1,2}-\d{1,2}", normalized)


RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN ((?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY|"
            r"PGP PRIVATE KEY BLOCK)-----[\s\S]*?(?:-----END \1-----|$)",
            re.IGNORECASE,
        ),
        100,
    ),
    RedactionRule(
        "AUTH_TOKEN",
        re.compile(r"(?:^|[\r\n])[ \t]*(?:Proxy-)?Authorization[ \t]*:[ \t]*([^ \t\r\n][^\r\n]*)", re.I | re.M),
        95,
        group=1,
    ),
    RedactionRule(
        "AUTH_TOKEN",
        re.compile(r"(?:^|[^A-Za-z0-9_-])(?:Bearer|Basic)\s+[\"']?([A-Za-z0-9._~+/=-]{16,})(?![A-Za-z0-9._~+/=-])", re.I),
        94,
        group=1,
    ),
    RedactionRule(
        "AUTH_TOKEN",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{16,}\b"),
        94,
    ),
    RedactionRule(
        "COOKIE",
        re.compile(r"(?:^|[\r\n])[ \t]*(?:Set-Cookie|Cookie)\s*:\s*([^\r\n]+)", re.I | re.M),
        92,
        group=1,
    ),
    RedactionRule(
        "CREDENTIAL",
        re.compile(
            rf"(?:^|[\r\n;,{{}}])[ \t]*(?:-[ \t]+)?(?:(?:export|const|let|var)[ \t]+)*"
            rf"[\"']?{_SENSITIVE_KEY}[\"']?[ \t]*[:=][ \t]*({_CREDENTIAL_VALUE})",
            re.I | re.M,
        ),
        85,
        group=1,
        accept=_is_credential,
    ),
    RedactionRule(
        "CREDENTIAL",
        re.compile(rf"--{_SENSITIVE_KEY}(?![A-Za-z0-9_$-])(?:[ \t]+|=[ \t]*)({_CREDENTIAL_VALUE})", re.I),
        85,
        group=1,
        accept=_is_credential,
    ),
    RedactionRule(
        "CREDENTIAL",
        re.compile(rf"[?&]{_SENSITIVE_KEY}(?![A-Za-z0-9_$-])=([^&#\s\"'<>]+)", re.I),
        85,
        group=1,
        accept=_is_credential,
    ),
    RedactionRule(
        "CREDENTIAL",
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]*:([^\s/@]+)@"),
        85,
        group=1,
        accept=_is_credential,
    ),
    RedactionRule("API_KEY", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}[A-Za-z0-9]\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\bsk_[A-Za-z0-9_-]{8,}\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}[A-Za-z0-9]\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\b(?:(?:sk|rk)_live_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{16,})\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}[A-Za-z0-9]\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 90),
    RedactionRule("API_KEY", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), 90),
    RedactionRule(
        "EMAIL",
        re.compile(
            r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@[A-Z0-9]"
            r"(?:[A-Z0-9.-]{0,251}[A-Z0-9])?\.[A-Z]{2,63}(?![A-Z0-9-]|\.[A-Z0-9])",
            re.I,
        ),
        70,
    ),
    RedactionRule(
        "OPAQUE_ID",
        re.compile(r"(?<![A-Za-z0-9])[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}(?![A-Za-z0-9])"),
        60,
    ),
    RedactionRule(
        "OPAQUE_ID",
        re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{64}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{32})(?![A-Za-z0-9])"),
        60,
        accept=lambda value: bool(re.search(r"[A-Fa-f]", value)),
    ),
    RedactionRule(
        "OPAQUE_ID",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{24,}(?![A-Za-z0-9])"),
        60,
        accept=_is_opaque_id,
    ),
    RedactionRule(
        "LONG_NUMBER",
        re.compile(r"(?<![A-Za-z0-9.])\+?(?:\d|\(\d)[\d ()-]*\d[Xx]?\)?(?![A-Za-z0-9]|\.\d)"),
        50,
        accept=_is_long_number,
    ),
)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    priority: int


def _collect_spans(text: str) -> list[_Span]:
    spans: list[_Span] = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            value = match.group(rule.group)
            if not value or (rule.accept is not None and not rule.accept(value)):
                continue
            start, end = match.span(rule.group)
            if end > start:
                spans.append(_Span(start, end, rule.kind, rule.priority))
    return spans


def _merge_spans(spans: list[_Span]) -> list[_Span]:
    chosen: list[_Span] = []
    for span in sorted(spans, key=lambda s: (-s.priority, s.start)):
        if any(span.start < other.end and other.start < span.end for other in chosen):
            continue
        chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


def _avoid_partial_placeholder(text: str, end: int) -> int:
    if not end or end == len(text):
        return end
    start = text.rfind("[", 0, end)
    if start < 0:
        return end
    match = _PARTIAL_PLACEHOLDER.match(text[start:])
    if match and start + len(match.group()) > end:
        return start
    return end


def _redact_prefix(text: str, end: int) -> RedactionResult:
    if end <= 0:
        return RedactionResult(text="", counts={}, redacted=False)
    spans = [span for span in _merge_spans(_collect_spans(text)) if span.start < end]
    if not spans:
        return RedactionResult(text=text[:end], counts={}, redacted=False)
    counts: dict[str, int] = {}
    parts: list[str] = []
    offset = 0
    for span in spans:
        parts.append(text[offset:span.start])
        parts.append(f"[REDACTED:{span.kind}]")
        counts[span.kind] = counts.get(span.kind, 0) + 1
        offset = span.end
    parts.append(text[offset:end])
    return RedactionResult(text="".join(parts), counts=counts, redacted=True)


def redact_memory_payload(text: str, max_chars: int | None = None) -> RedactionResult:
    """脱敏文本；max_chars 给定时先按代码点截断，并保证不切出半截密钥。"""
    if not isinstance(text, str) or not text:
        return RedactionResult(text="", counts={}, redacted=False)

    if max_chars is None or max_chars <= 0:
        return _redact_prefix(text, len(text))

    cut = min(int(max_chars), len(text))
    sample_end = min(len(text), cut + 1024)
    sample = text[:sample_end]
    for span in _collect_spans(sample):
        if span.start < cut <= span.end:
            cut = min(cut, span.start)
    cut = _avoid_partial_placeholder(sample, cut)
    result = _redact_prefix(sample, cut)
    result.truncated = cut < len(text)
    return result


def has_meaningful_payload(text: str) -> bool:
    """脱敏后是否还有可写入的内容（排除只剩占位符/空白）。"""
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_PLACEHOLDER.sub("", text).strip())
