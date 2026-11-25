"""
VecRecall — 记忆运行时核心引擎

关键设计原则：
  检索路径  → 纯向量语义，不经过任何结构过滤
  组织路径  → Wing/Topic/Diary 分层，只用于人工浏览和 UI 展示
  AAAK      → 只生成摘要供 UI 展示，不参与检索
  四层栈    → L0/L1/L2/L3 保留，L2 触发改为语义阈值

v2.2 起按职责拆分，本文件只保留 VecRecall 引擎本身：
  core/models.py      Memory / RetrievalResult / ContextBundle / 序列化
  core/vectors.py     向量后端（Numpy / SQLite / Chroma）
  core/embeddings.py  嵌入后端（BagOfWords / SentenceTransformer / Ollama / API）
  core/storage.py     KnowledgeGraph（SQLite 元数据与原文）
  core/hooks.py       扩展钩子（before_write / after_write / recall_filter / on_delete）

为向后兼容，上述模块的公开名字仍可从 vecrecall.core.engine 导入。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from vecrecall.core.extractor import (
    Extraction, MemoryExtractor, HeuristicExtractor,
    heuristic_importance, heuristic_summary,  # noqa: F401
)
from vecrecall.core.crypto import Cipher
from vecrecall.core.forgetting import ForgettingCurve
from vecrecall.core.temporal_graph import TemporalGraph
from vecrecall.core.hooks import HookRegistry, load_entry_point_plugins
from vecrecall.core.lexical import BM25Index, LexicalCache, rrf_merge
from vecrecall.core.contradiction import ContradictionDetector, Verdict
# 向后兼容的再导出
from vecrecall.core.models import (  # noqa: F401
    estimate_tokens, memory_to_dict, Memory, RetrievalResult, ContextBundle,
)
from vecrecall.core.vectors import (  # noqa: F401
    VectorBackend, NumpyVectorBackend, SqliteVectorBackend, ChromaVectorBackend,
)
from vecrecall.core.embeddings import (  # noqa: F401
    EmbeddingBackend, HashEmbeddingBackend, BagOfWordsEmbeddingBackend,
    SentenceTransformerBackend, OllamaEmbeddingBackend, APIEmbeddingBackend,
    DEFAULT_SENTENCE_MODEL, build_embedding_backend,
)
from vecrecall.core.storage import KnowledgeGraph  # noqa: F401


class VecRecall:
    """
    改进版记忆引擎。

    核心改动：
      1. 检索路径完全去除结构过滤（不按 wing/topic 过滤向量查询）
      2. L2 触发改为语义相似度阈值，而非 Room 名称匹配
      3. AAAK（ui_summary）只写入 SQLite UI 层，不进入向量检索
      4. 组织层（Wing/Topic）只影响 KnowledgeGraph，不影响向量查询
    """

    # L2 触发：当前对话与历史话题的语义相似度 ≥ 此阈值时，加载该话题上下文
    L2_TRIGGER_THRESHOLD = 0.55
    # L1 固定加载数量
    L1_TOP_K = 15
    # L2 按需加载数量
    L2_TOP_K = 8
    # L3 深度检索数量
    L3_TOP_K = 20
    # 混合评分：时间衰减半衰期（天）
    RECENCY_HALF_LIFE_DAYS = 30.0
    # 检索模式：vector（纯向量）/ bm25（纯词法）/ rrf（向量 + BM25 名次融合，v2.3 起默认）
    DEFAULT_SEARCH_MODE = "rrf"
    SEARCH_MODES = ("vector", "bm25", "rrf")
    # RRF 平滑常数与每路候选数倍率（每路取 n × 倍率 条再融合）
    RRF_K = 60
    RRF_CANDIDATES = 3
    # 矛盾检测（v2.4）：候选相似度下限（上限即去重阈值）、候选数、被取代记忆的检索降权系数
    CONTRADICTION_MIN_SIM = 0.2
    CONTRADICTION_CANDIDATES = 5
    SUPERSEDED_WEIGHT = 0.5
    # 来源追踪（v2.5）：写入入口标识；MCP / REST / CLI 启动时改成 mcp / rest / cli
    DEFAULT_VIA = "python"
    # 去重合并时最多保留多少条「重复来源」记录
    PROVENANCE_MAX_MERGES = 20

    def __init__(
        self,
        base_dir: str,
        wing: str = "default",
        vector_backend: VectorBackend | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        identity_prompt: str = "",
        use_blockchain: bool = False,
        extractor: MemoryExtractor | None = None,
        dedup: bool = True,
        dedup_threshold: float = 0.92,
        encryption_key: str | None = None,
        owner: str = "",
        redact_payloads: bool = False,
        hooks: HookRegistry | None = None,
        load_plugins: bool = False,
        detect_contradictions: bool = True,
        contradiction_judge=None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.wing = wing
        self.identity_prompt = identity_prompt
        # 双层隔离：本实例的写入者/查看者标识（agent id / user id）。
        # 空 = 单机模式，不做可见性过滤（保持旧行为）。
        self.owner = owner

        # 认知层：写入时自动抽取结构化信息（None=关闭，保持词法级零依赖）
        self._extractor = extractor
        # 语义去重：相似度 ≥ 阈值时合并到既有记忆（避免重复）
        self.dedup = dedup
        self.dedup_threshold = dedup_threshold
        # 矛盾检测：新记忆推翻旧记忆时，旧记忆标记 superseded_by 并在检索时降权（不删除）
        self.detect_contradictions = bool(detect_contradictions)
        self._contradiction = ContradictionDetector(judge=contradiction_judge)
        # 写入前脱敏（不可逆，默认关闭；immutable 审计记忆永不脱敏）
        self.redact_payloads = bool(redact_payloads)

        # 扩展钩子：外部扩展包在这里挂载（默认为空，不改变任何行为）
        self.hooks = hooks if hooks is not None else HookRegistry()
        if load_plugins:
            load_entry_point_plugins(self.hooks)

        # 存储后端：自动检测可用的最佳后端
        if embedding_backend is not None:
            self._emb = embedding_backend
        elif SentenceTransformerBackend is not None:
            self._emb = SentenceTransformerBackend()
        else:
            # 零依赖默认：词法语义嵌入（余弦相似度 ≈ 词面重合度）
            self._emb = BagOfWordsEmbeddingBackend()

        if vector_backend is not None:
            self._vec = vector_backend
        elif ChromaVectorBackend is not None:
            self._vec = ChromaVectorBackend(str(self.base_dir / "chroma"))
        else:
            # 零依赖默认：SQLite 持久化，重启不丢向量索引
            self._vec = SqliteVectorBackend(str(self.base_dir / "vectors.db"))

        # 静态加密：提供 encryption_key 时，记忆内容（content/摘要/元数据）加密落盘
        self._cipher = None
        if encryption_key:
            self._cipher = Cipher.resolve(encryption_key, self.base_dir)
        self._kg = KnowledgeGraph(
            str(self.base_dir / "knowledge.db"), cipher=self._cipher)

        # 时序知识图谱：写入记忆时自动记录实体/关系/状态演化
        self._graph = TemporalGraph(str(self.base_dir / "graph.db"))

        # 遗忘曲线：记忆强度随时间衰减（艾宾浩斯曲线），供检索加权与遗忘清理
        self._forgetting = ForgettingCurve()

        # 词法索引：首次 bm25 / rrf 检索时从 SQLite 原文构建，之后增量维护
        self._bm25: BM25Index | None = None
        # 词频磁盘缓存：新进程（如每次 vr 命令）不必重新分词；加密时不用，避免明文落盘
        self.lexical_cache = encryption_key is None
        self._lex_cache: LexicalCache | None = None
        self.last_lexical_build: dict = {}

        # 可选哈希链：写入记忆时同步生成防篡改区块
        self._chain = None
        if use_blockchain:
            from vecrecall.blockchain import BlockChain
            self._chain = BlockChain(str(self.base_dir / "chain.db"))

    # ── 写入 ──────────────────────────────────

    def add(self, content: str, topic: str = "general",
            wing: str | None = None, importance: float | None = None,
            session_id: str = "", ui_summary: str = "",
            metadata: dict | None = None, dedup: bool | None = None,
            memory_type: str = "episodic", visibility: str = "private",
            owner: str = "", retention_policy: str = "decay",
            scenario_tags: list[str] | None = None,
            policy_version: str | None = None,
            topic_key: str = "",
            provenance: dict | None = None) -> Memory:
        """
        存入一条记忆。

        - content 原文逐字存入 KnowledgeGraph
        - 嵌入向量不携带 wing/topic 过滤信息，保持纯语义
        - ui_summary（AAAK）只写入 KG，不进入向量
        - 配置了 extractor 时，自动抽取 topic/importance/摘要/实体/关系
        - 语义去重：相似度 ≥ dedup_threshold 时合并到既有记忆（occurrences+1）
        - memory_type: episodic / semantic / procedural
          · semantic 强制走时序知识图谱（无 extractor 时启发式兜底）
          · procedural 走「场景标签 + 版本」精确匹配（query_procedural）
        - visibility + owner: 双层隔离（private 仅 owner 可见，shared 同 wing 可见）
        - retention_policy: decay（默认衰减）/ immutable（豁免去重与遗忘）
        - scenario_tags / policy_version: procedural 专用（写入 metadata）
        """
        w = wing or self.wing

        redaction_counts: dict[str, int] = {}
        if self.redact_payloads and retention_policy != "immutable":
            from vecrecall.core.redaction import (
                has_meaningful_payload,
                redact_memory_payload,
            )
            redacted = redact_memory_payload(content)
            if not has_meaningful_payload(redacted.text):
                raise ValueError("content is empty after redaction")
            content = redacted.text
            redaction_counts = dict(redacted.counts)
            if ui_summary:
                ui_summary = redact_memory_payload(ui_summary).text

        # 扩展钩子：写入前（可改写字段或抛异常拒绝写入）
        if self.hooks.has("before_write"):
            fields = self.hooks.run_before_write({
                "content": content, "topic": topic, "wing": w,
                "importance": importance, "session_id": session_id,
                "ui_summary": ui_summary, "metadata": dict(metadata or {}),
                "memory_type": memory_type, "visibility": visibility,
                "owner": owner, "retention_policy": retention_policy,
                "topic_key": topic_key,
            })
            content = fields["content"]; topic = fields["topic"]; w = fields["wing"]
            importance = fields["importance"]; session_id = fields["session_id"]
            ui_summary = fields["ui_summary"]; metadata = fields["metadata"]
            memory_type = fields["memory_type"]; visibility = fields["visibility"]
            owner = fields["owner"]; retention_policy = fields["retention_policy"]
            topic_key = fields["topic_key"]

        # 认知层抽取（仅在未显式提供时填充）
        ext: Extraction | None = None
        if self._extractor is not None:
            ext = self._extractor.extract(content)
        elif memory_type == "semantic":
            # semantic 记忆强制走时序图谱：即使未配置 extractor，
            # 也用启发式抽取兜底，保证实体/关系一定入图。
            ext = HeuristicExtractor().extract(content)

        if ext is not None:
            if topic == "general" and ext.topic != "general":
                topic = ext.topic
            if importance is None:
                importance = ext.importance
            if not ui_summary:
                ui_summary = ext.ui_summary

        if importance is None:
            importance = self._heuristic_importance(content)

        meta = dict(metadata or {})
        prov = self._build_provenance(provenance, meta, session_id, owner)
        if redaction_counts:
            meta["redaction_counts"] = redaction_counts
        if self.redact_payloads and retention_policy == "immutable":
            meta["redaction_skipped"] = "immutable"
        if ext is not None:
            if ext.entities:
                meta.setdefault("entities", ext.entities)
            if ext.relations:
                meta.setdefault("relations", ext.relations)
        # procedural 记忆：场景标签 + 策略版本写入 metadata，供精确匹配
        if memory_type == "procedural":
            if scenario_tags:
                meta["scenario_tags"] = list(scenario_tags)
            if policy_version:
                meta["policy_version"] = policy_version

        # 语义去重合并（相似度过高时不再新增）
        # 豁免：retention_policy=immutable（审计记忆）语义再相似也各存各的，
        # 两笔金额差一分的审批决策是不同的审计事实，绝不能合并 occurrences。
        do_dedup = self.dedup if dedup is None else dedup
        # 钩子可通过 metadata["_no_dedup"] 要求本条不参与去重（标记本身不落盘）
        if meta.pop("_no_dedup", False):
            do_dedup = False
        if do_dedup and retention_policy != "immutable":
            existing = self._dedup_candidate(content)
            if existing is not None:
                existing.importance = max(existing.importance, importance)
                existing.metadata["occurrences"] = \
                    existing.metadata.get("occurrences", 1) + 1
                self._record_merge(existing, prov)
                self._kg.save(existing)
                self._ingest_graph(ext, existing.id, existing.timestamp)
                self.hooks.run_after_write(existing, True)
                return existing

        # 矛盾检测：在写入新记忆之前找候选，避免候选里出现自己
        conflicts: list[tuple[Memory, Verdict]] = []
        if (self.detect_contradictions and retention_policy != "immutable"
                and not meta.pop("_no_contradiction", False)):
            conflicts = self._find_contradictions(content, w, owner or self.owner)
        else:
            meta.pop("_no_contradiction", None)

        meta["provenance"] = prov
        mem = Memory.create(
            wing=w, topic=topic, content=content,
            session_id=session_id, importance=importance,
            ui_summary=ui_summary, metadata=meta,
            memory_type=memory_type, visibility=visibility,
            owner=owner, retention_policy=retention_policy,
            topic_key=topic_key,
        )

        # 1. 向量索引：只索引原文，不含结构信息（这是关键改动）
        vec = self._emb.embed(content)
        self._vec.upsert(mem.id, vec, {"id": mem.id})

        # 2. 元数据 + 原文持久化到 SQLite
        self._kg.save(mem)
        self._bm25_add(mem.id, content)

        # 3. 时序知识图谱：实体/关系/状态演化
        self._ingest_graph(ext, mem.id, mem.timestamp)

        # 3b. 矛盾：旧记忆标记被取代（不删除），新记忆记录它取代了谁
        if conflicts:
            self._apply_supersede(mem, conflicts)

        # 4. 可选：哈希链区块（防篡改审计；topic_key 非空时入独立子链）
        if self._chain is not None:
            from vecrecall.blockchain import extract_keywords
            self._chain.new_block(
                content=content,
                wing=w,
                session_id=mem.session_id,
                trigger="memory",
                keywords=extract_keywords(content),
                topic_key=topic_key,
            )

        self.hooks.run_after_write(mem, False)
        return mem

    def add_batch(self, items: list[dict]) -> list[Memory]:
        """批量写入，更高效"""
        memories = []
        for item in items:
            m = self.add(**item)
            memories.append(m)
        return memories

    # 会话存档粒度：turn = 每轮对话一条记忆（默认），session = 整段会话一条记忆
    ARCHIVE_GRANULARITIES = ("turn", "session")

    def archive_session(self, messages: list[dict], session_id: str = "",
                        topic: str = "session", wing: str | None = None,
                        granularity: str = "turn",
                        importance: float | None = None,
                        **kwargs) -> list[Memory]:
        """
        存档一段对话，返回写入的记忆列表。

        granularity="turn"（默认，v2.3 起）：每轮对话一条记忆，共用同一个 session_id，
          metadata 记录 turn_index 与 role，可用 session_memories() 按顺序还原整段会话。
          LongMemEval-S 上，相比整段存一条：会话级 R_any@5 75.3% → 92.1%，
          top-5 上下文约 13,900 → 900 token（词法嵌入，470 题）。
        granularity="session"：整段会话合成一条记忆（v2.2 及以前的行为）。

        - 空内容的轮次会跳过。
        - turn 模式下关闭语义去重：同一句「好的」出现在不同会话里是不同的事实，
          合并会丢失会话归属。
        - 其余参数（visibility / owner / memory_type 等）原样传给 add()。
        """
        if granularity not in self.ARCHIVE_GRANULARITIES:
            raise ValueError(
                f"非法 granularity: {granularity}（仅 turn / session）")
        sid = session_id or str(uuid.uuid4())
        turns = [(i, str(m.get("role", "")), str(m.get("content", "")))
                 for i, m in enumerate(messages or [])]
        turns = [t for t in turns if t[2].strip()]
        if not turns:
            return []

        if granularity == "session":
            combined = "\n".join(f"[{role}] {content}" for _i, role, content in turns)
            return [self.add(content=combined, topic=topic, wing=wing,
                             session_id=sid, importance=importance, **kwargs)]

        kwargs.setdefault("dedup", False)
        base_meta = dict(kwargs.pop("metadata", None) or {})
        out: list[Memory] = []
        for i, role, content in turns:
            meta = dict(base_meta)
            meta.update({"turn_index": i, "role": role})
            out.append(self.add(content=f"{role}: {content}", topic=topic, wing=wing,
                                session_id=sid, importance=importance,
                                metadata=meta, **kwargs))
        return out

    def session_memories(self, session_id: str) -> list[Memory]:
        """按 session_id 取回一段会话的全部记忆（按 turn_index、时间排序，做可见性过滤）。"""
        mems = self._kg.list_by_session(session_id)
        if self.owner:
            mems = [m for m in mems if self._visible_to(m, self.owner)]
        mems = self._filter_memories(mems)
        return sorted(mems, key=lambda m: (m.metadata.get("turn_index", 0), m.timestamp))

    # ── 四层记忆栈 ───────────────────────────

    def build_context(
        self,
        current_query: str = "",
        load_l2: bool = True,
        load_l3: bool = False,
        hybrid: bool = False,
        memory_types: list[str] | None = None,
        viewer: str | None = None,
        as_of: float | None = None,
        mode: str | None = None,
        include_superseded: bool = False,
    ) -> ContextBundle:
        """
        构建完整的四层上下文 bundle。

        L0: 固定身份层
        L1: 全库按 importance 排序 top-15（不按 wing 过滤）；
            immutable（审计）记忆无条件并入，不受 top-K 挤占
        L2: 当前查询语义触发，阈值 ≥ 0.55
        L3: 仅显式请求时触发，全量语义检索
        hybrid: True 时 L2/L3 分数 = 0.6·相似度 + 0.25·重要性 + 0.15·时间新鲜度
        memory_types: 声明本次加载的记忆语义类型（episodic/semantic/procedural），
            与 L0-L3 加载策略维度正交；None 表示不过滤（全部类型）。
        """

        # L0
        l0 = self.identity_prompt or f"[Wing: {self.wing}] AI 助手，服务于当前项目。"

        # L1：按 importance 取 top-K，不做向量过滤（保留原版设计）
        top_mems = self._kg.top_by_importance(wing=None, n=self.L1_TOP_K)
        # 召回豁免：immutable（审计）记忆无条件并入 L1，
        # 不受 top-K 挤占和强度/重要性阈值限制。
        seen_ids = {m.id for m in top_mems}
        immutable_extra = [m for m in self._kg.list_by_retention("immutable")
                           if m.id not in seen_ids]
        if immutable_extra:
            top_mems = sorted(top_mems + immutable_extra,
                              key=lambda m: m.importance, reverse=True)
        l1_results = [
            RetrievalResult(memory=m, score=m.importance, layer="L1")
            for m in top_mems
        ]

        # L2：语义触发（核心改动：阈值判断，不是 Room 名称匹配）
        l2_results: list[RetrievalResult] = []
        if load_l2 and current_query.strip():
            l2_results = self._semantic_l2(current_query, hybrid=hybrid, as_of=as_of)

        # L3：全量语义检索
        l3_results: list[RetrievalResult] = []
        if load_l3 and current_query.strip():
            l3_results = self._deep_search(current_query, self.L3_TOP_K,
                                           hybrid=hybrid, as_of=as_of,
                                           mode=mode or self.DEFAULT_SEARCH_MODE)

        # 被取代的旧记忆降权（L1 按重要性、L2/L3 按检索分数）
        l1_results = self._apply_superseded_weight(l1_results, include_superseded)
        l2_results = self._apply_superseded_weight(l2_results, include_superseded)
        l3_results = self._apply_superseded_weight(l3_results, include_superseded)

        # 类型过滤：一处实现，L1/L2/L3 三层统一生效
        if memory_types is not None:
            allowed = set(memory_types)
            l1_results = [r for r in l1_results if r.memory.memory_type in allowed]
            l2_results = [r for r in l2_results if r.memory.memory_type in allowed]
            l3_results = [r for r in l3_results if r.memory.memory_type in allowed]

        # 可见性过滤（双层隔离）：private 仅 owner 可见，shared 同 wing 全员可见
        viewer = viewer if viewer is not None else self.owner
        if viewer:
            l1_results = [r for r in l1_results
                          if self._visible_to(r.memory, viewer)]
            l2_results = [r for r in l2_results
                          if self._visible_to(r.memory, viewer)]
            l3_results = [r for r in l3_results
                          if self._visible_to(r.memory, viewer)]

        # 扩展钩子：召回过滤（授权/租户隔离等，只能删减）
        if self.hooks.has("recall_filter"):
            l1_results = self._filter_results(l1_results, viewer)
            l2_results = self._filter_results(l2_results, viewer)
            l3_results = self._filter_results(l3_results, viewer)

        # token 估算（中英混合修正：中文 ≈ 0.7 token/字）
        def count(results):
            return sum(estimate_tokens(r.memory.content) for r in results)

        total_tokens = estimate_tokens(l0) + count(l1_results) + count(l2_results) + count(l3_results)

        return ContextBundle(
            l0_identity=l0,
            l1_key_moments=l1_results,
            l2_topic_context=l2_results,
            l3_deep_results=l3_results,
            total_tokens_estimate=total_tokens,
        )

    def _semantic_l2(self, query: str, hybrid: bool = False,
                     as_of: float | None = None) -> list[RetrievalResult]:
        """
        L2 语义触发：查询向量与历史记忆的余弦相似度 ≥ 阈值才返回。
        关键：不使用任何结构过滤条件（不按 wing/topic/room 限制）。
        """
        vec = self._emb.embed(query)
        raw = self._vec.query(vec, n=self.L2_TOP_K * 2, min_score=self.L2_TRIGGER_THRESHOLD)

        ids = [r[0] for r in raw]
        scores = {r[0]: r[1] for r in raw}
        mem_map = self._kg.get_many(ids)

        results = []
        for mid, score in scores.items():
            if mid in mem_map:
                s = self._hybrid_score(score, mem_map[mid], as_of) if hybrid else score
                results.append(RetrievalResult(
                    memory=mem_map[mid], score=s, layer="L2"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.L2_TOP_K]

    def _deep_search(self, query: str, n: int, min_score: float | None = None,
                     hybrid: bool = False,
                     as_of: float | None = None,
                     mode: str = "vector") -> list[RetrievalResult]:
        """
        L3 全量语义检索：直接命中向量库，无任何过滤条件，
        召回率（R@5）由嵌入质量决定，可用 benchmarks/benchmark_recall.py 复现评测。
        hybrid=True 时对结果做 相似度+重要性+时间 融合重排（不改变召回集合）。

        min_score=None（默认）不按分数过滤，始终返回 top-n。
        余弦相似度取值 [-1, 1]：词法哈希嵌入在两段文本没有共同词时，相似度会在 0
        附近正负波动，若默认按 0 截断，排在后面的真正答案会被直接丢掉。
        """
        if mode not in self.SEARCH_MODES:
            raise ValueError(f"非法 mode: {mode}（可选 {', '.join(self.SEARCH_MODES)}）")
        if mode != "vector":
            return self._lexical_search(query, n, mode=mode, min_score=min_score,
                                        hybrid=hybrid, as_of=as_of)
        vec = self._emb.embed(query)
        threshold = float("-inf") if min_score is None else min_score
        raw = self._vec.query(vec, n=n, min_score=threshold)

        ids = [r[0] for r in raw]
        scores = {r[0]: r[1] for r in raw}
        mem_map = self._kg.get_many(ids)

        results = []
        for mid, score in scores.items():
            if mid in mem_map:
                m = mem_map[mid]
                if self.owner and not self._visible_to(m, self.owner):
                    continue  # 可见性过滤：private 记忆不泄露给非 owner
                s = self._hybrid_score(score, m, as_of) if hybrid else score
                results.append(RetrievalResult(
                    memory=m, score=s, layer="L3"
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ── 词法检索 / RRF ────────────────────────

    def _bm25_index(self) -> BM25Index:
        """取词法索引；首次使用时从 SQLite 原文构建。"""
        if self._bm25 is None:
            import time as _t
            t0 = _t.perf_counter()
            docs = self._kg.iter_id_content()
            if self.lexical_cache and self._cipher is None:
                if self._lex_cache is None:
                    self._lex_cache = LexicalCache(str(self.base_dir / "lexical.db"))
                idx, stats = self._lex_cache.build(docs)
            else:
                idx = BM25Index()
                n = 0
                for mid, content in docs:
                    idx.add(mid, content)
                    n += 1
                stats = {"hit": 0, "miss": n, "stale_removed": 0}
            stats["ms"] = round((_t.perf_counter() - t0) * 1000, 1)
            self.last_lexical_build = stats
            self._bm25 = idx
        return self._bm25

    def _bm25_add(self, memory_id: str, content: str) -> None:
        """写入路径同步：索引已构建时增量更新，未构建时什么也不做。"""
        if self._bm25 is not None:
            self._bm25.add(memory_id, content)

    def _lexical_search(self, query: str, n: int, mode: str,
                        min_score: float | None = None, hybrid: bool = False,
                        as_of: float | None = None) -> list[RetrievalResult]:
        """bm25：纯词法；rrf：向量与 BM25 各取 n×RRF_CANDIDATES 条，按名次融合。

        hybrid=True 时把融合后的名次当作「相似度」再做重要性 + 新鲜度重排，
        名次分数先归一到 [0, 1]，避免 RRF 分数（约 0.01–0.03）被其他两项压过。
        """
        pool = max(n * self.RRF_CANDIDATES, n)
        lex = self._bm25_index().search(query, pool)
        if mode == "bm25":
            fused = lex[:n]
        else:
            vec_ids = [r.memory.id for r in
                       self._deep_search(query, pool, min_score=min_score)]
            fused = rrf_merge([vec_ids, [d for d, _ in lex]], k=self.RRF_K)[:n]

        mem_map = self._kg.get_many([d for d, _ in fused])
        results: list[RetrievalResult] = []
        top = fused[0][1] if fused else 1.0
        for rank, (mid, sc) in enumerate(fused):
            m = mem_map.get(mid)
            if m is None:
                continue
            if self.owner and not self._visible_to(m, self.owner):
                continue
            if hybrid:
                sc = self._hybrid_score(sc / top if top else 0.0, m, as_of)
            results.append(RetrievalResult(memory=m, score=sc, layer="L3"))
        if hybrid:
            results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _hybrid_score(self, sim: float, mem: Memory,
                      as_of: float | None = None) -> float:
        """融合评分：0.6·相似度 + 0.25·重要性 + 0.15·时间新鲜度（指数衰减）

        as_of：计算新鲜度的参考时刻（epoch 秒），默认当前时间。
        记忆库整体较旧、或回放历史对话时，应传入提问时刻；
        否则所有记忆的新鲜度都接近 0，时间因素失效，hybrid 退化为纯向量排序。
        """
        ref = time.time() if as_of is None else as_of
        age_days = max(0.0, ref - mem.timestamp) / 86400.0
        recency = 0.5 ** (age_days / self.RECENCY_HALF_LIFE_DAYS)
        return 0.6 * sim + 0.25 * mem.importance + 0.15 * recency

    def _filter_results(self, results: list[RetrievalResult],
                        viewer: str | None = None) -> list[RetrievalResult]:
        """对检索结果应用 recall_filter 钩子，保持原有顺序。"""
        if not self.hooks.has("recall_filter") or not results:
            return results
        kept = {m.id for m in self.hooks.run_recall_filter(
            [r.memory for r in results], viewer or self.owner)}
        return [r for r in results if r.memory.id in kept]

    def _filter_memories(self, memories: list[Memory]) -> list[Memory]:
        if not self.hooks.has("recall_filter") or not memories:
            return memories
        return self.hooks.run_recall_filter(memories, self.owner)

    def _visible_to(self, m: Memory, viewer: str | None) -> bool:
        """
        双层隔离判定：
        - shared：同 wing 全员可见
        - private：仅 owner 匹配可见
        - owner 为空的记忆（老数据/未声明）：视为公开，保持旧行为
        - viewer 为空（实例未配置 owner 的单机模式）：不过滤
        """
        if m.visibility == "shared" or not m.owner:
            return True
        if not viewer:
            return True
        return m.owner == viewer

    # ── 搜索 ──────────────────────────────────

    def search(self, query: str, n: int = 10, min_score: float | None = None,
               hybrid: bool = False,
               acceptance_query: str | None = None,
               as_of: float | None = None,
               mode: str | None = None,
               include_superseded: bool = False) -> list[RetrievalResult]:
        """直接语义搜索，不经过任何结构过滤；hybrid=True 启用融合重排。

        被新记忆取代的旧记忆（metadata.superseded_by）分数乘以 SUPERSEDED_WEIGHT 后重排；
        include_superseded=True 时不降权。

        min_score=None（默认）始终返回 top-n；显式给定时才丢弃低于阈值的结果。
        as_of：hybrid 计算时间新鲜度的参考时刻（epoch 秒），默认当前时间。
        mode：rrf（默认，向量与 BM25 按名次融合）/ vector（纯向量）/ bm25（纯词法）。
            None 时使用 DEFAULT_SEARCH_MODE。
            bm25 / rrf 下 score 分别是 BM25 分数和 RRF 分数，与余弦相似度不可比；
            min_score 只作用于向量路。

        acceptance_query 给定时，对每条结果附加三项验收（同边界/同行为/
        可执行），不改变召回和排序，调用方自行决定是否采信。
        """
        results = self._deep_search(query, n, min_score=min_score, hybrid=hybrid,
                                    as_of=as_of, mode=mode or self.DEFAULT_SEARCH_MODE)
        results = self._apply_superseded_weight(results, include_superseded)
        results = self._filter_results(results)
        if acceptance_query is not None:
            from vecrecall.core.recall_policy import evaluate_memory
            for result in results:
                verdict = evaluate_memory(result.memory.content, acceptance_query)
                result.acceptance = verdict.as_dict()
        return results

    def query_by_topic_key(self, prefix: str,
                           wing: str | None = None) -> list[Memory]:
        """
        按分链键前缀查询记忆（通用原语）。

        例：query_by_topic_key("contract:C-001") 返回该主题关联的全部记忆，
        配合 verify_integrity(topic_key=...) 即可独立导出和验证一条审计链。
        """
        out: list[Memory] = []
        for m in self._kg.list_by_topic_key_prefix(prefix, wing=wing):
            if self.owner and not self._visible_to(m, self.owner):
                continue
            out.append(m)
        return self._filter_memories(out)

    def query_procedural(self, scenario_tags: list[str] | None = None,
                         version: str | None = None) -> list[Memory]:
        """
        procedural 记忆精确匹配：按「场景标签 + 策略版本」检索，不走语义近似。

        - strategy/规则/阈值类记忆是可版本化的策略表，检索时必须精确命中，
          向量相似度召回对它们是错误工具（差一分的阈值不是同一个策略）。
        - scenario_tags: 请求的标签须全部包含在记忆的标签里（子集匹配）；
          None 表示不按标签过滤。
        - version: 精确匹配 policy_version；None 表示任意版本。
        """
        out: list[Memory] = []
        for m in self._kg.list_by_type("procedural"):
            if self.owner and not self._visible_to(m, self.owner):
                continue  # 可见性过滤
            if scenario_tags:
                mem_tags = set(m.metadata.get("scenario_tags") or [])
                if not set(scenario_tags) <= mem_tags:
                    continue
            if version is not None and \
                    m.metadata.get("policy_version") != version:
                continue
            out.append(m)
        return self._filter_memories(out)

    # ── 遗忘曲线 ──────────────────────────────

    def memory_strength(self, memory_id: str) -> float | None:
        """返回某条记忆的当前强度（0-1，新记忆≈1），不存在返回 None。"""
        m = self._kg.get(memory_id)
        if not m:
            return None
        return self._forgetting.strength(m)

    def forgotten(self, threshold: float = 0.2,
                  wing: str | None = None) -> list[Memory]:
        """列出强度已跌破阈值的「已遗忘」记忆（按强度升序，最遗忘的在前）。"""
        now = time.time()
        out = [m for m in self._kg.iter_all(wing=wing)
               if m.retention_policy != "immutable"
               and self._forgetting.is_forgotten(m, threshold, now)]
        out.sort(key=lambda m: self._forgetting.strength(m, now))
        return out

    def recall(self, memory_id: str) -> Memory:
        """复习一条记忆：增强其记忆强度（间隔效应减缓遗忘）。"""
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")
        self._forgetting.review(m)
        self._kg.save(m)
        return m

    # ── 主动蒸馏 ──────────────────────────────

    def distill(self, wing: str | None = None, topic: str | None = None,
                min_cluster: int = 2, dry_run: bool = True) -> list[dict]:
        """主动蒸馏：把同一话题下碎片化的多条记忆压缩成一条抽象长期记忆。

        只蒸馏「碎片化」的组：条数达标（≥ min_cluster）且组内无高价值记忆
        （importance < 0.8），避免误伤关键记忆的原始细节。

        dry_run=True 只返回蒸馏计划（默认），dry_run=False 真正写入。
        每条计划含 topic / sources / count / summary / importance。
        """
        groups: dict[str, list[Memory]] = {}
        for m in self._kg.iter_all(wing=wing):
            # 豁免：immutable（审计记忆）不参与蒸馏——原始细节是不可替代的证据
            if m.retention_policy == "immutable":
                continue
            if m.metadata.get("distilled"):
                continue
            if topic and m.topic != topic:
                continue
            groups.setdefault(m.topic, []).append(m)

        plans: list[dict] = []
        for grp_topic, mems in groups.items():
            if len(mems) < min_cluster:
                continue
            if any(m.importance >= 0.8 for m in mems):
                continue  # 含高价值记忆，保留原始细节不蒸馏
            contents = [m.content for m in mems]
            summary = (self._extractor.summarize(contents)
                       if self._extractor is not None
                       else "；".join(heuristic_summary(c, 60)
                                      for c in contents)[:500])
            plans.append({
                "topic": grp_topic,
                "sources": [m.id for m in mems],
                "count": len(mems),
                "summary": summary,
                "importance": max(m.importance for m in mems),
            })

        if not dry_run:
            for plan in plans:
                self._write_distilled(plan)
        return plans

    def _write_distilled(self, plan: dict) -> Memory:
        """把蒸馏计划落成一条 Memory，并把源记忆标记为已蒸馏。"""
        sources = plan["sources"]
        mem = Memory.create(
            wing=self.wing, topic=plan["topic"],
            content=plan["summary"], importance=plan["importance"],
            ui_summary=plan["summary"],
            metadata={"distilled": True, "sources": sources,
                      "distilled_at": time.time(),
                      "provenance": self._build_provenance(
                          {"via": "distill", "derived_from": list(sources)}, {}, "", "")},
        )
        self._vec.upsert(mem.id, self._emb.embed(mem.content), {"id": mem.id})
        self._kg.save(mem)
        self._bm25_add(mem.id, mem.content)
        if self._extractor is not None:
            self._ingest_graph(self._extractor.extract(mem.content),
                               mem.id, mem.timestamp)
        for sid in sources:
            src = self._kg.get(sid)
            if src:
                src.metadata["distilled_into"] = mem.id
                self._kg.save(src)
        return mem

    # ── 多跳向量+图谱混合检索 ────────────────

    def graph_search(self, query: str, n: int = 10, hops: int = 2,
                     min_score: float = 0.1) -> dict:
        """多跳混合检索：向量召回种子 → 图谱扩展关联实体 → 汇总证据。

        返回 {"query": ..., "direct": [RetrievalResult...], "graph": [...]}。
        - direct：向量直接命中的记忆
        - graph：经图谱多跳扩展发现的关联记忆，每项含 memory / entities / paths
          （paths 记录「种子实体 → 关联实体」的关系路径，作为可解释证据）
        """
        seeds = self._filter_results(
            self._deep_search(query, n=max(n, 5), min_score=min_score))

        seed_entities: list[str] = []
        for r in seeds:
            for e in r.memory.metadata.get("entities", []) or []:
                if e and e not in seed_entities:
                    seed_entities.append(e)

        seed_ids = {r.memory.id for r in seeds}
        discovered: dict[str, dict] = {}
        for ent in seed_entities:
            for nb in self._graph.neighbors(ent, depth=hops):
                for mid in self._graph.entity_memories(nb["entity"]):
                    if mid in seed_ids:
                        continue
                    m = self._kg.get(mid)
                    if not m:
                        continue
                    hit = discovered.setdefault(
                        mid, {"memory": m, "entities": [], "paths": []})
                    if nb["entity"] not in hit["entities"]:
                        hit["entities"].append(nb["entity"])
                    hit["paths"].append(
                        {"from": ent, "to": nb["entity"], "path": nb["path"]})

        if self.hooks.has("recall_filter") and discovered:
            kept = {m.id for m in self._filter_memories(
                [d["memory"] for d in discovered.values()])}
            discovered = {k: v for k, v in discovered.items() if k in kept}
        graph_hits = sorted(discovered.values(),
                            key=lambda d: d["memory"].importance, reverse=True)
        return {"query": query, "direct": seeds, "graph": graph_hits}

    # ── 组织层（只供 UI，不影响检索）─────────

    def list_wings(self) -> list[str]:
        return self._kg.list_wings()

    def list_topics(self, wing: str | None = None) -> list[str]:
        return self._kg.list_topics(wing)

    def list_memories(self, wing: str | None = None, topic: str | None = None,
                      limit: int = 50, offset: int = 0,
                      full: bool = False, sort: str = "importance") -> dict:
        """
        向接口方回吐记忆列表（结构化清单，供浏览/挑选）。

        - wing/topic：可选过滤（只影响列表，不影响检索）
        - limit/offset：分页
        - full=True 时包含原文与 metadata
        - sort: "importance"（默认）或 "recent"

        返回 {"memories": [...], "total": N, "limit": L, "offset": O}
        """
        mems, total = self._kg.query_memories(wing, topic, limit, offset, sort)
        return {
            "memories": [memory_to_dict(m, full=full) for m in mems],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def link(self, source_id: str, target_id: str,
             link_type: str = "related", weight: float = 1.0) -> None:
        """手动建立跨 wing 关联（可选）。跨 wing 仅对 shared 记忆开放。"""
        src, dst = self._kg.get(source_id), self._kg.get(target_id)
        if src is None or dst is None:
            raise ValueError("link 的源/目标记忆不存在")
        if src.wing != dst.wing and \
                (src.visibility != "shared" or dst.visibility != "shared"):
            raise ValueError(
                "跨 wing 关联仅对 visibility=shared 的记忆开放")
        self._kg.add_link(source_id, target_id, link_type, weight)

    def update_visibility(self, memory_id: str, visibility: str) -> Memory:
        """原子操作：变更记忆可见性（private / shared）。"""
        if visibility not in ("private", "shared"):
            raise ValueError(f"非法 visibility: {visibility}（仅 private/shared）")
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")
        m.visibility = visibility
        self._kg.save(m)
        return m

    def update_retention_policy(self, memory_id: str, policy: str) -> Memory:
        """
        原子操作：变更留存策略（decay / immutable）。

        供外部编排（如契约状态机在状态迁移时调用）：
        记忆从活跃转为不可变时调用 update_retention_policy(id, "immutable")。
        """
        if policy not in ("decay", "immutable"):
            raise ValueError(f"非法 retention_policy: {policy}（仅 decay/immutable）")
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")
        m.retention_policy = policy
        self._kg.save(m)
        return m

    # ── 生命周期管理 ──────────────────────────

    def update(self, memory_id: str, content: str | None = None,
               topic: str | None = None, importance: float | None = None,
               ui_summary: str | None = None,
               metadata: dict | None = None) -> Memory:
        """
        更新一条记忆。content 变化时自动重新向量化，
        向量索引与 SQLite 始终保持一致。
        """
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")

        if content is not None and content != m.content:
            m.content = content
            vec = self._emb.embed(content)
            self._vec.upsert(m.id, vec, {"id": m.id})
            self._bm25_add(m.id, content)
        if topic is not None:
            m.topic = topic
        if importance is not None:
            m.importance = max(0.0, min(1.0, importance))
        if ui_summary is not None:
            m.ui_summary = ui_summary
        if metadata:
            m.metadata.update(metadata)

        self._kg.save(m)
        return m

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆（向量索引 + SQLite 同步移除）"""
        if not self._kg.get(memory_id):
            return False
        self._vec.delete(memory_id)
        self._kg.delete(memory_id)
        if self._bm25 is not None:
            self._bm25.remove(memory_id)
        self.hooks.run_on_delete(memory_id)
        return True

    def prune(self, min_importance: float | None = None,
              older_than_days: float | None = None,
              wing: str | None = None,
              dry_run: bool = True,
              use_forgetting: bool = False,
              strength_threshold: float = 0.2) -> list[Memory]:
        """
        清理低价值记忆。

        use_forgetting=False（默认）：按 importance 阈值 / 年龄阈值清理。
        use_forgetting=True：按遗忘曲线强度 < strength_threshold 清理 ——
        重要记忆衰减慢、复习过的记忆也会被保留，天然区分「该忘」与「该留」。

        dry_run=True 只返回候选清单不实际删除（默认），
        dry_run=False 真正删除（向量索引同步清理）。
        """
        now = time.time()
        victims: list[Memory] = []
        for m in self._kg.iter_all(wing=wing):
            # 豁免：immutable（审计记忆）永不被 prune——审批决策、证据链
            # 不能因为「很久没复习」或「importance 低」被清理掉。
            if m.retention_policy == "immutable":
                continue
            if use_forgetting:
                if self._forgetting.is_forgotten(m, strength_threshold, now):
                    victims.append(m)
                continue
            if min_importance is not None and m.importance < min_importance:
                victims.append(m)
                continue
            if older_than_days is not None and \
                    (now - m.timestamp) > older_than_days * 86400:
                victims.append(m)

        if not dry_run:
            for m in victims:
                self.delete(m.id)
        return victims

    def reindex(self) -> int:
        """
        从 SQLite 原文重建全部向量索引。

        适用场景：更换/升级嵌入后端后迁移旧数据、
        向量库损坏恢复、Chroma/Numpy 混用切换。
        """
        count = 0
        for m in self._kg.iter_all():
            self._vec.upsert(m.id, self._emb.embed(m.content), {"id": m.id})
            count += 1
        self._bm25 = None   # 词法索引下次检索时从原文重建
        return count

    # ── 防篡改验证（可选区块链）──────────────

    def verify_integrity(self, wing: str | None = None,
                         topic_key: str = "") -> dict:
        """
        验证哈希链完整性，检测记忆原文是否被篡改。
        topic_key 非空时只验证 (wing, topic_key) 子链（如单个契约的审计链）。
        """
        if self._chain is None:
            return {"enabled": False,
                    "message": "区块链未启用（构造时 use_blockchain=True 开启）"}
        wings = [wing] if wing else (self._kg.list_wings() or [self.wing])
        results = {}
        for w in wings:
            ok, msg = self._chain.verify_chain(w, topic_key)
            results[w] = {"valid": ok, "message": msg}
        return {"enabled": True, "wings": results}

    # ── 时序知识图谱查询 ───────────────────────

    def entity(self, name: str) -> dict | None:
        """查询实体画像：当前属性快照 + 状态时间线 + 关联关系。"""
        return self._graph.get_entity(name)

    def entity_timeline(self, name: str) -> list[dict]:
        """实体各属性的状态演化（按时间升序）。"""
        return self._graph.entity_timeline(name)

    def entity_relations(self, name: str) -> list[dict]:
        """与实体直接相连的所有关系。"""
        return self._graph.entity_relations(name)

    def list_entities(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """按提及次数降序列出全部实体。"""
        return self._graph.list_entities(limit, offset)

    def graph_stats(self) -> dict:
        """时序知识图谱统计（实体 / 状态 / 关系数量）。"""
        return self._graph.stats()

    # ── 统计 ──────────────────────────────────

    def stats(self) -> dict:
        s = self._kg.stats()
        s["vector_backend"] = type(self._vec).__name__
        s["embedding_backend"] = type(self._emb).__name__
        if hasattr(self._vec, "count"):
            s["indexed_vectors"] = self._vec.count()
        if self._chain is not None:
            s["blockchain"] = self._chain.stats()
        s["graph"] = self._graph.stats()
        return s

    # ── 辅助 ──────────────────────────────────

    # ── 来源追踪 ──────────────────────────────

    # 只有这些键由引擎维护；调用方可以补充其他键（如 source_ref、channel、message_id）
    _PROVENANCE_ENGINE_KEYS = ("created_at",)

    def _build_provenance(self, given: dict | None, meta: dict,
                          session_id: str, owner: str) -> dict:
        """组装来源记录：写入入口、写入者、会话、轮次、时间，以及调用方补充的字段。

        优先级：引擎维护的 created_at > provenance 参数 > metadata["provenance"] > 默认值。
        metadata 里的 turn_index / role（archive_session 写入）会同步进来。
        """
        prov: dict = {"via": self.DEFAULT_VIA}
        agent = owner or self.owner
        if agent:
            prov["agent"] = agent
        if session_id:
            prov["session_id"] = session_id
        for k in ("turn_index", "role"):
            if k in meta:
                prov[k] = meta[k]
        old = meta.pop("provenance", None)
        if isinstance(old, dict):
            prov.update(old)
        if given:
            if not isinstance(given, dict):
                raise TypeError("provenance 必须是 dict")
            prov.update(given)
        for k in self._PROVENANCE_ENGINE_KEYS:
            prov.pop(k, None)
        prov["created_at"] = time.time()
        return {k: v for k, v in prov.items() if v is not None and v != ""}

    def _record_merge(self, existing: Memory, prov: dict) -> None:
        """去重合并时记下这次重复写入的来源（最多保留 PROVENANCE_MAX_MERGES 条，超出计数）。"""
        merges = list(existing.metadata.get("merged_from") or [])
        entry = {k: prov[k] for k in ("via", "agent", "session_id", "turn_index", "source_ref")
                 if k in prov}
        entry["at"] = prov.get("created_at", time.time())
        if len(merges) < self.PROVENANCE_MAX_MERGES:
            merges.append(entry)
        else:
            existing.metadata["merged_dropped"] = existing.metadata.get("merged_dropped", 0) + 1
        existing.metadata["merged_from"] = merges

    def provenance(self, memory_id: str) -> dict:
        """一条记忆的完整来源：写入来源、重复写入、取代关系、蒸馏来源。

        v2.4 及更早写入的记忆没有 provenance，返回的 origin 只含能从字段推断的内容
        （session_id / owner / 写入时间），并标记 legacy=True。
        """
        m = self._kg.get(memory_id)
        if not m:
            raise ValueError(f"记忆不存在: {memory_id}")
        if self.owner and not self._visible_to(m, self.owner):
            raise ValueError(f"记忆不存在: {memory_id}")
        md = m.metadata
        origin = md.get("provenance")
        if not origin:
            origin = {"legacy": True, "created_at": m.timestamp}
            if m.session_id:
                origin["session_id"] = m.session_id
            if m.owner:
                origin["agent"] = m.owner
            if "turn_index" in md:
                origin["turn_index"] = md["turn_index"]
            if md.get("source"):
                origin["via"] = md["source"]
        out = {
            "id": m.id,
            "origin": origin,
            "occurrences": md.get("occurrences", 1),
            "merged_from": md.get("merged_from", []),
            "superseded_by": md.get("superseded_by"),
            "supersedes": md.get("supersedes", []),
        }
        if md.get("merged_dropped"):
            out["merged_dropped"] = md["merged_dropped"]
        if md.get("distilled"):
            out["derived_from"] = md.get("sources", [])
        if md.get("distilled_into"):
            out["distilled_into"] = md["distilled_into"]
        return out

    # ── 矛盾检测 ──────────────────────────────

    def _find_contradictions(self, content: str, wing: str,
                             viewer: str) -> list[tuple[Memory, Verdict]]:
        """在已有记忆里找被 content 推翻的记忆。

        候选：向量相似度在 [CONTRADICTION_MIN_SIM, dedup_threshold) 之间的前几条，
        同 wing、对写入者可见、不是 immutable、尚未被取代。
        规则模式下，新记忆没有变更信号时直接返回（不做检索）。
        """
        if not self._contradiction.needs_candidates(content):
            return []
        vec = self._emb.embed(content)
        raw = self._vec.query(vec, n=self.CONTRADICTION_CANDIDATES * 2,
                              min_score=self.CONTRADICTION_MIN_SIM)
        raw = [r for r in raw if r[1] < self.dedup_threshold][: self.CONTRADICTION_CANDIDATES]
        if not raw:
            return []
        mem_map = self._kg.get_many([r[0] for r in raw])
        out: list[tuple[Memory, Verdict]] = []
        for mid, sim, _p in raw:
            m = mem_map.get(mid)
            if m is None or m.wing != wing or m.retention_policy == "immutable":
                continue
            if m.metadata.get("superseded_by"):
                continue
            if viewer and not self._visible_to(m, viewer):
                continue
            v = self._contradiction.judge(m.content, content)
            if v is not None:
                v.detail = dict(v.detail, similarity=round(float(sim), 3))
                out.append((m, v))
        return out

    def _apply_supersede(self, new: Memory, conflicts: list[tuple[Memory, Verdict]],
                         manual: bool = False) -> None:
        now = time.time()
        for old, v in conflicts:
            old.metadata["superseded_by"] = new.id
            old.metadata["superseded_at"] = now
            old.metadata["supersede_reason"] = v.reason
            old.metadata["supersede_method"] = "manual" if manual else v.method
            old.metadata["supersede_confidence"] = v.confidence
            self._kg.save(old)
            self._kg.add_link(new.id, old.id, link_type="supersedes", weight=v.confidence)
        prev = list(new.metadata.get("supersedes") or [])
        new.metadata["supersedes"] = prev + [o.id for o, _ in conflicts if o.id not in prev]
        self._kg.save(new)

    def _superseded_weight(self, m: Memory) -> float:
        return self.SUPERSEDED_WEIGHT if m.metadata.get("superseded_by") else 1.0

    def _apply_superseded_weight(self, results: list[RetrievalResult],
                                 include_superseded: bool) -> list[RetrievalResult]:
        """被取代的记忆分数乘以 SUPERSEDED_WEIGHT 后重新排序（稳定排序，其余顺序不变）。

        rrf / bm25 下 score 是名次融合分数，乘以系数同样会让它排到后面。
        include_superseded=True 时原样返回。
        """
        if include_superseded or not any(r.memory.metadata.get("superseded_by") for r in results):
            return results
        for r in results:
            if r.memory.metadata.get("superseded_by"):
                r.score *= self.SUPERSEDED_WEIGHT
        return sorted(results, key=lambda r: r.score, reverse=True)

    def contradictions(self, wing: str | None = None) -> list[dict]:
        """列出全部「被取代」记录：[{old, new, reason, method, confidence, at}]，按时间倒序。"""
        out = []
        for m in self._kg.iter_all(wing=wing):
            nid = m.metadata.get("superseded_by")
            if not nid:
                continue
            if self.owner and not self._visible_to(m, self.owner):
                continue
            out.append({
                "old_id": m.id, "old": m.content, "new_id": nid,
                "new": (self._kg.get(nid).content if self._kg.get(nid) else None),
                "reason": m.metadata.get("supersede_reason", ""),
                "method": m.metadata.get("supersede_method", ""),
                "confidence": m.metadata.get("supersede_confidence"),
                "at": m.metadata.get("superseded_at"),
            })
        out.sort(key=lambda d: d["at"] or 0, reverse=True)
        return out

    def supersede(self, old_id: str, new_id: str, reason: str = "manual") -> None:
        """手动标记 new 取代 old（规则没检测到时使用）。"""
        old, new = self._kg.get(old_id), self._kg.get(new_id)
        if not old or not new:
            raise ValueError("记忆不存在")
        if old.id == new.id:
            raise ValueError("不能取代自己")
        if old.retention_policy == "immutable":
            raise ValueError("immutable 记忆不参与取代")
        self._apply_supersede(new, [(old, Verdict(confidence=1.0, reason=reason, method="manual"))],
                              manual=True)

    def unsupersede(self, old_id: str) -> Memory:
        """撤销取代标记（误判时使用）：恢复 old 的正常权重，并从新记忆的 supersedes 中移除。"""
        old = self._kg.get(old_id)
        if not old:
            raise ValueError(f"记忆不存在: {old_id}")
        nid = old.metadata.pop("superseded_by", None)
        for k in ("superseded_at", "supersede_reason", "supersede_method", "supersede_confidence"):
            old.metadata.pop(k, None)
        old.metadata["supersede_dismissed"] = nid
        self._kg.save(old)
        new = self._kg.get(nid) if nid else None
        if new is not None:
            new.metadata["supersedes"] = [x for x in (new.metadata.get("supersedes") or []) if x != old_id]
            self._kg.save(new)
        return old

    def _heuristic_importance(self, content: str) -> float:
        """启发式重要性评分（委托给 extractor 模块，保持单处定义）"""
        return heuristic_importance(content)

    def _dedup_candidate(self, content: str) -> Memory | None:
        """返回与 content 语义高度相似的既有记忆（用于去重合并），无则 None。"""
        vec = self._emb.embed(content)
        raw = self._vec.query(vec, n=1, min_score=self.dedup_threshold)
        if not raw:
            return None
        return self._kg.get(raw[0][0])

    def _ingest_graph(self, ext: Extraction | None,
                      memory_id: str, timestamp: float) -> None:
        """把一次抽取结果写入时序知识图谱（ext 为 None 时跳过）。"""
        if ext is None:
            return
        self._graph.ingest(
            ext.entities, ext.relations, ext.entity_states,
            memory_id=memory_id, timestamp=timestamp,
        )

    def close(self):
        self._kg.close()
        self._graph.close()
        if self._chain is not None:
            self._chain.close()
        if hasattr(self._vec, "close"):
            self._vec.close()
        if self._lex_cache is not None:
            self._lex_cache.close()
            self._lex_cache = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
