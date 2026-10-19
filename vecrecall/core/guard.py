"""凭据护栏。

记忆以明文落盘，且必须先对明文做嵌入才能语义检索 —— 凭据一旦入库就无法
事后补救（加密即等于放弃检索）。因此唯一可靠的时机是写入之前。

默认策略是脱敏而非拒绝：保留语义（主机、端口、库名、变量名），只去掉
凭据值本身。这样「数据库连哪台机器」仍然可检索，而密码永不落盘。

这是模式匹配，不是数学意义上的完备保证：能挡住所有常见格式的凭据，
但无法识别无关键字的裸密码，也可能误伤「密码应当加密存储」这类讨论。
"""

from __future__ import annotations

import re

REDACTED = "***"

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "连接串中的凭据",
        re.compile(r"([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@", re.I),
        r"\1\2:***@",
    ),
    (
        "口令或密钥赋值",
        re.compile(
            r"(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"([\"']?\s*[:=]\s*[\"']?)([^\s\"',;)]{4,})",
            re.I,
        ),
        r"\1\2***",
    ),
    (
        "已知前缀的 API 密钥",
        re.compile(
            r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|npm_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\-]{10,})\b"
        ),
        REDACTED,
    ),
    (
        "JWT",
        re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
        REDACTED,
    ),
    (
        "私钥",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
        ),
        "[已移除私钥]",
    ),
]


def find_secrets(text: str) -> list[str]:
    """返回命中的凭据类型列表；为空表示未发现可疑内容。"""
    text = text or ""
    return [kind for kind, pat, _ in _RULES if pat.search(text)]


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """把凭据部分替换为 ***，其余内容原样保留。

    返回（处理后的文本，命中的凭据类型列表）。
    """
    kinds: list[str] = []
    out = text or ""
    for kind, pat, repl in _RULES:
        if not pat.search(out):
            continue
        kinds.append(kind)
        out = pat.sub(repl, out)
    return out, kinds
