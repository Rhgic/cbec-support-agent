"""BGE-m3 封装：统一对外提供 encode 接口。

为什么单独封装：查询与文档必须用同一模型、同一维度（1024），集中在这里避免各处
重复加载权重。FlagEmbedding 较重，延迟到首次调用再 import，避免导入即初始化拖慢启动。
所有 embedding 走这里，业务代码不直接碰 FlagEmbedding。
"""
import os
from functools import lru_cache

from app.config import get_settings

# 必须在 torch / tokenizers 初始化前设置：HF tokenizers 的并行 fork 与 Milvus(gRPC)
# 共存会段错误（fork-after-gRPC）。关掉分词器并行即可，代价可忽略。
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

settings = get_settings()

# 查询与文档共享同一模型，跨语种无需翻译，维度严格 1024
EMBED_DIM = 1024


@lru_cache
def _model():
    # 延迟加载：无 GPU / 无模型权重环境下，模块导入不触发下载与初始化
    # 注意：FlagEmbedding 的 BGE-M3 类是 BGEM3FlagModel（原实现误写成不存在的 BGEM3Model）
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(
        settings.bge_m3_model,
        use_fp16=False,
        devices=[settings.bge_m3_device],
    )


def encode_texts(texts: list[str]) -> list[list[float]]:
    """对一批文本编码，返回 1024 维向量列表（dense）。空输入返回空列表。"""
    if not texts:
        return []
    out = _model().encode(texts, max_length=8192)
    # BGEM3Model.encode 返回 dict；检索只用 dense 向量
    return [list(map(float, v)) for v in out["dense_vecs"]]


def encode_query(text: str) -> list[float]:
    """单条查询编码（与文档同模型，跨语种检索的关键）。"""
    return encode_texts([text])[0]
