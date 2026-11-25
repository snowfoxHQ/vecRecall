"""记忆卡片门槛插件：结构化的编码经验卡片，未经验证的正面经验不能以「已验证」入库。

移植自 HMR（hmr/engines/memory_card.py，借鉴 MemoraX Code 的 CODE_AGENT_MEMORY 卡片）。
卡片是一段以 CODE_AGENT_MEMORY 开头的纯文本，每行一个字段：

    CODE_AGENT_MEMORY
    repo: snowfoxHQ/vecRecall
    scope: vecrecall/core/engine.py::search
    status: verified                 # verified / failed_attempt / candidate
    signal: test_core 257/257 通过
    problem: search 默认 min_score=0 丢弃负分结果
    technical_context: 词法哈希嵌入的余弦相似度可以为负
    surfaces: search / _deep_search / mp_search / REST /search
    failed_shape: 只改了 _deep_search，漏了 search 的默认值
    validation: python tests/test_core.py
    principle: 检索默认返回 top-n，阈值由调用方显式给出
    anchors: tests/test_core.py::test_search_returns_topn_with_negative_scores

门槛（与 HMR 相同）：
  - status=verified 必须写 validation，且 signal 不能为空或 unknown
  - status=failed_attempt 必须写 failed_shape（错在哪）
  - problem 不能为空
  - principle / anchors 为空、卡片过长只给警告，不拒绝

接入方式：作为 before_write 钩子挂在 VecRecall 上，所有写入入口（Python API、MCP、
REST、CLI）都会经过它。不是卡片的内容原样放行。

  strict=True（默认）：不合格的卡片直接拒绝写入（抛 ValueError）。
  strict=False：verified / failed_attempt 不合格时降级为 candidate 再写入，
                避免未验证经验以 verified 的身份入库；problem 为空仍然拒绝。

卡片的 status / evidence 等字段以卡片为准，调用方无法通过 metadata 覆盖。

与 HMR 原版的区别：
  - 通过钩子接入，不改核心；结构化字段写入 metadata["card"]，status / repo 等提到顶层便于过滤
  - 不改变 memory_type（由调用方决定）；topic 为 general 时默认设为 code_memory
  - 未给 importance 时按状态给默认值：verified 0.8、failed_attempt 0.75、candidate 0.4
  - 卡片不参与语义去重，同一问题的多张卡片各自保留
  - 宽松模式的降级在钩子内一次完成；原版降级后再次进入 ingest 时会重新校验
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

PREFIX = "CODE_AGENT_MEMORY"
FIELDS = (
    "repo", "scope", "status", "signal", "problem", "technical_context",
    "surfaces", "failed_shape", "validation", "principle", "anchors",
)
STATUSES = ("verified", "failed_attempt", "candidate")
MAX_CARD_CHARS = 1100
DEFAULT_TOPIC = "code_memory"
DEFAULT_IMPORTANCE = {"verified": 0.8, "failed_attempt": 0.75, "candidate": 0.4}


@dataclass
class MemoryCard:
    repo: str = ""
    scope: str = ""
    status: str = "candidate"
    signal: str = ""
    problem: str = ""
    technical_context: str = ""
    surfaces: str = ""
    failed_shape: str = ""
    validation: str = ""
    principle: str = ""
    anchors: str = ""


@dataclass
class CardCheck:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_card(text: str) -> bool:
    """第一行非空内容是 CODE_AGENT_MEMORY 即视为卡片。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip() == PREFIX
    return False


def parse_card(text: str) -> MemoryCard | None:
    """解析卡片；不是卡片或没有任何已知字段时返回 None。未知字段与 # 注释行忽略。"""
    if not is_card(text):
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()][1:]
    values: dict[str, str] = {}
    for line in lines:
        if line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in FIELDS:
            values[key] = value.strip()
    if not any(values.values()):
        return None
    card = MemoryCard(**values)
    card.status = (card.status or "candidate").strip().lower()
    return card


def render_card(card: MemoryCard) -> str:
    return "\n".join([PREFIX] + [f"{k}: {getattr(card, k, '')}" for k in FIELDS])


def validate_card(card: MemoryCard) -> CardCheck:
    errors: list[str] = []
    warnings: list[str] = []
    if card.status not in STATUSES:
        errors.append(f"status 必须是 {' / '.join(STATUSES)} 之一，当前为 {card.status!r}")
    if card.status == "verified":
        if not card.validation.strip():
            errors.append("status=verified 必须填写 validation（怎么验证的）")
        sig = card.signal.strip().lower()
        if not sig or "unknown" in sig:
            errors.append("status=verified 必须填写明确的 signal，不能为空或 unknown")
    if card.status == "failed_attempt" and not card.failed_shape.strip():
        errors.append("status=failed_attempt 必须填写 failed_shape（错在哪）")
    if not card.problem.strip():
        errors.append("problem 不能为空")
    if not card.principle.strip():
        warnings.append("principle 为空，经验难以迁移到其他场景")
    if not card.anchors.strip():
        warnings.append("anchors 为空，缺少可核对的代码或测试位置")
    if len(render_card(card)) > MAX_CARD_CHARS:
        warnings.append(f"卡片超过 {MAX_CARD_CHARS} 字符，建议精简")
    return CardCheck(ok=not errors, errors=errors, warnings=warnings)


def card_evidence(card: MemoryCard) -> list[str]:
    return [v for v in (card.validation, card.anchors, card.signal)
            if v.strip() and v.strip().lower() not in ("none", "n/a", "unknown")]


class MemoryCardGate:
    """before_write 钩子：识别卡片、执行门槛、把结构化字段写进 metadata。"""

    def __init__(self, strict: bool = True):
        self.strict = strict
        self.accepted = 0
        self.rejected = 0
        self.downgraded = 0

    def register(self, registry) -> None:
        registry.register("before_write", self.before_write)

    def before_write(self, fields_: dict) -> dict | None:
        content = fields_.get("content") or ""
        if not is_card(content):
            return None
        card = parse_card(content)
        if card is None:
            self.rejected += 1
            raise ValueError(f"{PREFIX} 卡片格式无法解析：没有任何已知字段")

        check = validate_card(card)
        downgraded_from = None
        if not check.ok:
            fixable = (self.strict is False and card.problem.strip()
                       and card.status in ("verified", "failed_attempt"))
            if not fixable:
                self.rejected += 1
                raise ValueError(f"{PREFIX} 校验失败：" + "；".join(check.errors))
            downgraded_from = card.status
            card.status = "candidate"
            check = validate_card(card)
            self.downgraded += 1

        meta = dict(fields_.get("metadata") or {})
        meta.update({                                   # 卡片字段优先，调用方不能覆盖
            "source": "memory_card",
            "card": asdict(card),
            "card_status": card.status,
            "card_repo": card.repo,
            "card_scope": card.scope,
            "evidence": card_evidence(card),
        })
        if check.warnings:
            meta["card_warnings"] = check.warnings
        # 卡片不参与语义去重：同一个问题的「失败尝试」和后来的「已验证」正文高度相似，
        # 合并会丢掉其中一张卡的状态与证据
        meta["_no_dedup"] = True
        if downgraded_from:
            meta["card_downgraded_from"] = downgraded_from
        else:
            meta.pop("card_downgraded_from", None)

        fields_["content"] = render_card(card)
        fields_["metadata"] = meta
        if fields_.get("topic") in (None, "", "general"):
            fields_["topic"] = DEFAULT_TOPIC
        if fields_.get("importance") is None:
            fields_["importance"] = DEFAULT_IMPORTANCE[card.status]
        self.accepted += 1
        return fields_

    def stats(self) -> dict:
        return {"strict": self.strict, "accepted": self.accepted,
                "rejected": self.rejected, "downgraded": self.downgraded}


def enable_memory_cards(palace, strict: bool = True) -> MemoryCardGate:
    """给一个 VecRecall 实例装上卡片门槛，返回插件对象。"""
    gate = MemoryCardGate(strict=strict)
    palace.hooks.install(gate)
    return gate


def list_cards(palace, status: str | None = None, repo: str | None = None) -> list:
    """列出库里的卡片记忆（按时间倒序），可按状态、仓库过滤；遵守可见性隔离。"""
    out = []
    for m in palace._kg.iter_all():
        if m.metadata.get("source") != "memory_card":
            continue
        if palace.owner and not palace._visible_to(m, palace.owner):
            continue
        if status and m.metadata.get("card_status") != status:
            continue
        if repo and m.metadata.get("card_repo") != repo:
            continue
        out.append(m)
    return sorted(out, key=lambda m: m.timestamp, reverse=True)


__all__ = [
    "MemoryCard", "CardCheck", "MemoryCardGate", "enable_memory_cards", "list_cards",
    "is_card", "parse_card", "render_card", "validate_card", "card_evidence",
    "PREFIX", "FIELDS", "STATUSES",
]

# 字段定义与 FIELDS 必须一致
assert tuple(f.name for f in fields(MemoryCard)) == FIELDS
