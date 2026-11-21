"""检索决策与结果验收。

把 MemoraX Code 的 Skill 决策规则（何时 Search、如何构造查询、结果三项验收）
落成 vecRecall 的本地、可测试函数。不依赖任何云服务；判定器是确定性启发式，
可用 ``decider`` 参数替换为本地 LLM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class RecallDecision:
    should_recall: bool
    reason: str
    signals: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "should_recall": self.should_recall,
            "reason": self.reason,
            "signals": list(self.signals),
            "confidence": round(self.confidence, 3),
        }


@dataclass
class AcceptanceResult:
    accepted: bool
    checks: dict[str, bool]
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "checks": dict(self.checks),
            "reason": self.reason,
        }


_HISTORY = (
    "之前", "以前", "上次", "记得", "当初", "历史", "原先", "早先", "过去",
    "previous", "earlier", "last time", "before", "remember", "history",
    "originally", "regression",
)
_DECISION = (
    "为什么", "为何", "设计理由", "决策", "失败", "踩坑", "修复", "经验", "教训",
    "约定", "规范", "方案", "根因", "原因", "别再", "避免重复", "回归",
    "why", "rationale", "decision", "failed", "failure", "fix", "pitfall",
    "lesson", "root cause", "convention", "agreement", "regression", "avoid",
)
_WORK = (
    "实现", "调试", "重构", "迁移", "审查", "评审", "审计", "设计", "api", "schema",
    "parser", "workflow", "协议", "兼容", "升级",
    "implement", "debug", "refactor", "migrate", "migration", "review", "audit",
    "api", "schema", "parser", "workflow", "contract", "upgrade",
)
_SKIP = (
    "你好", "谢谢", "嗨", "嗨喽", "谢谢啦",
    "hello", "hi", "thanks", "thank you", "good morning",
    "typo", "错别字", "拼写", "改个", "换个写法", "格式化一下", "rename this",
    "format this", "just change", "one-shot", "一次性",
    "现在几点", "what time is it", "list files", "run ls",
)
_ACTIONABLE = (
    "修复", "解决", "因为", "应该", "需要", "验证", "建议", "原因", "检查", "改用", "避免",
    "fix", "resolve", "because", "should", "must", "validate", "verify", "suggest",
    "root cause", "use ", "avoid", "check", "instead",
)
_BEHAVIOR = (
    "错误", "异常", "失败", "症状", "超时", "崩溃", "报错", "回归", "不一致", "现象",
    "error", "exception", "fail", "symptom", "timeout", "crash", "regression",
    "inconsistent", "behavior", "broken", "stale",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokens(text: str) -> set[str]:
    text = _norm(text)
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in han:
        if len(chunk) == 1:
            words.add(chunk)
        else:
            words.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return words


def _matched(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = _norm(text)
    return [phrase for phrase in phrases if phrase in lowered]


@dataclass(frozen=True)
class RecallPolicy:
    """阈值与词表可覆盖，便于针对具体项目调参。"""
    history: tuple[str, ...] = _HISTORY
    decision: tuple[str, ...] = _DECISION
    work: tuple[str, ...] = _WORK
    skip: tuple[str, ...] = _SKIP
    min_confidence: float = 0.35


DEFAULT_POLICY = RecallPolicy()


def decide_recall(
    query: str,
    context: str | None = None,
    policy: RecallPolicy = DEFAULT_POLICY,
    decider: Callable[[str, str | None], float | None] | None = None,
) -> RecallDecision:
    """决定当前任务是否需要检索历史记忆。

    ``decider`` 返回 0..1 的概率；不可用时回退到确定性启发式。
    """
    if not isinstance(query, str) or not query.strip():
        return RecallDecision(False, "empty_query")

    signals: list[str] = []
    skip_hits = _matched(query, policy.skip)
    history_hits = _matched(query, policy.history)
    decision_hits = _matched(query, policy.decision)
    work_hits = _matched(query, policy.work)
    if skip_hits:
        signals.append("skip_phrase")
    if history_hits:
        signals.append("history_dependency")
    if decision_hits:
        signals.append("decision_dependency")
    if work_hits:
        signals.append("lifecycle_work")

    if decider is not None:
        try:
            probability = decider(query, context)
        except Exception:
            probability = None
        if probability is not None and 0.0 <= probability <= 1.0:
            should = probability >= 0.5 and not skip_hits
            reason = "model_decision" if should else "model_skip"
            return RecallDecision(should, reason, signals + ["model"], float(probability))

    if skip_hits and not history_hits and not work_hits:
        return RecallDecision(False, "self_contained_or_trivial", signals, 0.9)

    # "previous/remember" 本身不够：若上下文已含锚点且无需决策依据，则不检索。
    context_norm = _norm(context or "")
    anchors = _tokens(query) - _tokens(" ".join(policy.history))
    known = bool(context_norm) and any(token in _tokens(context) for token in anchors)
    if known and not decision_hits:
        return RecallDecision(False, "context_already_contains_facts", signals + ["known_anchor"], 0.8)

    confidence = 0.2
    if history_hits:
        confidence += 0.3
    if decision_hits:
        confidence += 0.35
    if work_hits:
        confidence += 0.2
    if context_norm and not known:
        confidence += 0.05
    confidence = min(0.99, confidence)
    should = confidence >= policy.min_confidence
    reason = "historical_dependency" if should else "insufficient_signal"
    return RecallDecision(should, reason, signals, confidence)


def build_query_intent(query: str, context: str | None = None) -> str:
    """把碎片化请求整理为 `<target>: <condition>，<decision>?` 形式。"""
    text = re.sub(r"\s+", " ", (query or "").strip())
    if not text:
        return ""
    if "：" in text or ":" in text or "?" in text or "？" in text:
        return text
    condition_tokens = _matched(text, _HISTORY + _DECISION + _WORK)
    decision_word = "决策/结论是什么？" if _matched(text, _DECISION) else "可复用的经验是什么？"
    if condition_tokens:
        return f"{text}: {','.join(condition_tokens)}，{decision_word}"
    return f"{text}: 当前任务，{decision_word}"


def evaluate_memory(
    candidate: str | dict,
    query: str,
    context: str | None = None,
) -> AcceptanceResult:
    """三项验收：同边界、同行为、能改变下一步动作。"""
    if isinstance(candidate, dict):
        parts = [str(candidate.get(key, "")) for key in
                 ("content", "topic", "wing", "ui_summary", "title", "text")]
        text = " ".join(part for part in parts if part)
    else:
        text = str(candidate or "")

    q_tokens = _tokens(query)
    c_tokens = _tokens(text)
    if not q_tokens or not c_tokens:
        return AcceptanceResult(False, {"same_boundary": False, "same_behavior": False, "actionable": False}, "empty_input")

    overlap = q_tokens & c_tokens
    same_boundary = bool(overlap)
    same_behavior = bool(
        (_matched(query, _BEHAVIOR) and _matched(text, _BEHAVIOR))
        or (overlap & _tokens(" ".join(_BEHAVIOR)))
    )
    actionable = bool(_matched(text, _ACTIONABLE))
    checks = {
        "same_boundary": same_boundary,
        "same_behavior": bool(same_behavior),
        "actionable": actionable,
    }
    accepted = all(checks.values())
    reason = "three_checks_passed" if accepted else "three_checks_failed"
    return AcceptanceResult(accepted, checks, reason)
