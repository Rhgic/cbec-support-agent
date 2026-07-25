"""向量化入库（Milvus）：读 Postgres 的 knowledge_chunks（build_knowledge 写入），
编码后灌进 Milvus 集合。

Postgres 仍是内容 / 元数据的记录源；Milvus 是向量索引。可重复运行——用 Milvus upsert
按 chunk id 覆盖，已灌的重跑即更新。必须先 build_knowledge 建好 chunk。
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import KnowledgeChunk, KnowledgeDoc
from app.services.embedding import encode_texts
from app.services.vectorstore import upsert


def embed() -> int:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeChunk.category,
                KnowledgeDoc.source_url,
            ).join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
        ).all()
    finally:
        db.close()

    if not rows:
        return 0
    vecs = encode_texts([r.content for r in rows])
    items = [
        {
            "id": r.id,
            "embedding": v,
            "category": r.category,
            "content": r.content,
            "source_url": r.source_url,
        }
        for r, v in zip(rows, vecs, strict=True)
    ]
    return upsert(items)


def main() -> None:
    n = embed()
    print(f"upserted {n} vectors into Milvus")


if __name__ == "__main__":
    main()
