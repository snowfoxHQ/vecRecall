"""推理链与反思（移植自 HMR ThoughtChain，可选模块）。

记录「为什么这样决定」：一次推理任务是一条链，链上按顺序挂观察、假设、决策、
行动等节点；事后用实际结果做反思，标出哪些决策对、哪些错，并把有学习价值的
结论写回长期记忆。下次遇到相似目标时，可以直接查到历史上被验证有效的决策。

与 HMR 原版的区别：
  - 不依赖 pydantic / numpy，链的持久化复用 VecRecall 本身：
    每条链是独立推理库（<base_dir>/reasoning/）里的一条记忆，正文是目标，
    链的完整状态放在 metadata，重启后直接从 SQLite 读回，不再另存一套 JSON。
  - 推理库与主记忆库分开：推理过程不会出现在普通的 search() 结果里；
    只有反思得出的结论会写入主库（memory_type="semantic"，topic="reasoning_insight"）。
  - 找相似推理链用 rrf 检索取候选，再按词重叠过滤（默认 ≥ 0.3，与 HMR 相同），
    取代原来遍历全部链做关键词匹配；没有足够重叠时宁可不返回，避免给出无关的「历史经验」。
  - 有预期结果时，评分用 BM25 的分词计算重叠，原版按空格切词对中文无效。
  - 结论写入主库失败时，保留在链上（pending_insight），可用 retry_pending_insights() 补写。
  - 主库开启静态加密时，推理库沿用同一个密钥对象加密 metadata。

用法：
    from vecrecall.reasoning import ReasoningEngine, ThoughtType
    eng = ReasoningEngine(palace)
    chain = eng.start("排查 IPC 超时", expected_outcome="延迟降到 100ms 以下")
    eng.think(chain.chain_id, "队列深度持续 > 800", ThoughtType.OBSERVATION)
    eng.think(chain.chain_id, "消费者线程不足", ThoughtType.HYPOTHESIS)
    eng.think(chain.chain_id, "扩容消费者线程 x3", ThoughtType.DECISION)
    result = eng.reflect(chain.chain_id, "延迟从 800ms 降到 50ms", rating=0.9)
    eng.best_decision_for("IPC 积压")      # → 「扩容消费者线程 x3」及准确率
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from vecrecall.core.lexical import tokenize
from vecrecall.core.vectors import NumpyVectorBackend, SqliteVectorBackend

REASONING_TOPIC = "reasoning_chain"
INSIGHT_TOPIC = "reasoning_insight"


class ThoughtType(str, Enum):
    OBSERVATION = "observation"   # 观察到的事实
    HYPOTHESIS = "hypothesis"     # 可能的原因或方案
    DECISION = "decision"         # 选定的方案
    ACTION = "action"             # 具体执行了什么
    OUTCOME = "outcome"           # 行动的结果
    REFLECTION = "reflection"     # 事后回顾
    INSIGHT = "insight"           # 提炼出的通用结论


class ChainStatus(str, Enum):
    OPEN = "open"         # 正在思考
    ACTIVE = "active"     # 已执行行动，等待结果
    CLOSED = "closed"     # 已反思


# 不参与对错评判的节点类型
_UNJUDGED = {ThoughtType.OBSERVATION.value, ThoughtType.OUTCOME.value}


@dataclass
class Thought:
    thought_id: str
    thought_type: str
    content: str
    confidence: float = 0.7
    parent_id: Optional[str] = None
    memory_ids: list[str] = field(default_factory=list)
    was_correct: Optional[bool] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class Reflection:
    rating: float                       # 结果评分 0–1（人工给定或推断）
    accuracy: float                     # 可评判节点中判为正确的比例
    correct_nodes: list[str] = field(default_factory=list)
    wrong_nodes: list[str] = field(default_factory=list)
    key_mistakes: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    suggested_memory: Optional[str] = None
    insight_memory_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ThoughtChain:
    chain_id: str
    goal: str
    status: str = ChainStatus.OPEN.value
    agent_id: str = ""
    expected_outcome: Optional[str] = None
    actual_outcome: Optional[str] = None
    thoughts: list[Thought] = field(default_factory=list)
    reflection: Optional[Reflection] = None
    pending_insight: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None

    def by_type(self, ttype: ThoughtType | str) -> list[Thought]:
        t = ttype.value if isinstance(ttype, ThoughtType) else ttype
        return [x for x in self.thoughts if x.thought_type == t]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ThoughtChain":
        d = dict(d)
        d["thoughts"] = [Thought(**t) for t in d.get("thoughts") or []]
        ref = d.get("reflection")
        d["reflection"] = Reflection(**ref) if ref else None
        return cls(**d)

    def summary(self) -> str:
        lines = [f"目标: {self.goal}", f"状态: {self.status}"]
        for t in self.thoughts:
            mark = " ✓" if t.was_correct is True else " ✗" if t.was_correct is False else ""
            lines.append(f"  [{t.thought_type}]{mark} {t.content[:60]}")
        if self.reflection:
            lines.append(f"评分 {self.reflection.rating:.0%}  准确率 {self.reflection.accuracy:.0%}")
            lines.extend(f"  · {i}" for i in self.reflection.insights)
        return "\n".join(lines)


def _overlap(a: str, b: str) -> float:
    """a 的词有多少比例出现在 b 中（按 BM25 分词，中英文都可用）。"""
    ta = set(tokenize(a))
    if not ta:
        return 0.0
    return len(ta & set(tokenize(b))) / len(ta)


class Reflector:
    """事后反思：对比结果，标记节点对错，提炼结论。规则沿用 HMR。"""

    NEG = ("失败", "错误", "超时", "崩溃", "回滚", "fail", "error", "crash", "timeout")
    POS = ("成功", "完成", "正常", "通过", "解决", "降低", "success", "done", "ok", "pass", "fixed")

    def infer_rating(self, expected: Optional[str], actual: str) -> float:
        if expected:
            return min(1.0, _overlap(expected, actual) * 1.5)
        low = (actual or "").lower()
        neg = sum(w in low for w in self.NEG)
        pos = sum(w in low for w in self.POS)
        return 0.2 if neg > pos else 0.9 if pos > neg else 0.5

    def reflect(self, chain: ThoughtChain, actual: str,
                rating: Optional[float] = None) -> Reflection:
        if rating is None:
            rating = self.infer_rating(chain.expected_outcome, actual)
        rating = max(0.0, min(1.0, float(rating)))

        correct, wrong = [], []
        for t in chain.thoughts:
            if t.thought_type in _UNJUDGED:
                t.was_correct = None
                continue
            if rating >= 0.7:
                ok = True
            elif rating <= 0.3:
                ok = t.thought_type not in (ThoughtType.DECISION.value,
                                            ThoughtType.HYPOTHESIS.value)
            else:
                ok = rating > 0.5 if t.thought_type == ThoughtType.DECISION.value else True
            t.was_correct = ok
            (correct if ok else wrong).append(t.thought_id)

        mistakes = [f"[{t.thought_type}] {t.content[:50]}"
                    for t in chain.thoughts if t.thought_id in wrong]
        insights: list[str] = []
        decisions = chain.by_type(ThoughtType.DECISION)
        if rating >= 0.8 and decisions:
            insights.append(f"有效决策：{decisions[-1].content[:60]}（评分 {rating:.0%}）")
        elif rating <= 0.3:
            hyps = chain.by_type(ThoughtType.HYPOTHESIS)
            if hyps:
                insights.append(f"假设方向有误：{hyps[0].content[:40]} → 实际：{actual[:40]}")
            insights.append("下次应先验证假设，再做决策")
        insights.append(f"目标「{chain.goal[:30]}」{'达成' if rating > 0.6 else '未达成'}"
                        f"（评分 {rating:.0%}）")

        suggested = None
        if rating > 0.85 or rating < 0.25:      # 只有明确成功或失败才值得写入长期记忆
            dec = decisions[-1].content[:80] if decisions else ""
            suggested = (f"[推理结论] 目标：{chain.goal[:60]}｜"
                         f"结果：{'成功' if rating > 0.6 else '失败'}（{actual[:60]}）｜"
                         f"关键决策：{dec}｜{' / '.join(insights[:2])}")

        judged = len(correct) + len(wrong)
        return Reflection(
            rating=rating,
            accuracy=len(correct) / judged if judged else rating,
            correct_nodes=correct, wrong_nodes=wrong, key_mistakes=mistakes,
            insights=insights, suggested_memory=suggested,
        )


class ReasoningEngine:
    """推理链引擎。链保存在 <palace.base_dir>/reasoning/，结论写回主库。"""

    MIN_OVERLAP = 0.3     # 相似链的最低词重叠（与 HMR 原版一致）
    MIN_ACCURACY = 0.7    # best_decision_for 只采信准确率不低于该值的链

    def __init__(self, palace, directory: str | Path | None = None):
        from vecrecall.core.engine import VecRecall   # 避免循环导入

        self.palace = palace
        self.reflector = Reflector()
        base = Path(directory or Path(palace.base_dir) / "reasoning")
        base.mkdir(parents=True, exist_ok=True)
        # 主库用内存向量（测试 / 临时场景）时推理库也用内存；否则用 SQLite 持久化。
        # 向量索引丢了也没关系：链本身在 knowledge.db，reindex() 可从原文重建。
        if isinstance(palace._vec, NumpyVectorBackend):
            vec = NumpyVectorBackend()
        else:
            vec = SqliteVectorBackend(str(base / "vectors.db"))
        self.store = VecRecall(
            base_dir=str(base), wing=palace.wing, owner=palace.owner,
            embedding_backend=palace._emb, vector_backend=vec, dedup=False,
        )
        if getattr(palace, "_cipher", None) is not None:
            # 主库加密时，推理链的 metadata（含全部推理过程）同样加密落盘
            self.store._kg._cipher = palace._cipher

    # ── 读写 ──

    def _load(self, chain_id: str) -> Optional[ThoughtChain]:
        m = self.store._kg.get(chain_id)
        if not m or "chain" not in m.metadata:
            return None
        if self.store.owner and not self.store._visible_to(m, self.store.owner):
            return None
        return ThoughtChain.from_dict(m.metadata["chain"])

    def _save(self, chain: ThoughtChain) -> None:
        self.store.update(chain.chain_id, metadata={"chain": chain.to_dict(),
                                                    "status": chain.status})

    def get(self, chain_id: str) -> Optional[ThoughtChain]:
        return self._load(chain_id)

    # ── 推理过程 ──

    def start(self, goal: str, expected_outcome: Optional[str] = None,
              agent_id: str = "", visibility: str = "private") -> ThoughtChain:
        if not (goal or "").strip():
            raise ValueError("goal 不能为空")
        mem = self.store.add(content=goal, topic=REASONING_TOPIC, importance=0.5,
                             owner=self.store.owner, visibility=visibility,
                             metadata={"kind": "reasoning_chain"})
        chain = ThoughtChain(chain_id=mem.id, goal=goal, agent_id=agent_id,
                             expected_outcome=expected_outcome)
        self._save(chain)
        return chain

    def think(self, chain_id: str, content: str,
              thought_type: ThoughtType | str = ThoughtType.OBSERVATION,
              confidence: float = 0.7, memory_ids: list[str] | None = None,
              parent_id: Optional[str] = None) -> Optional[Thought]:
        """追加一个节点；链不存在或已反思时返回 None。memory_ids 可记录依据了哪些记忆。"""
        chain = self._load(chain_id)
        if chain is None or chain.status == ChainStatus.CLOSED.value:
            return None
        ttype = ThoughtType(thought_type).value
        if parent_id is None and chain.thoughts:
            parent_id = chain.thoughts[-1].thought_id
        t = Thought(thought_id=f"th_{uuid.uuid4().hex[:8]}", thought_type=ttype,
                    content=content, confidence=max(0.0, min(1.0, confidence)),
                    parent_id=parent_id, memory_ids=list(memory_ids or []))
        chain.thoughts.append(t)
        if ttype == ThoughtType.ACTION.value:
            chain.status = ChainStatus.ACTIVE.value
        self._save(chain)
        return t

    def reflect(self, chain_id: str, actual_outcome: str,
                rating: Optional[float] = None) -> Optional[Reflection]:
        """用实际结果反思；有学习价值的结论写入主库，失败时留在链上待补写。"""
        chain = self._load(chain_id)
        if chain is None or chain.status == ChainStatus.CLOSED.value:
            return None
        result = self.reflector.reflect(chain, actual_outcome, rating)
        chain.actual_outcome = actual_outcome
        chain.reflection = result
        chain.status = ChainStatus.CLOSED.value
        chain.closed_at = time.time()
        if result.suggested_memory:
            result.insight_memory_id = self._write_insight(chain, result)
            if result.insight_memory_id is None:
                chain.pending_insight = result.suggested_memory
        self._save(chain)
        return result

    def _write_insight(self, chain: ThoughtChain, result: Reflection) -> Optional[str]:
        try:
            mem = self.palace.add(
                content=result.suggested_memory, topic=INSIGHT_TOPIC,
                memory_type="semantic", importance=round(0.5 + 0.4 * result.accuracy, 3),
                owner=self.palace.owner, dedup=False,
                metadata={"source": "reasoning", "chain_id": chain.chain_id,
                          "accuracy": result.accuracy, "rating": result.rating},
            )
            return mem.id
        except Exception:  # noqa: BLE001 —— 主库写失败不能让反思本身失败
            return None

    def retry_pending_insights(self) -> int:
        """补写之前写入主库失败的结论，返回成功条数。"""
        done = 0
        for chain in self.chains(status=ChainStatus.CLOSED.value):
            if not chain.pending_insight or chain.reflection is None:
                continue
            mid = self._write_insight(chain, chain.reflection)
            if mid:
                chain.reflection.insight_memory_id = mid
                chain.pending_insight = None
                self._save(chain)
                done += 1
        return done

    # ── 查询 ──

    def chains(self, status: Optional[str] = None, agent_id: Optional[str] = None
               ) -> list[ThoughtChain]:
        out = []
        for m in self.store._kg.iter_all():
            if "chain" not in m.metadata:
                continue
            if self.store.owner and not self.store._visible_to(m, self.store.owner):
                continue
            c = ThoughtChain.from_dict(m.metadata["chain"])
            if status and c.status != status:
                continue
            if agent_id is not None and c.agent_id != agent_id:
                continue
            out.append(c)
        return sorted(out, key=lambda c: c.created_at)

    def find_similar(self, goal: str, top_k: int = 3, closed_only: bool = True,
                     min_overlap: float | None = None) -> list[ThoughtChain]:
        """找目标相似的历史链：rrf 取候选，再要求足够的词重叠，按相关度排序。"""
        threshold = self.MIN_OVERLAP if min_overlap is None else min_overlap
        out: list[ThoughtChain] = []
        for r in self.store.search(goal, n=max(top_k * 5, 10), mode="rrf"):
            if r.memory.topic != REASONING_TOPIC:
                continue
            if _overlap(goal, r.memory.content) < threshold:
                continue
            chain = self._load(r.memory.id)
            if chain is None or (closed_only and chain.status != ChainStatus.CLOSED.value):
                continue
            out.append(chain)
            if len(out) >= top_k:
                break
        return out

    def best_decision_for(self, goal: str) -> Optional[dict]:
        """相似历史链中，准确率最高（≥ MIN_ACCURACY）的那条里最后一个被判为正确的决策。"""
        best, best_acc = None, -1.0
        for chain in self.find_similar(goal, top_k=5):
            ref = chain.reflection
            if ref and ref.accuracy >= self.MIN_ACCURACY and ref.accuracy > best_acc:
                ok = [d for d in chain.by_type(ThoughtType.DECISION) if d.was_correct]
                if ok:
                    best, best_acc = (chain, ok[-1]), ref.accuracy
        if best is None:
            return None
        chain, decision = best
        return {"decision": decision.content, "accuracy": best_acc,
                "chain_id": chain.chain_id, "goal": chain.goal,
                "outcome": chain.actual_outcome}

    def stats(self) -> dict:
        all_chains = self.chains()
        closed = [c for c in all_chains if c.reflection]
        by_status: dict[str, int] = {}
        for c in all_chains:
            by_status[c.status] = by_status.get(c.status, 0) + 1
        return {
            "chains": len(all_chains),
            "by_status": by_status,
            "avg_accuracy": (sum(c.reflection.accuracy for c in closed) / len(closed)
                             if closed else None),
            "pending_insights": sum(1 for c in all_chains if c.pending_insight),
        }

    def close(self) -> None:
        self.store.close()
