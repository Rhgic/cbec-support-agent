"""工单编排结果的统一持久化逻辑。

worker 与端到端评测都走这里，避免两条入口对状态判定产生漂移。
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.models import Ticket


def _declined_by_design(ticket: Ticket) -> bool:
    """工单没有草稿，是因为编排**刻意**跳过了生成，而不是生成失败？

    三个条件逐一镜像 graph/builder.py 的路由判定——那里决定"不进生成"，
    这里就得认出同一批工单，两处必须同步改：
      _route_after_classify  intent == "other" 或 置信度 < 0.7  → 直奔风险闸门
      _route_after_retrieve  short_circuited                    → 直奔风险闸门

    区分"拒答"与"失败"不是措辞洁癖：本系统的核心主张是「无依据不出站」，
    把主动拒答记成 failed，等于把最想证明的能力标成故障。
    实测库里 44 条 failed 里有 42 条属于这一类（19 条短路 + 23 条非业务意图），
    /metrics 会因此显示成两位数的"失败率"，演示时还得花时间解释那其实是正确行为。
    """
    if ticket.short_circuited:
        return True
    if ticket.intent == "other":
        return True
    conf = ticket.intent_confidence
    return conf is not None and conf < 0.7


def apply_ticket_result(db: Session, ticket_id: int, result: Mapping[str, Any]) -> None:
    """把 LangGraph 结果写回工单；调用方负责提交事务。"""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise LookupError(f"ticket not found: {ticket_id}")

    ticket.masked_text = result.get("masked_text")
    ticket.lang = result.get("lang")
    ticket.intent = result.get("intent")
    ticket.intent_confidence = result.get("intent_confidence")
    ticket.intent_method = result.get("intent_method")
    ticket.retrieval_score = result.get("retrieval_score")
    ticket.short_circuited = result.get("short_circuited", False)
    ticket.draft_reply = result.get("draft_reply")
    ticket.citations = result.get("citations")
    ticket.risk_level = result.get("risk_level")
    ticket.action = result.get("action")

    fatal = bool(result.get("fatal_error"))
    if fatal or ticket.risk_level == "high" or ticket.action == "human_required":
        if ticket.draft_reply:
            ticket.status = "awaiting_review"
        elif not fatal and _declined_by_design(ticket):
            ticket.status = "refused"
        else:
            ticket.status = "failed"
    elif ticket.action == "auto_send":
        # 本系统只起草，不实际发送；自动出站结果以 closed 表示流程完成。
        ticket.status = "closed"
    else:
        ticket.status = "awaiting_review"
