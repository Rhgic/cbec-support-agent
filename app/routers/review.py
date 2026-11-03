"""人工审核路由（规格 9）。

GET  /review/queue：待审列表（status=awaiting_review 且有草稿）
POST /review/{ticket_id}：{action, final_reply?, failure_tags?, corrected_*?}
      approved/edited → 工单标记 ready_to_send（等待渠道发送）
      rejected        → 工单标记 failed
      同时写入 reviews 表（人工修正样本收集，供离线导出；不做线上自动回流）

为什么收集 corrected_*：只有人工明确纠正的字段才生成 gold 标签；普通 approved 不会被
误当成标注数据。导出后仍需人工复核，避免把一次误操作自动吃回线上。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.auth import require_token
from app.config import get_settings
from app.database import SessionLocal
from app.models import Review, Ticket
from app.schemas import ReviewCreate, ReviewOut

settings = get_settings()
router = APIRouter(tags=["review"])


@router.get("/review/queue", response_model=list[ReviewOut])
def review_queue(
    token: str = Depends(require_token), limit: int = Query(50, ge=1, le=200), after_id: int = Query(0, ge=0)
):
    db = SessionLocal()
    try:
        rows = (
            db.query(Ticket)
            .filter(Ticket.status == "awaiting_review", Ticket.draft_reply.isnot(None), Ticket.id > after_id)
            .order_by(Ticket.id)
            .limit(limit)
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
        t = db.execute(select(Ticket).where(Ticket.id == ticket_id).with_for_update()).scalar_one_or_none()
        if t is None:
            raise HTTPException(status_code=404, detail="工单不存在")

        if t.status != "awaiting_review" or not t.draft_reply:
            raise HTTPException(status_code=409, detail="工单不处于可审核状态")
        final_reply = (
            payload.final_reply
            if payload.action == "edited"
            else (t.draft_reply if payload.action == "approved" else None)
        )

        db.add(
            Review(
                ticket_id=ticket_id,
                risk_level=t.risk_level or "high",
                draft_reply=t.draft_reply or "",
                final_reply=final_reply,
                reviewer_action=payload.action,
                failure_tags=payload.failure_tags,
                corrected_lang=payload.corrected_lang,
                corrected_intent=payload.corrected_intent,
                corrected_risk_level=payload.corrected_risk_level,
            )
        )
        # approved/edited 放行；rejected 视为失败，不自动出站
        t.status = "ready_to_send" if payload.action in ("approved", "edited") else "failed"
        if payload.action in ("approved", "edited"):
            t.draft_reply = final_reply
        db.commit()
        return {"ticket_id": ticket_id, "status": t.status}
    finally:
        db.close()
