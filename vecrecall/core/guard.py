"""凭据护栏（兼容层）。

v2.2 起凭据识别规则统一由 ``vecrecall.core.redaction`` 维护，本模块只保留
原有接口，供 ``scripts/audit_secrets.py`` 和老代码使用：

- ``find_secrets(text)``：返回命中的凭据类型列表，为空表示未发现可疑内容。
- ``redact_secrets(text)``：只脱敏凭据，返回（处理后的文本，命中类型列表）。

与 ``redact_memory_payload`` 的区别：这里只关心**凭据**（私钥、令牌、API Key、
口令赋值、Cookie），不处理邮箱、长数字、不透明 ID 等一般敏感信息，
因此不会把「联系 alice@example.com」当成泄露。

这是模式匹配，不是完备保证：无法识别没有关键字的裸密码。
"""

from __future__ import annotations

from vecrecall.core.redaction import redact_memory_payload

# 视为「凭据」的脱敏类型；EMAIL / OPAQUE_ID / LONG_NUMBER 不算
CREDENTIAL_KINDS = frozenset({"PRIVATE_KEY", "AUTH_TOKEN", "COOKIE", "CREDENTIAL", "API_KEY"})


def find_secrets(text: str) -> list[str]:
    """返回命中的凭据类型列表（去重、按类型名排序）；为空表示未发现可疑内容。"""
    result = redact_memory_payload(text or "")
    return sorted(k for k in result.counts if k in CREDENTIAL_KINDS)


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """只把凭据替换为 ``[REDACTED:<类型>]``，其余内容（含邮箱等）原样保留。

    返回（处理后的文本，命中的凭据类型列表）。
    """
    from vecrecall.core.redaction import _collect_spans, _merge_spans

    src = text or ""
    spans = [s for s in _merge_spans(_collect_spans(src)) if s.kind in CREDENTIAL_KINDS]
    if not spans:
        return src, []
    parts: list[str] = []
    offset = 0
    for s in spans:
        parts.append(src[offset:s.start])
        parts.append(f"[REDACTED:{s.kind}]")
        offset = s.end
    parts.append(src[offset:])
    return "".join(parts), sorted({s.kind for s in spans})
