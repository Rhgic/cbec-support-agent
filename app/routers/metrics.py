"""指标聚合接口（规格 10）：导出按 lang / intent 维度的全部指标。

可在线聚合的：自动解决率、拒答率、规则命中率、单均成本、各维度分布。
高风险拦截召回（标注集）与无依据回答率（对抗集）需离线用 datasets/risk_labeled.jsonl、
datasets/adversarial.jsonl 评测（见 locust / eval 说明），不在本接口内——它们依赖
人工标注集，不是运行中聚合量。
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func

from app.config import get_settings
from app.database import SessionLocal
from app.models import AgentRun, Ticket

settings = get_settings()
router = APIRouter(tags=["metrics"])


def require_token(authorization: str | None = Header(default=None)) -> str:
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    return settings.demo_token


@router.get("/metrics/summary")
def summary(
    token: str = Depends(require_token),
    eval_run_id: str | None = Query(default=None),
):
    db = SessionLocal()
    try:
        # 演示控制台默认展示最近一次评测批次，避免把历史评测和人工演示工单混成
        # 一个无意义分母；显式传 eval_run_id 时可复核任意批次。
        selected_run_id = eval_run_id
        if selected_run_id is None:
            selected_run_id = (
                db.query(Ticket.eval_run_id)
                .filter(Ticket.eval_run_id.is_not(None))
                .order_by(Ticket.created_at.desc())
                .limit(1)
                .scalar()
            )

        tickets = db.query(Ticket)
        runs = db.query(AgentRun).join(Ticket, AgentRun.ticket_id == Ticket.id)
        if selected_run_id:
            tickets = tickets.filter(Ticket.eval_run_id == selected_run_id)
            runs = runs.filter(Ticket.eval_run_id == selected_run_id)

        total = tickets.count()
        if total == 0:
            return {
                "total": 0,
                "eval_run_id": selected_run_id,
                "auto_solve_rate": 0.0,
                "reject_rate": 0.0,
                "rule_hit_rate": 0.0,
                "total_cost_usd": 0.0,
                "avg_cost_usd": 0.0,
                "by_lang": {},
                "by_intent": {},
            }

        auto = tickets.filter(Ticket.action == "auto_send").count()
        short = tickets.filter(Ticket.short_circuited.is_(True)).count()
        rule = tickets.filter(Ticket.intent_method == "rule").count()
        cost = runs.with_entities(func.coalesce(func.sum(AgentRun.cost_usd), 0.0)).scalar() or 0.0

        by_lang = dict(tickets.with_entities(Ticket.lang, func.count()).group_by(Ticket.lang).all())
        by_intent = dict(
            tickets.with_entities(Ticket.intent, func.count()).group_by(Ticket.intent).all()
        )

        return {
            "total": total,
            "eval_run_id": selected_run_id,
            "auto_solve_rate": auto / total,
            "reject_rate": short / total,
            "rule_hit_rate": rule / total,
            "total_cost_usd": float(cost),
            "avg_cost_usd": float(cost) / total,
            "by_lang": by_lang,
            "by_intent": by_intent,
        }
    finally:
        db.close()
