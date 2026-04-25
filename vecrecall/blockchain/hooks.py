"""
VecRecall Blockchain — Hooks

对接三个平台的上下文存档触发器：
  OpenClaw  — before_compaction hook（75% 阈值自动触发）
  Hermes    — on_precompact + summarize_session
  Claude Code — 通过 MCP 工具手动/自动触发

触发时机：
  auto_75    上下文使用量达到 75% 时自动触发
  compaction 上下文压缩前强制触发
  session_end 会话结束时触发
  manual     用户手动触发
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _root)

from vecrecall.blockchain.block import Block
from vecrecall.blockchain.chain import BlockChain
from vecrecall.blockchain.indexer import extract_keywords, extract_from_messages


class BlockchainHookBase:
    """所有平台 Hook 的基类"""

    def __init__(
        self,
        chain: BlockChain,
        wing: str = "default",
        context_window_size: int = 2_000_000,   # 默认 200 万 token
        auto_trigger_threshold: float = 0.75,    # 75% 自动触发
    ):
        self._chain = chain
        self._wing = wing
        self._context_window_size = context_window_size
        self._auto_threshold = auto_trigger_threshold
        self._current_token_count = 0

    def _should_auto_trigger(self, token_count: int) -> bool:
        """判断是否达到自动触发阈值（75%）"""
        return token_count >= self._context_window_size * self._auto_threshold

    def _archive(
        self,
        messages: list[dict],
        session_id: str = "",
        trigger: str = "manual",
    ) -> Optional[Block]:
        """存档消息列表为区块"""
        valid = [m for m in messages if m.get("content", "").strip()]
        if not valid:
            return None

        content = "\n".join(
            f"[{m.get('role', 'unknown')}] {m.get('content', '')}"
            for m in valid
        )
        keywords = extract_from_messages(valid)

        block = self._chain.new_block(
            content=content,
            wing=self._wing,
            session_id=session_id or f"session-{int(time.time())}",
            trigger=trigger,
            keywords=keywords,
        )
        return block

    def get_context_for_new_window(
        self,
        query: str = "",
        keywords: list[str] | None = None,
        date: str | None = None,
        max_blocks: int = 3,
    ) -> str:
        """
        为新的上下文窗口生成历史摘要。
        当 AI 进入第二个上下文窗口时调用，注入上一个窗口的关键信息。
        """
        lines = ["## 历史上下文记忆（来自 VecRecall 区块链）\n"]

        if keywords or query:
            # 按关键词检索相关区块
            search_kw = keywords or extract_keywords(query)
            blocks = self._chain.search_by_keywords(
                search_kw, wing=self._wing, limit=max_blocks
            )
            # 检索无结果时降级取最新区块
            if not blocks:
                blocks = self._chain.get_blocks_by_wing(self._wing, limit=max_blocks)
        else:
            # 无检索条件时取最近的区块
            blocks = self._chain.get_blocks_by_wing(self._wing, limit=max_blocks)

        if not blocks:
            return ""

        for block in blocks:
            lines.append(f"### 区块 #{block.index} | {block.date_label}")
            lines.append(f"关键词: {', '.join(block.keywords[:8])}")
            # 只注入前 500 字作为摘要，避免撑满新窗口
            preview = block.content[:500].replace("\n", " ")
            lines.append(f"内容摘要: {preview}...")
            lines.append("")

        stats = self._chain.stats(self._wing)
        lines.append(f"<!-- 区块链总计: {stats['total_blocks']} 个区块, "
                     f"{stats['total_tokens']:,} tokens -->")

        return "\n".join(lines)


# ─────────────────────────────────────────────
# OpenClaw Hook
# ─────────────────────────────────────────────

class OpenClawBlockchainHook(BlockchainHookBase):
    """
    OpenClaw before_compaction hook 集成。

    在 VecClaw 的 on_before_compaction 之后调用，
    把当前上下文存入区块链，然后为新窗口生成历史摘要注入。

    openclaw.json 配置：
      {
        "plugins": {
          "vecclaw_blockchain": {
            "kind": "memory",
            "path": "~/.openclaw/extensions/vecclaw_blockchain",
            "config": {
              "db_path": "~/.vr/blockchain/openclaw.db",
              "wing": "openclaw-agent",
              "context_window_size": 2000000,
              "auto_trigger_threshold": 0.75
            }
          }
        }
      }
    """

    def on_before_compaction(
        self,
        messages: list[dict],
        session_id: str = "",
        token_count: int = 0,
    ) -> dict:
        """
        压缩前触发：存档当前上下文为区块。
        返回注入到新窗口的历史摘要。
        """
        # 存档当前窗口
        block = self._archive(messages, session_id, trigger="compaction")
        if not block:
            return {"inject_context": "", "block": None}

        # 生成历史摘要供新窗口使用
        history_context = self.get_context_for_new_window(
            keywords=block.keywords[:5]
        )

        return {
            "inject_context": history_context,
            "block_id": block.block_id,
            "block_index": block.index,
            "log": f"[VecRecall Blockchain] 区块 #{block.index} 已存档  "
                   f"keywords={block.keywords[:5]}",
        }

    def on_session_end(
        self,
        messages: list[dict],
        session_id: str = "",
        token_count: int = 0,
    ) -> dict:
        """会话结束时存档"""
        block = self._archive(messages, session_id, trigger="session_end")
        if not block:
            return {"block": None}
        return {
            "block_id": block.block_id,
            "block_index": block.index,
            "log": f"[VecRecall Blockchain] 会话结束存档 区块 #{block.index}",
        }

    def check_auto_trigger(
        self,
        messages: list[dict],
        token_count: int,
        session_id: str = "",
    ) -> Optional[dict]:
        """
        在 before_prompt_build 时检查是否达到 75% 阈值。
        达到则自动触发存档。
        """
        if self._should_auto_trigger(token_count):
            block = self._archive(messages, session_id, trigger="auto_75")
            if block:
                return {
                    "triggered": True,
                    "block_id": block.block_id,
                    "log": f"[VecRecall Blockchain] 75% 阈值触发 区块 #{block.index}",
                }
        return None


# ─────────────────────────────────────────────
# Hermes Hook
# ─────────────────────────────────────────────

class HermesBlockchainHook(BlockchainHookBase):
    """
    Hermes Agent on_precompact + summarize_session 集成。

    在 VecHermes 的 summarize_session 之后调用，
    额外把完整上下文存入区块链。

    hermes config.yaml：
      hooks:
        gateway:
          - type: plugin
            path: /path/to/vecrecall/blockchain/hooks.py
            class: HermesBlockchainHook
            config:
              db_path: ~/.vr/blockchain/hermes.db
              wing: hermes-agent
    """

    def on_precompact(
        self,
        messages: list[dict],
        session_id: str = "",
    ) -> dict:
        """上下文压缩前存档"""
        block = self._archive(messages, session_id, trigger="compaction")
        if not block:
            return {"block": None}

        history = self.get_context_for_new_window(keywords=block.keywords[:5])
        return {
            "block_id": block.block_id,
            "inject_context": history,
            "log": f"[VecRecall Blockchain] Hermes precompact 区块 #{block.index}",
        }

    def summarize_session(
        self,
        messages: list[dict],
        session_id: str = "",
    ) -> dict:
        """会话结束时存档（配合 Hermes summarize_session）"""
        block = self._archive(messages, session_id, trigger="session_end")
        if not block:
            return {"block": None, "summary": ""}

        summary = (f"[区块 #{block.index} | {block.date_label}] "
                   f"关键词: {', '.join(block.keywords[:5])}")
        return {
            "block_id": block.block_id,
            "summary": summary,
            "log": f"[VecRecall Blockchain] Hermes session 区块 #{block.index}",
        }


# ─────────────────────────────────────────────
# Claude Code MCP 工具触发器
# ─────────────────────────────────────────────

class ClaudeCodeBlockchainHook(BlockchainHookBase):
    """
    Claude Code MCP 工具触发器。

    通过 vr-mcp 的 MCP 工具供 Claude Code 调用：
      bc_archive     手动存档当前上下文
      bc_context     获取历史区块注入新窗口
      bc_search      按日期+关键词检索区块
      bc_stats       查看区块链状态
      bc_verify      验证哈希链完整性
    """

    def archive(
        self,
        messages: list[dict],
        session_id: str = "",
        trigger: str = "manual",
    ) -> dict:
        """手动存档"""
        block = self._archive(messages, session_id, trigger=trigger)
        if not block:
            return {"ok": False, "reason": "无有效内容"}
        return {
            "ok": True,
            "block_id": block.block_id,
            "block_index": block.index,
            "date_label": block.date_label,
            "token_count": block.token_count,
            "keywords": block.keywords,
            "hash": block.hash[:16] + "...",
        }

    def get_context(
        self,
        query: str = "",
        keywords: list[str] | None = None,
        date: str | None = None,
        max_blocks: int = 3,
    ) -> str:
        """获取历史上下文，注入新窗口"""
        return self.get_context_for_new_window(
            query=query,
            keywords=keywords,
            date=date,
            max_blocks=max_blocks,
        )

    def search(
        self,
        keywords: list[str] | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """按日期+关键词检索区块"""
        blocks = self._chain.search_by_keywords(
            keywords or [],
            wing=self._wing,
            date_start=date_start,
            date_end=date_end,
            limit=limit,
        )
        return [
            {
                "block_id": b.block_id,
                "index": b.index,
                "date_label": b.date_label,
                "token_count": b.token_count,
                "keywords": b.keywords,
                "content_preview": b.content[:200],
                "hash": b.hash[:16] + "...",
            }
            for b in blocks
        ]

    def stats(self) -> dict:
        return self._chain.stats(self._wing)

    def verify(self) -> dict:
        ok, msg = self._chain.verify_chain(self._wing)
        return {"ok": ok, "message": msg}

    def list_dates(self) -> list[str]:
        return self._chain.list_dates(self._wing)


# ─────────────────────────────────────────────
# 统一工厂函数
# ─────────────────────────────────────────────

def create_hook(platform: str, config: dict) -> BlockchainHookBase:
    """
    工厂函数，根据平台创建对应的 Hook。

    platform: "openclaw" | "hermes" | "claude_code"
    """
    db_path = os.path.expanduser(config.get("db_path", "~/.vr/blockchain/chain.db"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    chain = BlockChain(
        db_path=db_path,
        group_size=config.get("group_size", 4),
        group_size_l2=config.get("group_size_l2", 4),
    )
    common = {
        "chain": chain,
        "wing": config.get("wing", "default"),
        "context_window_size": config.get("context_window_size", 2_000_000),
        "auto_trigger_threshold": config.get("auto_trigger_threshold", 0.75),
    }

    if platform == "openclaw":
        return OpenClawBlockchainHook(**common)
    elif platform == "hermes":
        return HermesBlockchainHook(**common)
    elif platform == "claude_code":
        return ClaudeCodeBlockchainHook(**common)
    else:
        raise ValueError(f"未知平台: {platform}，支持: openclaw / hermes / claude_code")
