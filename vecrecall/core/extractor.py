"""
LLM 认知层 —— 可插拔记忆抽取器（MemoryExtractor）。

写入记忆时自动抽取结构化信息：
  topic       话题标签（组织用）
  importance  重要性评分（0-1）
  ui_summary  AAAK 摘要（仅 UI 展示，不参与检索）
  entities    关键实体（人名/项目/技术/公司等）
  relations   实体间关系（为时序知识图谱预留）

两种实现：
  HeuristicExtractor  零依赖启发式（默认，词法关键词 + 英文专有名词）
  LLMExtractor        调用 Ollama / OpenAI 兼容 API，失败自动降级启发式

与嵌入后端保持一致：LLM 调用走标准库 urllib，零新增依赖。
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class Extraction:
    """一次记忆抽取的结构化结果。"""
    topic: str = "general"
    importance: float = 0.5
    ui_summary: str = ""
    entities: list[str] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    entity_states: list[dict] = field(default_factory=list)  # 实体属性状态（时序图谱）


# ─────────────────────────────────────────────
# 启发式辅助（零依赖）
# ─────────────────────────────────────────────

_IMPORTANCE_KEYWORDS = [
    "决定", "架构", "bug", "部署", "重要", "critical",
    "decision", "architecture", "deploy", "migration",
]


def heuristic_importance(content: str) -> float:
    """启发式重要性评分：长度 + 关键词。"""
    score = min(len(content) / 2000, 0.5)
    low = content.lower()
    for kw in _IMPORTANCE_KEYWORDS:
        if kw.lower() in low:
            score += 0.1
    return min(score, 1.0)


def heuristic_summary(content: str, max_len: int = 120) -> str:
    """启发式摘要：压缩空白后截断。"""
    text = " ".join(content.split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


_TOPIC_KEYWORDS = [
    ("architecture", ["架构", "architecture", "微服务", "microservice", "模块", "module"]),
    ("decision", ["决定", "决策", "decision", "采用", "选择", "方案"]),
    ("ops", ["部署", "deploy", "bug", "运维", "迁移", "migration", "上线"]),
    ("security", ["安全", "security", "认证", "auth", "加密", "encrypt", "密钥"]),
    ("data", ["数据", "data", "数据库", "database", "索引", "index"]),
]

# 英文专有名词（连续大写开头词），作为启发式实体
_ENGLISH_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z]{1,}(?:\s+[A-Z][a-zA-Z]{1,})*\b")


# ─────────────────────────────────────────────
# 抽象基类 + 实现
# ─────────────────────────────────────────────

class MemoryExtractor:
    """记忆抽取器抽象基类。"""

    def extract(self, content: str) -> Extraction:
        raise NotImplementedError

    def summarize(self, texts: list[str]) -> str:
        """把多条相关记忆压缩成一条抽象摘要（主动蒸馏用）。默认启发式拼接。"""
        parts = [heuristic_summary(t, 60) for t in texts if t]
        return "；".join(parts)[:500]


class HeuristicExtractor(MemoryExtractor):
    """零依赖启发式抽取：关键词话题 + 启发式重要性 + 英文专有名词实体。"""

    def extract(self, content: str) -> Extraction:
        low = content.lower()
        topic = "general"
        for t, kws in _TOPIC_KEYWORDS:
            if any(k in low for k in kws):
                topic = t
                break
        entities = list(dict.fromkeys(_ENGLISH_ENTITY_RE.findall(content)))[:20]
        # 零依赖下的「边」：同段记忆中共现的实体两两建立弱关系
        relations = []
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                relations.append({"source": entities[i], "target": entities[j],
                                  "type": "co-occurs"})
                if len(relations) >= 45:
                    break
            if len(relations) >= 45:
                break
        return Extraction(
            topic=topic,
            importance=heuristic_importance(content),
            ui_summary=heuristic_summary(content),
            entities=entities,
            relations=relations,
        )


_PROMPT = """你是记忆抽取器。从下面这段内容中提取结构化信息，只输出一个 JSON 对象，不要输出任何其他文字或解释。

JSON 结构要求：
{
  "topic": "简短话题标签（英文小写，如 architecture / decision / ops / security / data，无归属用 general）",
  "importance": 0.8,
  "summary": "一句话中文摘要（不超过 50 字）",
  "entities": ["实体1", "实体2"],
  "relations": [{"source": "实体A", "target": "实体B", "type": "关系类型"}],
  "entity_states": [{"entity": "实体名", "attribute": "属性名", "value": "属性值"}]
}

内容如下：
{content}"""


_SUMMARIZE_PROMPT = """你是记忆蒸馏器。把下面多条相关记忆压缩成一条简洁、信息完整的长期记忆（中文，不超过 150 字），保留关键事实、决策与结论，去除重复与冗余：

{texts}

只输出总结文本本身，不要任何前缀、标题或解释。"""


class LLMExtractor(MemoryExtractor):
    """调用 LLM 抽取结构化记忆，失败时自动降级启发式（保证写入永不阻塞）。"""

    def __init__(self, provider: str = "ollama", model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 30.0,
                 fallback: MemoryExtractor | None = None):
        self.provider = provider
        self.model = model or ("llama3.2" if provider == "ollama"
                               else "gpt-4o-mini")
        self.base_url = (base_url or
                         ("http://127.0.0.1:11434" if provider == "ollama"
                          else "https://api.openai.com/v1"))
        self.api_key = api_key
        self.timeout = timeout
        self.fallback = fallback or HeuristicExtractor()

    def extract(self, content: str) -> Extraction:
        try:
            raw = self._chat(_PROMPT.replace("{content}", content))
            data = json.loads(raw)
            return Extraction(
                topic=str(data.get("topic", "general")).strip().lower() or "general",
                importance=max(0.0, min(1.0, float(data.get("importance", 0.5)))),
                ui_summary=str(data.get("summary", "")).strip(),
                entities=[str(e) for e in data.get("entities", [])][:50],
                relations=[r for r in data.get("relations", [])
                           if isinstance(r, dict)][:50],
                entity_states=self._parse_entity_states(
                    data.get("entity_states", [])),
            )
        except Exception:
            # 网络失败 / JSON 解析失败 / 模型异常 → 降级启发式，写入不阻塞
            return self.fallback.extract(content)

    def _parse_entity_states(self, raw: list) -> list[dict]:
        """统一展平 entity_states 为 [{entity, attribute, value}] 形式。"""
        out = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            ent = str(s.get("entity", "")).strip()
            if not ent:
                continue
            attr = s.get("attribute")
            if attr:
                out.append({"entity": ent, "attribute": str(attr).strip(),
                            "value": str(s.get("value", "")).strip()})
            elif isinstance(s.get("attributes"), dict):
                for k, v in s["attributes"].items():
                    out.append({"entity": ent, "attribute": str(k).strip(),
                                "value": str(v).strip()})
        return out[:100]

    def summarize(self, texts: list[str]) -> str:
        """调用 LLM 把多条记忆蒸馏成一条抽象长期记忆，失败降级启发式拼接。"""
        try:
            joined = "\n".join(f"- {t}" for t in texts if t)
            out = self._chat(_SUMMARIZE_PROMPT.replace("{texts}", joined)).strip()
            return out or super().summarize(texts)
        except Exception:
            return super().summarize(texts)

    def _chat(self, prompt: str) -> str:
        """发送聊天请求并返回模型文本输出（可被测试 mock）。"""
        if self.provider == "ollama":
            return self._chat_ollama(prompt)
        return self._chat_openai(prompt)

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _chat_ollama(self, prompt: str) -> str:
        url = self.base_url.rstrip("/") + "/api/chat"
        payload = {"model": self.model, "stream": False, "format": "json",
                   "messages": [{"role": "user", "content": prompt}]}
        data = self._post_json(url, payload)
        return str(data.get("message", {}).get("content", ""))

    def _chat_openai(self, prompt: str) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_object"}}
        data = self._post_json(url, payload, headers)
        return str(data["choices"][0]["message"]["content"])


# ─────────────────────────────────────────────
# 工厂
# ─────────────────────────────────────────────

def build_extractor(kind: str = "heuristic", **kwargs) -> MemoryExtractor:
    """构造记忆抽取器。

    kind: heuristic（默认，零依赖） / ollama / api
    kwargs 透传 model / base_url / api_key / timeout。
    """
    if kind in (None, "", "heuristic", "none", "off"):
        return HeuristicExtractor()
    if kind in ("ollama", "api"):
        provider = kind
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return LLMExtractor(provider=provider, **clean)
    raise ValueError(f"未知抽取器类型: {kind}")
