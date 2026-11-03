"""工单路由：提交 / 查询 / trace（规格 9）。

POST /tickets：护栏（熔断→503 / 限流→429 / 配额→429）→ 脱敏 →
  入队 arq 异步处理，返回 {ticket_id, task_id}
GET  /tickets/{id}：状态与结果（含各节点耗时聚合展示所需字段）
GET  /tickets/{id}/trace：agent_runs 全链路，供演示
"""

from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text

from app.auth import require_token
from app.config import get_settings
from app.database import SessionLocal
from app.models import AgentRun, Ticket
from app.schemas import AgentRunOut, TicketCreate, TicketOut, TicketTrace
from app.services.guardrails import (
    check_breaker,
    check_quota,
    check_rate_limit,
)
from app.services.pii import mask_text

settings = get_settings()
router = APIRouter(tags=["tickets"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/tickets")
async def create_ticket(
    payload: TicketCreate,
    request: Request,
    token: str = Depends(require_token),
):
    ip = _client_ip(request)

    # 成本护栏：熔断时全局拒绝新任务（降级方向：放行并告警——这里返回 503 即拒绝）
    if not check_breaker():
        raise HTTPException(status_code=503, detail="全局熔断：今日 LLM 成本已超限")
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁", headers={"Retry-After": "60"})
    if not check_quota(token):
        raise HTTPException(status_code=429, detail="今日配额已用尽", headers={"Retry-After": "86400"})

    masked, _ = mask_text(payload.text)

    db = SessionLocal()
    try:
        ticket = Ticket(raw_text=payload.text, masked_text=masked, status="processing")
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        tid = ticket.id
    finally:
        db.close()

    # 入队异步处理
    try:
        from arq import create_pool

        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            job = await redis.enqueue_job("process_ticket", tid, _job_id=f"ticket:{tid}")
            if job is None:
                raise RuntimeError("任务未入队")
            task_id = job.job_id
        finally:
            await redis.aclose()
    except Exception:
        # 队列不可用：无法异步处理，标记 failed（护栏降级放行指限流/配额，队列故障则失败）
        db = SessionLocal()
        try:
            t = db.get(Ticket, tid)
            if t:
                t.status = "failed"
                db.commit()
        finally:
            db.close()
        raise HTTPException(status_code=503, detail="任务队列不可用") from None

    return {"ticket_id": tid, "task_id": task_id}


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, token: str = Depends(require_token)):
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="工单不存在")
        return TicketOut(
            ticket_id=t.id,
            status=t.status,
            lang=t.lang,
            intent=t.intent,
            intent_confidence=t.intent_confidence,
            intent_method=t.intent_method,
            retrieval_score=t.retrieval_score,
            short_circuited=t.short_circuited,
            draft_reply=t.draft_reply,
            citations=t.citations or [],
            risk_level=t.risk_level,
            action=t.action,
        )
    finally:
        db.close()


@router.get("/tickets/{ticket_id}/trace", response_model=TicketTrace)
def get_trace(ticket_id: int, token: str = Depends(require_token)):
    db = SessionLocal()
    try:
        rows = db.execute(select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.id)).scalars().all()
        return TicketTrace(
            ticket_id=ticket_id,
            runs=[
                AgentRunOut(
                    node=r.node,
                    latency_ms=r.latency_ms,
                    token_in=r.token_in,
                    token_out=r.token_out,
                    cost_usd=float(r.cost_usd) if r.cost_usd is not None else None,
                    ok=r.ok,
                    error=r.error,
                )
                for r in rows
            ],
        )
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/retry")
async def retry_ticket(ticket_id: int, token: str = Depends(require_token)):
    from uuid import uuid4

    from arq import create_pool

    db = SessionLocal()
    redis = None
    try:
        ticket = db.execute(select(Ticket).where(Ticket.id == ticket_id).with_for_update()).scalar_one_or_none()
        if ticket is None:
            raise HTTPException(status_code=404, detail="工单不存在")
        if ticket.status not in {"failed", "deferred"}:
            raise HTTPException(status_code=409, detail="仅失败或延期工单可重试")
        locked = db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ticket_id}).scalar()
        if not locked:
            raise HTTPException(status_code=409, detail="工单仍在执行，请稍后重试")
        # Manual retry deliberately starts a fresh graph; automatic retries resume checkpoints.
        import asyncio

        def clear_checkpoint():
            from langgraph.checkpoint.postgres import PostgresSaver

            from app.tasks.worker import _libpq_dsn

            with PostgresSaver.from_conn_string(_libpq_dsn(settings.database_url)) as saver:
                saver.setup()
                saver.delete_thread(str(ticket_id))

        await asyncio.to_thread(clear_checkpoint)
        ticket.status = "processing"
        db.commit()
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job = await redis.enqueue_job("process_ticket", ticket_id, _job_id=f"retry:{ticket_id}:{uuid4().hex}")
        if job is None:
            raise RuntimeError("任务未入队")
        ticket.status = "processing"
        db.commit()
        return {"ticket_id": ticket_id, "task_id": job.job_id}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="任务重试失败") from None
    finally:
        db.close()
        if redis is not None:
            await redis.aclose()
