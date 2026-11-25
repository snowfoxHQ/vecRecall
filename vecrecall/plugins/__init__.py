"""可选插件：通过 vecrecall.core.hooks 挂载，不改动核心行为。"""

from vecrecall.plugins.trace import TracePlugin, TraceStore, enable_trace
from vecrecall.plugins.memory_card import (
    MemoryCardGate, enable_memory_cards, list_cards, parse_card, validate_card,
)

__all__ = [
    "TracePlugin", "TraceStore", "enable_trace",
    "MemoryCardGate", "enable_memory_cards", "list_cards", "parse_card", "validate_card",
]
