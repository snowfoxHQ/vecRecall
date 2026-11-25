"""词法检索：BM25 倒排索引 + RRF（Reciprocal Rank Fusion）多路融合。

为什么需要：
  - 词法哈希嵌入把每个词随机映射到一个维度，两段文本没有共同词时相似度在 0 附近
    随机波动；长文本里关键词的权重也会被大量无关词稀释。BM25 对精确词匹配更稳，
    并且按 IDF 降低「the / 我 / 的」这类高频词的影响。
  - 线性加权融合（hybrid）要求各路分数在同一尺度上，向量相似度和 BM25 分数做不到。
    RRF 只看名次：score(d) = Σ 1 / (k + rank_i(d))，不关心原始分数，适合融合
    尺度不同的检索结果。

设计：
  - 纯标准库、零依赖；索引只在内存中，由引擎在首次词法检索时从 SQLite 原文构建，
    之后随 add / update / delete / distill 增量维护，reindex 时丢弃重建。
  - 中文按「单字 + 相邻二字」切分；英文按整词，去掉常见停用词，做保守的词尾归一
    （dogs→dog、running→run），不引入外部分词器。
  - 多进程同时写同一个数据目录时，另一个进程新写入的记忆要等本进程重建索引后
    才能被词法检索到（向量检索不受影响）。
"""


import heapq
import math
import re
from collections import Counter
from typing import Iterable, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")

# 英文停用词：只收录几乎不携带信息的功能词，避免误删内容词
_EN_STOP = frozenset("""
a an the and or but if then else of to in on at by for with from as into onto
about over under after before between through during without within
is am are was were be been being do does did doing done have has had having
i me my mine myself we us our ours you your yours he him his she her hers it its
they them their theirs this that these those there here what which who whom whose
when where why how can could would should will shall may might must
not no nor so than too very just also only any some all each both either neither
s t ll ve re d m don doesn didn isn aren wasn weren won wouldn couldn shouldn
please thanks thank okay ok yes yeah hi hello hey
""".split())

# 中文高频虚字：单字时去掉，二字组合中仍保留
_ZH_STOP = frozenset("的了是我你他她它们在和与也就都吗呢吧啊呀哦嗯这那有个着过")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _stem(w: str) -> str:
    """保守的英文词尾归一：让常见的复数、时态变化与原形落到同一个键上。

    目标不是还原正确的词根，而是让「name / named」「hike / hiked」「dog / dogs」
    得到相同的结果；去掉词尾后再统一去掉不发音的 e。
    """
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"                                # stories → story
    elif w.endswith("ing") and len(w) > 5:
        w = w[:-3]
        if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "lsz":
            w = w[:-1]                                  # running → run
    elif w.endswith("eed") and len(w) > 4:
        w = w[:-1]                                      # agreed → agree（need / seed 不动）
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
        if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "lsz":
            w = w[:-1]                                  # stopped → stop
    elif w.endswith("es") and len(w) > 4 and (w[-3] in "sxz" or w[-4:-2] in ("ch", "sh")):
        w = w[:-2]                                      # boxes → box, matches → match
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]                                      # dogs → dog
    if len(w) > 3 and w.endswith("e") and not w.endswith("ee"):
        w = w[:-1]                                      # name / named → nam
    return w


def tokenize(text: str) -> list[str]:
    """BM25 用的分词：英文整词（去停用词 + 词尾归一），中文单字 + 相邻二字。"""
    raw = _TOKEN_RE.findall((text or "").lower())
    out: list[str] = []
    for i, t in enumerate(raw):
        if len(t) == 1 and _is_cjk(t):
            if t not in _ZH_STOP:
                out.append(t)
            nxt = raw[i + 1] if i + 1 < len(raw) else ""
            if len(nxt) == 1 and _is_cjk(nxt):
                out.append(t + nxt)
        else:
            if t in _EN_STOP or (len(t) < 2 and not t.isdigit()):
                continue
            out.append(_stem(t))
    return out


class BM25Index:
    """内存 BM25 倒排索引（Okapi BM25，默认 k1=1.5、b=0.75）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._tf: dict[str, Counter] = {}
        self._len: dict[str, int] = {}
        self._postings: dict[str, set[str]] = {}
        self._total_len = 0

    def __len__(self) -> int:
        return len(self._tf)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._tf

    def add(self, doc_id: str, text: str) -> None:
        """加入或替换一篇文档。"""
        if doc_id in self._tf:
            self.remove(doc_id)
        tf = Counter(tokenize(text))
        self._tf[doc_id] = tf
        n = sum(tf.values())
        self._len[doc_id] = n
        self._total_len += n
        for term in tf:
            self._postings.setdefault(term, set()).add(doc_id)

    def remove(self, doc_id: str) -> None:
        tf = self._tf.pop(doc_id, None)
        if tf is None:
            return
        self._total_len -= self._len.pop(doc_id, 0)
        for term in tf:
            docs = self._postings.get(term)
            if docs is not None:
                docs.discard(doc_id)
                if not docs:
                    del self._postings[term]

    def search(self, query: str, n: int) -> list[tuple[str, float]]:
        """返回 [(doc_id, bm25_score)]，按分数降序；只返回至少命中一个查询词的文档。"""
        N = len(self._tf)
        if N == 0 or n <= 0:
            return []
        avgdl = self._total_len / N or 1.0
        scores: dict[str, float] = {}
        for term in set(tokenize(query)):
            docs = self._postings.get(term)
            if not docs:
                continue
            df = len(docs)
            idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))
            for d in docs:
                f = self._tf[d][term]
                denom = f + self.k1 * (1.0 - self.b + self.b * self._len[d] / avgdl)
                scores[d] = scores.get(d, 0.0) + idf * f * (self.k1 + 1.0) / denom
        return heapq.nlargest(n, scores.items(), key=lambda kv: kv[1])


def rrf_merge(rankings: Sequence[Iterable[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：按名次融合多路排序结果。

    rankings：每一路是按相关度降序排列的 id 序列。
    返回 [(id, rrf_score)]，按分数降序；分数相同时，先出现在较前一路的排前面。
    k=60 是原论文（Cormack et al., 2009）的常用取值。
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}
    for li, ranking in enumerate(rankings):
        seen: set[str] = set()
        rank = 0
        for doc_id in ranking:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            rank += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(doc_id, (li, rank))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
