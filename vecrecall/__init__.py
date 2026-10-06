# VecRecall

from vecrecall.core.engine import VecRecall, Memory, RetrievalResult, ContextBundle

__version__ = "1.0.4"
__all__ = ["VecRecall", "Memory", "RetrievalResult", "ContextBundle"]
from vecrecall.core.engine import (
    VecRecall,
    Memory,
    RetrievalResult,
    ContextBundle,
    VectorBackend,
    NumpyVectorBackend,
    SqliteVectorBackend,
    EmbeddingBackend,
    HashEmbeddingBackend,
    BagOfWordsEmbeddingBackend,
    OllamaEmbeddingBackend,
    APIEmbeddingBackend,
    build_embedding_backend,
    estimate_tokens,
    memory_to_dict,
)
from vecrecall.core.extractor import (
    Extraction,
    MemoryExtractor,
    HeuristicExtractor,
    LLMExtractor,
    build_extractor,
    heuristic_importance,
    heuristic_summary,
)
from vecrecall.core.crypto import Cipher
from vecrecall.core.forgetting import ForgettingCurve
from vecrecall.core.temporal_graph import TemporalGraph, normalize_entity

__version__ = "2.0.0"
__all__ = [
    "VecRecall", "Memory", "RetrievalResult", "ContextBundle",
    "VectorBackend", "NumpyVectorBackend", "SqliteVectorBackend",
    "EmbeddingBackend", "HashEmbeddingBackend", "BagOfWordsEmbeddingBackend",
    "OllamaEmbeddingBackend", "APIEmbeddingBackend",
    "build_embedding_backend",
    "estimate_tokens", "memory_to_dict",
    "Extraction", "MemoryExtractor", "HeuristicExtractor", "LLMExtractor",
    "build_extractor", "heuristic_importance", "heuristic_summary",
    "TemporalGraph", "normalize_entity", "ForgettingCurve", "Cipher",
]
