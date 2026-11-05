"""① PII 脱敏节点。

输入 raw_text → 输出 masked_text + pii_placeholders。失败（vault 写不进去）不得放行原文，
直接置 fatal_error 转人工——脱敏是后续所有处理的安全边界。
"""

from app.database import SessionLocal
from app.graph.state import TicketState
from app.models import Ticket
from app.services.pii import mask_text, store_mapping


def mask(state: TicketState) -> dict:
    raw = state.get("raw_text")
    if raw is None:
        with SessionLocal() as db:
            ticket = db.get(Ticket, state["ticket_id"])
            if ticket is None:
                return {"fatal_error": "工单不存在"}
            raw = ticket.raw_text
    masked, placeholders = mask_text(raw)
    try:
        store_mapping(state["ticket_id"], placeholders)
    except Exception:
        # 脱敏失败不放行原文：置 fatal_error，下游风险闸门会据此升级为 high
        return {"fatal_error": "pii_vault 写入失败，原文未脱敏，转人工"}
    return {"masked_text": masked}
