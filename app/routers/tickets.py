"""工单路由：提交 / 查询 / trace（规格 9）。

POST /tickets：护栏（熔断→503 / 限流→429 / 配额→429）→ 脱敏算缓存键 →
  命中缓存直接返回（不计费），否则入队 arq 异步处理，返回 {ticket_id, task_id}
GET  /tickets/{id}：状态与结果（含各节点耗时聚合展示所需字段）
GET  /tickets/{id}/trace：agent_runs 全链路，供演示
"""
import json

from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AgentRun, Ticket
from app.schemas import AgentRunOut, TicketCreate, TicketOut, TicketTrace
from app.services.guardrails import (
    check_breaker,
    check_quota,
    check_rate_limit,
    get_cache,
)
from app.services.pii import mask_text

settings = get_settings()
router = APIRouter(tags=["tickets"])


def require_token(authorization: str | None = Header(default=None)) -> str:
    # 演示端固定 token（规格 1.2 不做注册登录体系）
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    return settings.demo_token


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _apply_cached(ticket_id: int, data: dict) -> None:
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        if t is None:
            return
        t.masked_text = t.masked_text
        t.draft_reply = data.get("draft_reply")
        t.citations = data.get("citations", [])
        t.risk_level = data.get("risk_level")
        t.action = data.get("action")
        t.lang = data.get("lang")
        t.intent = data.get("intent")
        # 缓存结果视为已结算：按 action 落状态
        t.status = "closed" if data.get("action") == "auto_send" else "awaiting_review"
        db.commit()
    finally:
        db.close()


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

    # 结果缓存命中：直接写回，不调 LLM、不入队、不计费
    cached = get_cache(masked, payload.lang or "unknown")
    if cached:
        _apply_cached(tid, json.loads(cached))
        return {"ticket_id": tid, "task_id": None, "cached": True}

    # 入队异步处理
    try:
        from arq import create_pool

        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job = await redis.enqueue_job("process_ticket", tid)
        task_id = job.job_id if job else None
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
def get_ticket(ticket_id: int):
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
def get_trace(ticket_id: int):
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(AgentRun)
                .where(AgentRun.ticket_id == ticket_id)
                .order_by(AgentRun.id)
            )
            .scalars()
            .all()
        )
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
