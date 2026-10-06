# VecRecall Blockchain Module
from vecrecall.blockchain.block import Block, BlockGroup
from vecrecall.blockchain.chain import BlockChain
from vecrecall.blockchain.indexer import extract_keywords, extract_from_messages
from vecrecall.blockchain.hooks import (
    OpenClawBlockchainHook,
    HermesBlockchainHook,
    ClaudeCodeBlockchainHook,
    create_hook,
)

__all__ = [
    "Block", "BlockGroup",
    "BlockChain",
    "extract_keywords", "extract_from_messages",
    "OpenClawBlockchainHook",
    "HermesBlockchainHook",
    "ClaudeCodeBlockchainHook",
    "create_hook",
]
