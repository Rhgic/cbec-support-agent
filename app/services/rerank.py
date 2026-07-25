"""BGE-reranker 重排（P1）：向量召回是"粗筛"，reranker 是"精排"。

为什么两阶段：向量检索快，但对精确词（订单号/SKU/产品名）和跨语种细粒度区分差；
交叉编码 reranker 把 (query, passage) 成对喂进模型逐一打分，精度高但慢——所以只对
召回的少量候选跑（k=20），CPU 上毫秒级。跨语种同族模型 bge-reranker-v2-m3，与 BGE-m3 配套。

实现：直接用 transformers 加载（AutoModelForSequenceClassification 交叉编码打分头），
绕开 FlagReranker 在新版 transformers 上的 tokenizer 兼容问题（XLMRobertaTokenizer 报错）。

边界：重排只改"哪些 chunk、什么顺序进生成"，**不改短路阈值**——短路仍用向量相似度
（已在 0.45 校准，见 retrieve.py 取候选内最大向量分）。rerank_score 与向量 score 并存。
"""
from functools import lru_cache

from app.config import get_settings

settings = get_settings()


@lru_cache
def _model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(settings.rerank_model)
    model = AutoModelForSequenceClassification.from_pretrained(settings.rerank_model)
    model.eval()
    return tok, model, torch


def rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """对候选按 (query, content) 交叉打分，降序取 top_k。写入 rerank_score，保留原向量 score。"""
    if not hits or len(hits) <= 1:
        return hits[:top_k]
    tok, model, torch = _model()
    pairs = [[query, h["content"]] for h in hits]
    with torch.no_grad():
        inputs = tok(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
        scores = model(**inputs).logits.view(-1).float().tolist()
    for h, s in zip(hits, scores, strict=False):
        h["rerank_score"] = float(s)
    hits.sort(key=lambda h: h.get("rerank_score", 0.0), reverse=True)
    return hits[:top_k]
