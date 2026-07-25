"""知识库管理路由（规格 9）。

GET  /knowledge/chunks：浏览切片（可按 category 过滤），便于核对检索语料
POST /knowledge/build：触发 build_knowledge（插入 docs + chunks，embedding 留空）
     向量化由脚本 scripts/embed_knowledge.py 单独跑（需 BGE-m3 权重）

为什么 build 与 embed 分离：build 纯 DB 写、可随时跑；embed 需加载模型、耗资源，
且依赖模型权重，故拆到 CLI 脚本，不在请求路径内同步执行。
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import KnowledgeChunk
from scripts.build_knowledge import build

settings = get_settings()
router = APIRouter(tags=["knowledge"])


def require_token(authorization: str | None = Header(default=None)) -> str:
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    return settings.demo_token


@router.get("/knowledge/chunks")
def list_chunks(
    category: str | None = Query(default=None),
    limit: int = Query(default=20, le=200),
    token: str = Depends(require_token),
):
    db = SessionLocal()
    try:
        stmt = select(
            KnowledgeChunk.id,
            KnowledgeChunk.category,
            KnowledgeChunk.content,
            KnowledgeChunk.token_count,
        )
        if category:
            stmt = stmt.where(KnowledgeChunk.category == category)
        rows = db.execute(stmt.limit(limit)).fetchall()
        return [
            {
                "chunk_id": r.id,
                "category": r.category,
                "content": r.content,
                "token_count": r.token_count,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("/knowledge/build")
def build_knowledge(
    category: str,
    raw_dir: str | None = None,
    token: str = Depends(require_token),
):
    if category not in ("return", "logistics", "product", "general"):
        raise HTTPException(status_code=422, detail="category 非法")
    n = build(category, raw_dir)
    return {"inserted_docs": n, "note": "切片已建，运行 scripts/embed_knowledge.py 完成向量化"}
