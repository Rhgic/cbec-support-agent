"""Milvus 向量检索：先按 category 标量过滤，再按向量排序。

为什么用 Milvus（本项目技术栈选定）：专用 ANN 引擎 + 标量过滤 + HNSW 索引。
开发用 Milvus Lite（pymilvus 内置、本地文件、免 docker）；把 MILVUS_URI 指向
http://host:19530 即无缝切到 standalone 集群，代码不变。

数据分工：chunk 的文本 / 元数据仍以 Postgres 为记录源（build_knowledge 写入），
embed_knowledge 读 PG、编码后灌进 Milvus。查询与文档同模型（BGE-m3），跨语种无需翻译。
score = COSINE 相似度（越大越相似），与 retrieve 的短路阈值语义一致。
"""
import os
from functools import lru_cache

from app.config import get_settings
from app.services.embedding import EMBED_DIM, encode_query

settings = get_settings()

# Top-K 召回数；eval_retrieval.py 会按不同 k 评估
DEFAULT_K = 5


@lru_cache
def _client():
    from pymilvus import MilvusClient

    uri = settings.milvus_uri
    # Lite 模式是本地文件路径（无 "://"）：必须转成绝对路径。
    # worker/API 的工作目录未必是项目根，相对路径会让 pymilvus 误判为 URI 并报
    # "Illegal uri"。以本文件位置回溯到项目根来解析。
    if "://" not in uri:
        if not os.path.isabs(uri):
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            uri = os.path.join(root, uri)
        os.makedirs(os.path.dirname(uri) or ".", exist_ok=True)
    return MilvusClient(uri)


def ensure_collection() -> None:
    """建集合 + HNSW/COSINE 索引（幂等）。category 作标量字段供「先过滤再检索」。"""
    from pymilvus import DataType

    client = _client()
    name = settings.milvus_collection
    if client.has_collection(name):
        return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)  # = knowledge_chunks.id
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("category", DataType.VARCHAR, max_length=16)
    schema.add_field("content", DataType.VARCHAR, max_length=8192)
    schema.add_field("source_url", DataType.VARCHAR, max_length=512)
    index = client.prepare_index_params()
    # HNSW + COSINE：先按 category 过滤子集，再对子集做近邻排序
    index.add_index("embedding", index_type="HNSW", metric_type="COSINE",
                    params={"M": 16, "efConstruction": 200})
    client.create_collection(name, schema=schema, index_params=index)


def upsert(items: list[dict]) -> int:
    """items: [{id, embedding, category, content, source_url}]。按 id 覆盖，返回条数。"""
    if not items:
        return 0
    ensure_collection()
    client = _client()
    client.upsert(settings.milvus_collection, items)
    client.flush(settings.milvus_collection)
    return len(items)


@lru_cache
def _ensure_loaded() -> bool:
    # Milvus 检索前须把集合 load 进内存（跨进程默认 released）。每进程加载一次。
    _client().load_collection(settings.milvus_collection)
    return True


def count() -> int:
    client = _client()
    if not client.has_collection(settings.milvus_collection):
        return 0
    return int(client.get_collection_stats(settings.milvus_collection).get("row_count", 0))


def search(db, category: str, query: str, k: int = DEFAULT_K) -> list[dict]:
    """两阶段检索：向量召回 recall_k → reranker 精排取 top-k（P1）。

    db 参数仅为兼容既有调用签名（Milvus 不需要 SQLAlchemy 会话）而保留。
    每条 hit 带 `score`=COSINE 向量相似度（短路阈值仍用它，已校准 0.45）；
    开启 rerank 时另带 `rerank_score`，列表顺序按 rerank 分。
    """
    client = _client()
    if not client.has_collection(settings.milvus_collection):
        return []
    _ensure_loaded()
    q = encode_query(query)
    limit = settings.recall_k if settings.rerank_enabled else k
    res = client.search(
        settings.milvus_collection,
        data=[q],
        limit=limit,
        filter=f'category == "{category}"',
        output_fields=["content", "source_url"],
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
    )
    hits = res[0] if res else []
    out = [
        {
            "chunk_id": h["id"],
            "content": h["entity"]["content"],
            "score": float(h["distance"]),
            "source_url": h["entity"]["source_url"],
        }
        for h in hits
    ]
    # 精排：只对召回的候选重排，不改短路阈值语义
    if settings.rerank_enabled and len(out) > 1:
        from app.services.rerank import rerank
        return rerank(query, out, top_k=k)
    return out[:k]
