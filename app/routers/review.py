"""人工审核路由（规格 9）。

GET  /review/queue：待审列表（status=awaiting_review 且有草稿）
POST /review/{ticket_id}：{action, final_reply?, failure_tags?}
      approved/edited → 工单标记 closed（出站/放行）
      rejected        → 工单标记 failed
      同时写入 reviews 表（人工修正样本收集，供离线归因；不做自动回流）

为什么收集 failure_tags：规格 4.7 的 reviews 表是「失败模式」样本的载体，只落库供离线
分析，不在线上回流——避免把人工修正当成训练数据自动吃回去。
"""
from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.database import SessionLocal
from app.models import Review, Ticket
from app.schemas import ReviewCreate, ReviewOut

settings = get_settings()
router = APIRouter(tags=["review"])


def require_token(authorization: str | None = Header(default=None)) -> str:
    # 演示端固定 token（规格 1.2 不做注册登录体系）
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    return settings.demo_token


@router.get("/review/queue", response_model=list[ReviewOut])
def review_queue(token: str = Depends(require_token)):
    db = SessionLocal()
    try:
        rows = (
            db.query(Ticket)
            .filter(Ticket.status == "awaiting_review", Ticket.draft_reply.isnot(None))
            .order_by(Ticket.id)
            .all()
        )
        return [
            ReviewOut(
                ticket_id=t.id,
                risk_level=t.risk_level or "high",
                draft_reply=t.draft_reply or "",
                action=t.action,
            )
            for t in rows
        ]
    finally:
        db.close()


@router.post("/review/{ticket_id}", response_model=dict)
def review(ticket_id: int, payload: ReviewCreate, token: str = Depends(require_token)):
    if payload.action not in ("approved", "edited", "rejected"):
        raise HTTPException(status_code=422, detail="action 必须是 approved/edited/rejected")

    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="工单不存在")

        db.add(
            Review(
                ticket_id=ticket_id,
                risk_level=t.risk_level or "high",
                draft_reply=t.draft_reply or "",
                final_reply=payload.final_reply,
                reviewer_action=payload.action,
                failure_tags=payload.failure_tags,
            )
        )
        # approved/edited 放行；rejected 视为失败，不自动出站
        t.status = "closed" if payload.action in ("approved", "edited") else "failed"
        db.commit()
        return {"ticket_id": ticket_id, "status": t.status}
    finally:
        db.close()
