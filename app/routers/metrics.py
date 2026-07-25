"""指标聚合接口（规格 10）：导出按 lang / intent 维度的全部指标。

可在线聚合的：自动解决率、拒答率、规则命中率、单均成本、各维度分布。
高风险拦截召回（标注集）与无依据回答率（对抗集）需离线用 datasets/risk_labeled.jsonl、
datasets/adversarial.jsonl 评测（见 locust / eval 说明），不在本接口内——它们依赖
人工标注集，不是运行中聚合量。
"""
from fastapi import APIRouter, Depends, Header, HTTPException
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
def summary(token: str = Depends(require_token)):
    db = SessionLocal()
    try:
        total = db.query(Ticket).count()
        if total == 0:
            return {
                "total": 0,
                "auto_solve_rate": 0.0,
                "reject_rate": 0.0,
                "rule_hit_rate": 0.0,
                "total_cost_usd": 0.0,
                "avg_cost_usd": 0.0,
                "by_lang": {},
                "by_intent": {},
            }

        auto = db.query(Ticket).filter(Ticket.action == "auto_send").count()
        short = db.query(Ticket).filter(Ticket.short_circuited.is_(True)).count()
        rule = db.query(Ticket).filter(Ticket.intent_method == "rule").count()
        cost = db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0.0)).scalar() or 0.0

        by_lang = dict(db.query(Ticket.lang, func.count()).group_by(Ticket.lang).all())
        by_intent = dict(db.query(Ticket.intent, func.count()).group_by(Ticket.intent).all())

        return {
            "total": total,
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
