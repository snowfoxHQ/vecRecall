"""嵌入后端（可插拔）：词法 BagOfWords（默认）/ SentenceTransformer / Ollama / OpenAI 兼容 API。"""

from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sqlite3  # noqa: F401
import struct  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
import urllib.error  # noqa: F401
import urllib.request  # noqa: F401
from dataclasses import dataclass, field, asdict  # noqa: F401
from typing import Iterator, Optional  # noqa: F401


# ─────────────────────────────────────────────
# 嵌入适配器（可插拔）
# ─────────────────────────────────────────────

class EmbeddingBackend:
    """embed() 用于写入的文档；embed_query() 用于检索查询。

    对称模型（MiniLM、词法嵌入）两者相同。检索专用的非对称模型（e5 / bge）
    要求查询和文档加不同前缀，子类覆盖 embed_query() 即可。
    """

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)


class HashEmbeddingBackend(EmbeddingBackend):
    """确定性哈希向量，仅供测试，无语义"""

    DIM = 128

    def embed(self, text: str) -> list[float]:
        import struct
        h = hashlib.sha256(text.encode()).digest()
        vals = struct.unpack("f" * (len(h) // 4), h)
        # 填充或裁剪到 DIM
        v = list(vals)
        while len(v) < self.DIM:
            v.extend(v)
        v = v[:self.DIM]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]


class BagOfWordsEmbeddingBackend(EmbeddingBackend):
    """
    零依赖语义嵌入（特征哈希 / hashing trick，含 subword 增强）。

    中文拆成单字 + 相邻二元组 + 三元组；英文/数字按整词，
    并额外拆分字符 3-gram / 4-gram（subword），以捕获词形变化
    与拼写变体（如 running/run、database/databases、颜色/色彩）。
    哈希到固定维度并带正负号，余弦相似度 ≈ 词面 + 子词重合度，
    是零依赖（无 sentence-transformers / Ollama / API）下最强的
    语义召回，且无状态、确定性、无外部依赖。
    """

    DIM = 512

    _TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        return '\u4e00' <= ch <= '\u9fff'

    def _tokens(self, text: str) -> list[str]:
        raw = self._TOKEN_RE.findall(text.lower())
        out: list[str] = []
        for i, t in enumerate(raw):
            if len(t) == 1 and self._is_cjk(t):
                # 单字
                out.append(t)
                # 相邻中文二元组 / 三元组（捕获词序信息）
                if i + 1 < len(raw) and len(raw[i + 1]) == 1 \
                        and self._is_cjk(raw[i + 1]):
                    out.append(t + raw[i + 1])
                    if i + 2 < len(raw) and len(raw[i + 2]) == 1 \
                            and self._is_cjk(raw[i + 2]):
                        out.append(t + raw[i + 1] + raw[i + 2])
            else:
                # 整词
                out.append(t)
                # 英文字符 3-gram / 4-gram（subword，捕获词形与拼写变体）
                if len(t) >= 4:
                    for j in range(len(t) - 2):
                        out.append("#" + t[j:j + 3])
                if len(t) >= 6:
                    for j in range(len(t) - 3):
                        out.append("#" + t[j:j + 4])
        return out

    def embed(self, text: str) -> list[float]:
        tf: dict[str, int] = {}
        for t in self._tokens(text):
            tf[t] = tf.get(t, 0) + 1

        v = [0.0] * self.DIM
        for token, freq in tf.items():
            weight = 1.0 + math.log(freq)
            h = hashlib.sha256(token.encode()).digest()
            idx = ((h[0] << 8) | h[1]) % self.DIM
            sign = 1.0 if h[2] & 1 else -1.0
            v[idx] += sign * weight

        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


# 默认本地语义模型：多语言 MiniLM（384 维，中英等 50+ 语言友好）
DEFAULT_SENTENCE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# 检索专用模型的前缀约定（模型卡要求）：(查询前缀, 文档前缀)。按模型名子串匹配。
MODEL_PREFIXES = {
    "multilingual-e5": ("query: ", "passage: "),
    "e5-": ("query: ", "passage: "),
    "bge-small-zh": ("为这个句子生成表示以用于检索相关文章：", ""),
    "bge-base-zh": ("为这个句子生成表示以用于检索相关文章：", ""),
    "bge-large-zh": ("为这个句子生成表示以用于检索相关文章：", ""),
    "bge-small-en": ("Represent this sentence for searching relevant passages: ", ""),
    "bge-base-en": ("Represent this sentence for searching relevant passages: ", ""),
    "bge-large-en": ("Represent this sentence for searching relevant passages: ", ""),
}


def prefixes_for(model: str) -> tuple[str, str]:
    """返回模型的 (查询前缀, 文档前缀)；未知模型（含 bge-m3、MiniLM）不加前缀。"""
    name = (model or "").lower()
    for key, pre in MODEL_PREFIXES.items():
        if key in name:
            return pre
    return ("", "")

try:
    from sentence_transformers import SentenceTransformer

    class SentenceTransformerBackend(EmbeddingBackend):
        """本地 sentence-transformers 模型。

        e5 / bge 等检索专用模型按 MODEL_PREFIXES 自动加查询 / 文档前缀；
        也可以用 query_prefix / doc_prefix 显式指定（传 "" 表示不加）。
        """

        def __init__(self, model: str = DEFAULT_SENTENCE_MODEL,
                     query_prefix: str | None = None, doc_prefix: str | None = None):
            self.model_name = model
            self._model = SentenceTransformer(model)
            qp, dp = prefixes_for(model)
            self.query_prefix = qp if query_prefix is None else query_prefix
            self.doc_prefix = dp if doc_prefix is None else doc_prefix

        def embed(self, text: str) -> list[float]:
            return self._model.encode(self.doc_prefix + text, normalize_embeddings=True).tolist()

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return self._model.encode([self.doc_prefix + t for t in texts],
                                      normalize_embeddings=True).tolist()

        def embed_query(self, text: str) -> list[float]:
            return self._model.encode(self.query_prefix + text, normalize_embeddings=True).tolist()

        def embed_query_batch(self, texts: list[str]) -> list[list[float]]:
            return self._model.encode([self.query_prefix + t for t in texts],
                                      normalize_embeddings=True).tolist()

except ImportError:
    SentenceTransformerBackend = None  # type: ignore


class OllamaEmbeddingBackend(EmbeddingBackend):
    """
    本地 Ollama 嵌入后端（零 API Key，数据不出本机）。

    依赖本地运行的 Ollama 服务（默认 http://localhost:11434），
    推荐模型：nomic-embed-text / bge-m3（中文）。

    优先走新版 /api/embed（支持批量），旧版 Ollama 自动回退到
    /api/embeddings（单条）。
    """

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434",
                 timeout: float = 60.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 新版 /api/embed 支持批量
        try:
            data = self._post("/api/embed", {"model": self.model, "input": texts})
            if "embeddings" in data:
                return [list(v) for v in data["embeddings"]]
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass  # 旧版 Ollama，回退单条接口

        # 回退旧版 /api/embeddings（仅单条）
        out: list[list[float]] = []
        for t in texts:
            data = self._post("/api/embeddings", {"model": self.model, "prompt": t})
            out.append(list(data["embedding"]))
        return out


class APIEmbeddingBackend(EmbeddingBackend):
    """
    OpenAI 兼容远程嵌入 API 后端。

    可指向 OpenAI、或任意 OpenAI 兼容的嵌入服务
    （硅基流动 / DeepSeek / 通义 / 本地 vLLM 等），
    只需调整 base_url / api_key / model。
    """

    def __init__(self, model: str = "text-embedding-3-small",
                 api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 60.0):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._post({"model": self.model, "input": texts})
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        return [list(item["embedding"]) for item in items]


def build_embedding_backend(kind: str = "auto", **kwargs) -> EmbeddingBackend:
    """
    按名称构造嵌入后端（供 CLI / MCP / REST / Python API 统一使用）。

    kind 取值：
      auto     → 优先 sentence-transformers，否则词法 BagOfWords（零依赖）
      bow      → BagOfWordsEmbeddingBackend（零依赖词法语义）
      sentence → SentenceTransformerBackend（本地模型）
      ollama   → OllamaEmbeddingBackend（本地 Ollama，零 API Key）
      api      → APIEmbeddingBackend（OpenAI 兼容远程 API）

    通用 kwargs：model / base_url / api_key
    """
    kind = (kind or "auto").lower()
    if kind == "bow":
        return BagOfWordsEmbeddingBackend()
    if kind == "sentence":
        if SentenceTransformerBackend is None:
            raise RuntimeError(
                "sentence-transformers 未安装，请 `pip install sentence-transformers`")
        return SentenceTransformerBackend(kwargs.get("model") or DEFAULT_SENTENCE_MODEL,
                                          query_prefix=kwargs.get("query_prefix"),
                                          doc_prefix=kwargs.get("doc_prefix"))
    if kind == "ollama":
        return OllamaEmbeddingBackend(
            model=kwargs.get("model") or "nomic-embed-text",
            base_url=kwargs.get("base_url") or kwargs.get("url") or "http://localhost:11434",
            timeout=kwargs.get("timeout") or 60.0,
        )
    if kind == "api":
        return APIEmbeddingBackend(
            model=kwargs.get("model") or "text-embedding-3-small",
            api_key=kwargs.get("api_key"),
            base_url=kwargs.get("base_url") or kwargs.get("url") or "https://api.openai.com/v1",
            timeout=kwargs.get("timeout") or 60.0,
        )
    # auto
    if SentenceTransformerBackend is not None:
        return SentenceTransformerBackend()
    return BagOfWordsEmbeddingBackend()
