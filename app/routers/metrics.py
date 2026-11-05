"""指标聚合接口（规格 10）：导出按 lang / intent 维度的全部指标。

可在线聚合的：自动解决率、拒答率、规则命中率、单均成本、各维度分布。
高风险拦截召回（标注集）与无依据回答率（对抗集）需离线用 datasets/risk_labeled.jsonl、
datasets/adversarial.jsonl 评测（见 locust / eval 说明），不在本接口内——它们依赖
人工标注集，不是运行中聚合量。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func

from app.auth import require_token
from app.config import get_settings
from app.database import SessionLocal
from app.models import AgentRun, Ticket

settings = get_settings()
router = APIRouter(tags=["metrics"])


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
        by_intent = dict(tickets.with_entities(Ticket.intent, func.count()).group_by(Ticket.intent).all())

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


@router.get("/metrics/operations")
def operations(token: str = Depends(require_token)):
    """Live operational snapshot; synthetic evaluation tickets are always excluded."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    now = datetime.now(UTC)
    with SessionLocal() as db:
        live = Ticket.eval_run_id.is_(None)
        counts = dict(db.execute(select(Ticket.status, func.count()).where(live).group_by(Ticket.status)).all())
        stale = (
            db.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(
                    live, Ticket.status.in_(["processing", "retrying"]), Ticket.updated_at < now - timedelta(minutes=20)
                )
            )
            or 0
        )
        oldest = db.scalar(
            select(func.min(Ticket.created_at)).where(live, Ticket.status.in_(["processing", "retrying"]))
        )
        attempts = (
            select(
                AgentRun.ticket_id,
                func.sum(AgentRun.latency_ms).label("ms"),
            )
            .join(Ticket, Ticket.id == AgentRun.ticket_id)
            .where(live)
            .group_by(AgentRun.ticket_id)
            .subquery()
        )
        latency = db.execute(
            select(
                func.count(),
                func.percentile_cont(0.5).within_group(attempts.c.ms),
                func.percentile_cont(0.95).within_group(attempts.c.ms),
            ).select_from(attempts)
        ).one()
        cost = (
            db.scalar(
                select(func.coalesce(func.sum(AgentRun.cost_usd), 0))
                .join(Ticket, Ticket.id == AgentRun.ticket_id)
                .where(live)
            )
            or 0
        )
        measured = (
            db.scalar(
                select(func.count(func.distinct(AgentRun.ticket_id)))
                .join(Ticket, Ticket.id == AgentRun.ticket_id)
                .where(live, AgentRun.cost_usd.is_not(None))
            )
            or 0
        )
        alerts = []
        if stale:
            alerts.append(
                {"code": "stale_processing", "count": stale, "message": "存在长时间未完成工单，请检查 worker"}
            )
        if counts.get("failed", 0):
            alerts.append(
                {"code": "failed_tickets", "count": counts["failed"], "message": "存在失败工单，可检查记录后重试"}
            )
        if counts.get("deferred", 0):
            alerts.append(
                {"code": "deferred_tickets", "count": counts["deferred"], "message": "存在预算延期工单，请核对预算"}
            )
        return {
            "generated_at": now,
            "by_status": counts,
            "stale_processing": stale,
            "oldest_processing_age_seconds": max(0, (now - oldest).total_seconds()) if oldest else None,
            "agent_processing_ms": {"sample_count": latency[0], "p50": latency[1], "p95": latency[2]},
            "estimated_cost_usd": float(cost),
            "cost_sample_count": measured,
            "avg_estimated_cost_usd": float(cost) / measured if measured else None,
            "alerts": alerts,
        }
