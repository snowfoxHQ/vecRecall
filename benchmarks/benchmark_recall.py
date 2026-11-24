"""VecRecall 内置召回率评测（R@1/5/10、nDCG@10、MRR）。

规模很小（21 组），只做快速冒烟检查；正式评测见 benchmarks/longmemeval.py。

可复现：内置中英混合评测集（query → 正确文档 + 干扰文档池），
评测不同嵌入后端的**纯向量** R@5（不经过结构过滤，与检索路径设计一致）。

用法：
    python benchmarks/benchmark_recall.py                # 评测 bow（零依赖词法）
    python benchmarks/benchmark_recall.py --all          # 评测 bow + sentence（语义模型，首次需下载）
    python benchmarks/benchmark_recall.py --model xxx    # 指定语义模型
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from vecrecall.core.engine import (  # noqa: E402
    VecRecall, NumpyVectorBackend, BagOfWordsEmbeddingBackend,
    build_embedding_backend,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _metrics import average, score  # noqa: E402

# 每个元素：(用户查询, 正确答案文档)
# 覆盖：中文词面重叠 / 中文近义改写 / 英文词形变化 / 中英混杂 / 生活偏好
CASES = [
    # 中文·词面部分重叠
    ("数据库连接池最大连接数是多少", "数据库连接池 max connections 配置为 100"),
    ("服务端口改成多少了", "HTTP 服务端口从 8080 改成了 9090"),
    ("请求超时设了多久", "请求 timeout 超时时间设置为 30 秒"),
    ("Alice 在哪个公司工作", "Alice 就职于 Google 担任工程师"),
    ("Bob 负责什么项目", "Bob 是 VecRecall 项目的核心开发者"),
    ("下周要开什么会", "下周三上午十点与客户开需求评审会"),
    ("项目截止日期是哪天", "deadline 是 10 月 15 日"),
    ("老板喜欢什么样的汇报", "领导偏好简洁、带数据图表的报告"),
    # 中文·近义改写（词面几乎不重叠，语义等价）
    ("怎么修复内存泄漏", "解决 memory leak 的几种方法"),
    ("程序崩溃了怎么办", "应用 crash 时的排查步骤"),
    ("如何提升查询速度", "优化 query 性能的几种手段"),
    ("登录失败了怎么办", "认证 authentication 报错的解决办法"),
    ("如何配置密钥", "设置 OpenAI 的 api_key 环境变量"),
    ("向量维度是多少", "embedding 向量的 dimension 设为 512"),
    # 英文·词形变化 / 同义改写
    ("how to run the tests", "instructions for executing the test suite"),
    ("database connection settings", "the configuration of the database connection pool"),
    ("error when saving files", "exception occurs during file persistence"),
    ("how to fix a crash", "troubleshooting steps for application crashes"),
    # 生活 / 偏好
    ("今天中午吃了什么", "中午吃了一碗牛肉面"),
    ("周末去哪里玩", "计划周六去爬山"),
    ("用户最讨厌什么", "用户反感冗长的解释，喜欢直接给结论"),
]

DISTRACTORS = [
    "明天天气预报有小雨",
    "Python 3.12 发布了新的类型语法",
    "今天股市大盘上涨",
    "孩子学校下周一开家长会",
    "小区物业费下个月上调",
    "最近上映了一部科幻电影",
    "超市周末全场八折",
    "健身计划每周三次有氧",
    "三亚五日游旅行攻略",
    "红烧肉的家常做法",
    "推荐的科幻小说是三体",
    "公司年会有抽奖环节",
    "快递已经送达菜鸟驿站",
    "手机提示系统更新",
    "健身房新来的教练",
]


def evaluate(embedder, cases=CASES, distractors=DISTRACTORS, hybrid=False):
    """返回指标 dict。所有正确文档 + 干扰文档混入同一向量库，逐 query 检索 top-10。"""
    with tempfile.TemporaryDirectory() as d:
        p = VecRecall(
            base_dir=d, wing="bench", embedding_backend=embedder,
            vector_backend=NumpyVectorBackend(), dedup=False,
        )
        correct_ids = []
        for _q, doc in cases:
            correct_ids.append(p.add(doc, topic="bench").id)
        for dist in distractors:
            p.add(dist, topic="distractor")

        rows = []
        for (q, _doc), cid in zip(cases, correct_ids):
            ranked = [r.memory.id for r in p.search(q, n=10, hybrid=hybrid)]
            rows.append(score(ranked, {cid}, (1, 5, 10)))
        p.close()
    return average(rows)


def run(name, embedder, hybrid=False):
    m = evaluate(embedder, hybrid=hybrid)
    print(f"  {name:<22} R@1 = {m['recall_any@1'] * 100:5.1f}%   "
          f"R@5 = {m['recall_any@5'] * 100:5.1f}%   R@10 = {m['recall_any@10'] * 100:5.1f}%   "
          f"nDCG@10 = {m['ndcg@10']:.3f}   MRR = {m['mrr']:.3f}")
    return m['recall_any@5']


def main():
    import argparse
    ap = argparse.ArgumentParser(description="VecRecall 内置召回率评测（快速冒烟）")
    ap.add_argument("--all", action="store_true", help="同时评测语义模型（sentence）")
    ap.add_argument("--model", default=None, help="语义模型名（默认多语言 MiniLM）")
    args = ap.parse_args()

    print("=" * 78)
    print("VecRecall 内置召回率评测（纯向量，无结构过滤）")
    print(f"评测集：{len(CASES)} 组 query + {len(DISTRACTORS)} 干扰文档 = "
          f"{len(CASES) + len(DISTRACTORS)} 文档候选")
    print("说明：规模很小，只用于快速检查是否改坏；正式数字请用 benchmarks/longmemeval.py")
    print("=" * 78)

    bow = BagOfWordsEmbeddingBackend()
    run("bow（零依赖词法）", bow)
    run("bow + hybrid", bow, hybrid=True)

    if args.all or args.model:
        print()
        try:
            emb = build_embedding_backend("sentence", model=args.model)
            run("sentence（语义）", emb)
            run("sentence + hybrid", emb, hybrid=True)
        except Exception as e:  # noqa: BLE001
            print(f"  sentence 评测未完成：{type(e).__name__}: {e}")
            print("  （模型未下载或网络不可用，可先 `pip install -e \".[sentence]\"`）")

    print("=" * 78)


if __name__ == "__main__":
    main()
