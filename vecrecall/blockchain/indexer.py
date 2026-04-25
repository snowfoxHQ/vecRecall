"""
VecRecall Blockchain — Indexer 关键词提取器

从对话内容中自动提取关键词，作为区块的检索凭证。
支持中英文混合内容，无需外部 NLP 依赖。
"""

from __future__ import annotations

import re
from collections import Counter


# 中文停用词
ZH_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
    "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
    "着", "没有", "看", "好", "自己", "这", "那", "什么", "这个", "那个",
    "可以", "但是", "因为", "所以", "如果", "然后", "还是", "已经", "对",
    "从", "被", "让", "把", "比", "用", "来", "他", "她", "它", "们",
    "这样", "那样", "时候", "问题", "方式", "需要", "可能", "应该", "通过",
}

# 英文停用词
EN_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "i", "you", "he", "she",
    "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "this", "that", "these", "those",
    "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "as", "into", "through", "about", "so", "if", "then",
    "what", "how", "when", "where", "why", "which", "who",
}

# 高权重关键词（出现即提升优先级）
HIGH_VALUE_PATTERNS = [
    r'\b(?:decided?|decision|架构|architecture|deploy|部署|migration|迁移)\b',
    r'\b(?:bug|fix|error|issue|problem|问题|修复|错误)\b',
    r'\b(?:version|v\d+\.\d+|版本)\b',
    r'\b(?:database|db|sql|postgresql|mysql|mongodb|数据库)\b',
    r'\b(?:api|mcp|llm|ai|model|模型)\b',
    r'\b(?:important|critical|key|核心|重要|关键)\b',
]


class KeywordExtractor:
    """
    从对话内容提取关键词。

    策略：
      1. 提取中文词组（2-6 字）
      2. 提取英文单词（3 字符以上）
      3. 过滤停用词
      4. 高权重词优先
      5. 按频率排序，取 top-N
    """

    def __init__(self, top_n: int = 15):
        self._top_n = top_n
        self._high_value_re = re.compile(
            "|".join(HIGH_VALUE_PATTERNS), re.IGNORECASE
        )

    def extract(self, text: str) -> list[str]:
        """从文本提取关键词列表"""
        if not text:
            return []

        keywords = {}

        # 1. 提取英文单词
        en_words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_\-]{2,}\b', text)
        for word in en_words:
            w = word.lower()
            if w not in EN_STOPWORDS and len(w) >= 3:
                keywords[w] = keywords.get(w, 0) + 1

        # 2. 提取中文词组（滑动窗口，2-4 字，覆盖子词）
        zh_all = re.findall(r'[\u4e00-\u9fff]+', text)
        zh_chunks = []
        for seg in zh_all:
            # 滑动窗口提取 2-4 字子词
            for size in (2, 3, 4):
                for start in range(len(seg) - size + 1):
                    zh_chunks.append(seg[start:start+size])
        for chunk in zh_chunks:
            if chunk not in ZH_STOPWORDS:
                keywords[chunk] = keywords.get(chunk, 0) + 1

        # 3. 高权重词加分
        for match in self._high_value_re.finditer(text):
            word = match.group().lower()
            keywords[word] = keywords.get(word, 0) + 5

        # 4. 按频率排序
        sorted_kw = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
        return [kw for kw, _ in sorted_kw[:self._top_n]]

    def extract_from_messages(self, messages: list[dict]) -> list[str]:
        """从对话消息列表提取关键词"""
        combined = " ".join(
            m.get("content", "") for m in messages
            if m.get("content", "").strip()
        )
        return self.extract(combined)

    def extract_date_keywords(self, text: str) -> tuple[list[str], str]:
        """
        提取关键词和日期标签。
        返回 (keywords, date_label)
        """
        from datetime import datetime
        keywords = self.extract(text)
        date_label = datetime.now().strftime("%Y-%m-%d")
        return keywords, date_label


# 全局默认提取器
_default_extractor = KeywordExtractor(top_n=15)


def extract_keywords(text: str) -> list[str]:
    """快捷函数：从文本提取关键词"""
    return _default_extractor.extract(text)


def extract_from_messages(messages: list[dict]) -> list[str]:
    """快捷函数：从消息列表提取关键词"""
    return _default_extractor.extract_from_messages(messages)
