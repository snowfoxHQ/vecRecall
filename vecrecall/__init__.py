# VecRecall
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
from vecrecall.core.redaction import (
    RedactionResult, RedactionRule, RULES as REDACTION_RULES,
    has_meaningful_payload, redact_memory_payload,
)
from vecrecall.core.writeback import (
    BufferedDecision, EnqueueResult, SqliteOutbox,
    WritebackDecision, WritebackGovernor,
)
from vecrecall.core.recall_policy import (
    RecallDecision, RecallPolicy, AcceptanceResult,
    build_query_intent, decide_recall, evaluate_memory,
)
from vecrecall.repomap import build_repo_map, detect_updates, validate_repo_map

__version__ = "2.2.0"
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
    "RedactionResult", "RedactionRule", "REDACTION_RULES",
    "has_meaningful_payload", "redact_memory_payload",
    "BufferedDecision", "EnqueueResult", "SqliteOutbox",
    "WritebackDecision", "WritebackGovernor",
    "RecallDecision", "RecallPolicy", "AcceptanceResult",
    "build_query_intent", "decide_recall", "evaluate_memory",
    "build_repo_map", "detect_updates", "validate_repo_map",
]
