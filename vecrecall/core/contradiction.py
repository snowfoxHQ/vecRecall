"""矛盾检测（v2.4）：新记忆是否推翻了某条旧记忆。

用法：引擎在 add() 写入新记忆前调用 `detector.judge(old_text, new_text)`，
返回 Verdict 时把旧记忆标记为被取代（superseded_by），检索时降权，不删除。

规则判断的原则是「宁可漏判，不误判」：
  1. 新记忆必须带「变更」信号（改用 / 换成 / 不再 / switched to / no longer ...）；
     弱信号（现在 / now / 目前）要求更高的主题重合度。
     没有信号直接放过，所以绝大多数写入不需要找候选，零额外开销。
  2. 两条记忆讨论的是同一件事：内容词（去掉变更词与停用词）的 Jaccard 重合度够高。
  3. 两边各有对方没有的内容词（旧值被换成了新值）；新记忆只是在旧记忆上加信息
     （旧记忆的内容词全部包含在新记忆里）不算矛盾。

规则覆盖不了的情况（如「我住在西雅图」→「我上个月搬去了波士顿」，没有共同词）
可以传入 judge 函数（例如调用 LLM）替换或补充规则：
    judge(old_text, new_text) -> Verdict | None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from vecrecall.core.lexical import tokenize

# 强变更信号：出现即说明新记忆在改写某个既有事实
_STRONG_ZH = ("改用", "换成", "改成", "改为", "改到", "换到", "换了", "改了", "不再", "不用了",
              "取消了", "废弃", "弃用", "替换", "迁移到", "已经不", "搬到", "搬去", "转到")
_STRONG_EN = ("switched", "switched to", "switch to", "changed to", "change to", "moved to", "migrated to",
              "replaced", "instead of", "no longer", "not anymore", "anymore", "stopped using",
              "rather than", "now use", "now using", "changed my mind", "updated to")
# 弱信号：常见但不一定表示变更，需要更高的重合度
_WEAK_ZH = ("现在", "目前", "如今", "最新")
_WEAK_EN = ("now", "currently", "these days", "latest")

# 变更词本身、以及只表达态度/计划的动词，不算「主题内容」
_CUE_TOKENS = set()
for _w in _STRONG_ZH + _WEAK_ZH + ("决定", "打算", "计划", "觉得", "已经", "我们", "开始", "以后", "之后"):
    _CUE_TOKENS.update(tokenize(_w))
for _w in _STRONG_EN + _WEAK_EN + ("decided", "decide", "plan", "planning", "think", "start", "started",
                                    "use", "using", "used", "switch", "change", "move", "last", "month",
                                    "week", "year", "today", "yesterday"):
    _CUE_TOKENS.update(tokenize(_w))

# 「替换型」信号：明确说出了新值替换旧值（React → Vue），
# 即使新记忆包含旧记忆的全部内容词（因为它同时提到旧值），也算取代
_REPLACE_CUES = ("换成", "改成", "改为", "改用", "替换", "迁移到", "换到", "搬到", "搬去",
                 "switched", "switched to", "switch to", "changed to", "change to",
                 "migrated to", "moved to", "replaced", "updated to")
# 注意：instead of / rather than 后面跟的是被放弃的值（用 A instead of B），
# 新记忆同时提到新旧值时无法判断谁取代谁，所以不算替换型信号

# 「否定型」信号：新记忆复述旧事实并否定它（不再用 X / no longer X），
# 同样允许新记忆包含旧记忆的全部内容词
_NEGATE_CUES = ("不再", "不用了", "取消了", "废弃", "弃用", "已经不",
                "no longer", "not anymore", "anymore", "stopped using")

STRONG_MIN_OVERLAP = 0.2
WEAK_MIN_OVERLAP = 0.4


@dataclass
class Verdict:
    """判断结果：new 取代 old。"""
    confidence: float
    reason: str
    method: str = "rule"
    detail: dict = field(default_factory=dict)


def _has(text_lower: str, cues) -> list[str]:
    out = []
    for c in cues:
        if c.isascii():
            if re.search(r"(?<![a-z])" + re.escape(c) + r"(?![a-z])", text_lower):
                out.append(c)
        elif c in text_lower:
            out.append(c)
    return out


def change_cues(text: str) -> tuple[list[str], list[str]]:
    """返回 (强信号, 弱信号)。"""
    t = (text or "").lower()
    return _has(t, _STRONG_ZH + _STRONG_EN), _has(t, _WEAK_ZH + _WEAK_EN)


def has_change_cue(text: str) -> bool:
    strong, weak = change_cues(text)
    return bool(strong or weak)


_ALL_CUES = sorted(set(_STRONG_ZH + _STRONG_EN + _WEAK_ZH + _WEAK_EN), key=len, reverse=True)


def _strip_cues(text: str) -> str:
    """分词前先把变更词替换成空格：中文按相邻二字切分，
    不去掉的话「用户现在偏好」会产生「户现」「在偏」这类跨越变更词的噪声词。"""
    t = (text or "").lower()
    for c in _ALL_CUES:
        if c.isascii():
            t = re.sub(r"(?<![a-z])" + re.escape(c) + r"(?![a-z])", " ", t)
        else:
            t = t.replace(c, " ")
    return t


def _content_terms(text: str) -> set[str]:
    return {t for t in tokenize(_strip_cues(text)) if t not in _CUE_TOKENS}


class RuleJudge:
    """零依赖规则判断。"""

    def __init__(self, strong_min_overlap: float = STRONG_MIN_OVERLAP,
                 weak_min_overlap: float = WEAK_MIN_OVERLAP):
        self.strong_min_overlap = strong_min_overlap
        self.weak_min_overlap = weak_min_overlap

    def __call__(self, old_text: str, new_text: str) -> Optional[Verdict]:
        strong, weak = change_cues(new_text)
        if not strong and not weak:
            return None
        a, b = _content_terms(old_text), _content_terms(new_text)
        if not a or not b:
            return None
        inter = a & b
        jac = len(inter) / len(a | b)
        need = self.strong_min_overlap if strong else self.weak_min_overlap
        if jac < need:
            return None
        only_old, only_new = a - b, b - a
        negate = any(c in _NEGATE_CUES for c in strong)
        replace = any(c in _REPLACE_CUES for c in strong)
        if not only_new and not negate:       # 没有新值：重复或子集（「不再用 X」除外）
            return None
        cue = (strong or weak)[0]
        if not only_old and not (replace or negate):
            return None                       # 新记忆只是在旧记忆上加信息
        conf = min(0.95, 0.5 + jac * (0.6 if strong else 0.4))
        return Verdict(
            confidence=round(conf, 3),
            reason=f"变更信号「{cue}」，主题重合 {jac:.2f}",
            detail={"cue": cue, "strong": bool(strong), "overlap": round(jac, 3),
                    "removed": sorted(only_old)[:8], "added": sorted(only_new)[:8]},
        )


Judge = Callable[[str, str], Optional[Verdict]]


_LLM_PROMPT = """你在维护一个 AI 的长期记忆库。判断「新记忆」是否推翻了「旧记忆」：
两条说的是同一件事（同一个人 / 项目 / 属性），并且新记忆给出了与旧记忆不同的当前状态，
旧记忆因此过时。只是补充细节、说的是不同的事、或两者可以同时成立，都不算推翻。

旧记忆：{old}
新记忆：{new}

只输出 JSON：{"contradicts": true 或 false, "confidence": 0 到 1 的数字, "reason": "不超过 20 字"}"""


class LLMJudge:
    """用 LLM 判断新记忆是否推翻旧记忆，补规则判断的盲区（如「住在西雅图」→「搬去了波士顿」）。

    chat：prompt → 模型输出文本。通常用 from_extractor() 复用 LLMExtractor 的配置。
    min_confidence：模型给出的置信度低于此值时不算推翻。
    任何异常（网络、超时、JSON 解析）都返回 None，写入永不因此失败。
    """

    def __init__(self, chat: Callable[[str], str], min_confidence: float = 0.7):
        self._chat = chat
        self.min_confidence = min_confidence
        self.calls = 0
        self.errors = 0

    @classmethod
    def from_extractor(cls, extractor, **kw) -> "LLMJudge":
        chat = getattr(extractor, "_chat", None)
        if chat is None:
            raise TypeError("需要 LLMExtractor（或任何带 _chat(prompt) -> str 的对象）")
        return cls(chat, **kw)

    @classmethod
    def from_config(cls, provider: str = "ollama", model: str | None = None,
                    base_url: str | None = None, api_key: str | None = None,
                    timeout: float = 30.0, **kw) -> "LLMJudge":
        from vecrecall.core.extractor import LLMExtractor
        return cls.from_extractor(LLMExtractor(provider=provider, model=model,
                                               base_url=base_url, api_key=api_key,
                                               timeout=timeout), **kw)

    def __call__(self, old_text: str, new_text: str) -> Optional[Verdict]:
        import json
        self.calls += 1
        try:
            raw = self._chat(_LLM_PROMPT.replace("{old}", old_text[:1500])
                             .replace("{new}", new_text[:1500]))
            m = re.search(r"\{.*\}", raw or "", re.S)
            data = json.loads(m.group(0) if m else raw)
            if data.get("contradicts") not in (True, "true", "True", 1):
                return None
            conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
            if conf < self.min_confidence:
                return None
            return Verdict(confidence=round(conf, 3),
                           reason="LLM：" + str(data.get("reason", "")).strip()[:60],
                           method="llm")
        except Exception:  # noqa: BLE001 —— 判断失败等同于不矛盾
            self.errors += 1
            return None


class ContradictionDetector:
    """组合规则判断与可选的外部判断（如 LLM）。

    mode="rule"：只用规则（默认）
    mode="judge"：只用外部 judge
    mode="either"：规则或外部 judge 任一判定即可
    """

    def __init__(self, judge: Judge | None = None, mode: str | None = None,
                 rule: RuleJudge | None = None, judge_min_similarity: float = 0.3,
                 max_judge_calls: int = 2):
        self.rule = rule or RuleJudge()
        self.judge_fn = judge
        # 外部 judge（如 LLM）开销大：只对相似度足够的前几个候选调用
        self.judge_min_similarity = judge_min_similarity
        self.max_judge_calls = max_judge_calls
        self.mode = mode or ("either" if judge else "rule")
        if self.mode not in ("rule", "judge", "either"):
            raise ValueError(f"非法 mode: {self.mode}")
        if self.mode != "rule" and judge is None:
            raise ValueError("mode 为 judge / either 时必须提供 judge")

    def needs_candidates(self, new_text: str) -> bool:
        """是否值得为这条新记忆找候选（规则模式下没有变更信号就跳过）。"""
        return self.mode != "rule" or has_change_cue(new_text)

    def judge(self, old_text: str, new_text: str,
              similarity: float | None = None, budget: list | None = None) -> Optional[Verdict]:
        """similarity：候选与新记忆的向量相似度；budget：本次写入剩余的外部 judge 调用次数（[n]，会被扣减）。"""
        if self.mode in ("rule", "either"):
            v = self.rule(old_text, new_text)
            if v is not None or self.mode == "rule":
                return v
        if similarity is not None and similarity < self.judge_min_similarity:
            return None
        if budget is not None:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
        v = self.judge_fn(old_text, new_text)
        if v is not None and not isinstance(v, Verdict):
            raise TypeError("judge 必须返回 Verdict 或 None")
        if v is not None and v.method == "rule":
            v.method = "judge"
        return v

    def new_budget(self) -> list:
        return [self.max_judge_calls]
