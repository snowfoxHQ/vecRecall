"""
遗忘曲线（Forgetting Curve）—— 记忆强度随时间的衰减模型。

基于艾宾浩斯遗忘曲线：记忆强度按指数衰减，且
  - 重要性越高的记忆衰减越慢（稳定性越大）
  - 被复习过的记忆衰减更慢（间隔效应，spacing effect）

与 Memory OS 范式对齐：检索时用「强度」而非「重要性」衡量记忆价值，
强度跌破阈值即视为「已遗忘」，可进入蒸馏（压缩为抽象长期记忆）或清理。

零依赖：纯标准库 math，与引擎其它模块风格一致。
"""

from __future__ import annotations

import math
import time


class ForgettingCurve:
    """记忆强度衰减模型。强度 R(t) = exp(-age / S)，S 为稳定性（天）。"""

    def __init__(self, base_half_life_days: float = 7.0,
                 importance_boost: float = 7.0,
                 spacing_effect: float = 1.0):
        """
        base_half_life_days  基础半衰期（天）。艾宾浩斯曲线：普通信息约 1 天
                             遗忘大半；此处取 7 天作为「未强化」记忆的半衰期。
        importance_boost     重要性对稳定性的放大系数。
                             S = base * (1 + boost * importance)
        spacing_effect       每次复习对稳定性的倍增。S *= (1 + spacing * reviews)
        """
        self.base_half_life_days = base_half_life_days
        self.importance_boost = importance_boost
        self.spacing_effect = spacing_effect

    def stability(self, importance: float, reviews: int = 0) -> float:
        """记忆稳定性（天）：重要记忆 + 复习过的记忆衰减更慢。"""
        imp = max(0.0, min(1.0, importance))
        return (self.base_half_life_days
                * (1.0 + self.importance_boost * imp)
                * (1.0 + self.spacing_effect * max(0, reviews)))

    def _age_days(self, memory, now: float) -> float:
        """距「最近一次复习（或写入）」的天数。"""
        last = memory.metadata.get("last_review", memory.timestamp)
        return max(0.0, (now - float(last)) / 86400.0)

    def strength(self, memory, now: float | None = None) -> float:
        """当前记忆强度（0-1）：新记忆≈1，随时间和遗忘递减。"""
        now = now if now is not None else time.time()
        reviews = int(memory.metadata.get("reviews", 0) or 0)
        S = self.stability(memory.importance, reviews)
        return math.exp(-self._age_days(memory, now) / S)

    def is_forgotten(self, memory, threshold: float = 0.2,
                     now: float | None = None) -> bool:
        """记忆强度是否已跌破遗忘阈值。"""
        return self.strength(memory, now) < threshold

    def review(self, memory) -> None:
        """复习一次：记录复习时间并增加复习次数（间隔效应减缓遗忘）。

        就地修改 memory.metadata，由调用方负责持久化（save）。
        """
        memory.metadata["reviews"] = int(memory.metadata.get("reviews", 0) or 0) + 1
        memory.metadata["last_review"] = time.time()
