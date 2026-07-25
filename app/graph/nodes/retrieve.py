"""③ 检索节点：跨语种检索（查询与文档同模型，无需翻译）。

先按 intent 作为 category 过滤，再取 Top-K；retrieval_score = Top-1 余弦相似度。
短路机制：retrieval_score < THRESHOLD → short_circuited=True，跳过工具与生成（无依据
不得自动出站）。
"""
from app.database import SessionLocal
from app.graph.state import TicketState
from app.services.vectorstore import DEFAULT_K, search

# ===== 检索置信度阈值 =====
# score = 余弦相似度（vectorstore 用 Milvus COSINE metric 返回），越大越相似；
# BGE-m3 归一化向量下相关文本实测多落在 ~0.3~0.9，越大越相似。
#
# 0.45 来自 scripts/sweep_threshold.py 在真实 BGE-m3 + Milvus 上的实测曲线：
#   阈值   误拒率(有答案却短路)   幻觉暴露(无答案却放行)
#   0.40        0%                  12.5%
#   0.45        0%                   0%    ← 拐点，取 0.45
#   0.50       13.6%                 0%
#   0.60       45.5%                 0%    ← 原猜测值偏高，误拒近半有答案工单
# 数据：22 answerable / 24 adversarial（合成，人味工单；知识库 9 chunk）。
#    接入真实工单后须重跑 sweep_threshold 复核，再据新曲线定稿并留存作为面试论据。
THRESHOLD: float = 0.45


def retrieve(state: TicketState) -> dict:
    intent = state["intent"]
    query = state.get("masked_text") or state["raw_text"]

    db = SessionLocal()
    try:
        chunks = search(db, intent, query, k=DEFAULT_K)
    finally:
        db.close()

    if not chunks:
        return {"chunks": [], "retrieval_score": 0.0, "short_circuited": True}

    # 取候选内最大向量相似度做短路判定——rerank 会重排列表顺序，不能再拿 chunks[0]；
    # 短路仍基于向量分（已在 0.45 校准），rerank 只影响进生成的 chunk 选择与顺序。
    top = max(c["score"] for c in chunks)
    # THRESHOLD 已设定：低于阈值时短路（无依据不自动出站）；THRESHOLD=None 时保守地一律短路
    short_circuited = THRESHOLD is None or top < THRESHOLD
    return {"chunks": chunks, "retrieval_score": top, "short_circuited": short_circuited}
