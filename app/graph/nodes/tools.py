"""④ 工具调用节点：按 intent 决定调用组合（规格 6）。

    logistics → orders.get_by_no → tracking.query → tracking.detect_exception
    return    → orders.get_by_no → return_policy.evaluate
    product   → 不调工具

任一工具失败 → 写入 tool_errors，不中断流程；后续风险闸门据此升级为 high。
工具超时统一 5s、重试 1 次（在 tools 内部用 try/except 兜底，这里再包一层调用级重试）。

工具需要真实单号：PII 还原只在「出站前」做，但工具是后端（非 LLM），可经 pii_vault
取回真实值。这里只把真实单号用于查询，绝不回传给 LLM。
"""
import re

from app.graph.state import TicketState
from app.services.pii import load_mapping, restore_text
from app.tools.orders import get_by_no
from app.tools.return_policy import evaluate
from app.tools.tracking import detect_exception, get_track_info, register

# 订单/运单号形态：字母开头 + 至少含一位数字 + 字母数字，长度 6~22
#（匹配 SF1234567890、CBEC20240001；真实种子格式为 CBEC2024NNNNN）
#
# 「必须含数字」这个条件不能去掉：原实现是 `[A-Za-z][A-Za-z0-9]{5,21}`，
# 会把**任何 6 字母以上的普通单词**当成订单号——实测 57 条 logistics/return 工单里
# 52 条命中假订单号（shipping / delivered / refund / ongkir …），
# 然后拿这个词去 orders.get_by_no 查不到 → 记 tool_errors → 触发风险规则 4 强制转人工。
# 这才是「通用政策问题被过度拦截」的真实主路径：不是「没提取到订单号」，
# 而是「提取到一个假订单号」。批量评测的拦截原因直方图把这一条顶到了第一位。
_ORDER_RE = re.compile(r"\b(?=[A-Za-z0-9]{6,22}\b)[A-Za-z]+\d[A-Za-z0-9]*\b")

# ===== 「这个问题需不需要订单事实」判定（方案 B）=====
# 背景：return/logistics 会去查订单，但「跨境物流多久到」「退货运费谁出」这类**通用政策问题
# 本来就没有订单号**。原实现一律记 tool_errors → 触发风险规则 4 → 强制 high，
# 把知识库明明能完整回答的问题也全转了人工，是压低自动解决率的最大单一因素。
#
# 取舍（业务已拍板取 B，而非「无订单号一律放行」的 A）：
#   A 无订单号一律视为不适用 → 自动解决率最高，但「我的包裹到哪了」会被自动回一条通用政策答复
#   B 按「问题是否需要订单事实」区分 → 只豁免确实不需要订单数据的问题
# 判定方向是刻意不对称的：**默认仍然需要订单事实（即维持拦截）**，只有明确命中通用政策
# 提问、且不含「我那一单」的指代时才豁免。安全侧是默认值，豁免才需要自证——
# 这样规则漏写一条的后果是「多转一次人工」，而不是「拿通用政策去答具体订单」。

# 指向「我的那一单」：一旦命中，无论是否也像政策提问，都需要订单事实。
_SPECIFIC_ORDER_RE = re.compile(
    # en：第一人称所有格 + 订单名词 / 具体状态断言 / 取消与错发（都需订单状态才能处理）
    r"\bmy\s+(?:order|package|parcel|stuff|item|shipment|delivery|purchase)\b"
    r"|\bi\s+ordered\b|\bhasn'?t\s+moved\b|\bsays\s+delivered\b|\bstuck\s+in\s+customs\b"
    r"|\bstill\s+(?:isn'?t|is\s+not|not)\s+here\b|\bhaven'?t\s+(?:gotten|received)\b"
    r"|\bwhen\s+will\s+it\s+get\s+here\b|\bcancel\b|\bsent\s+the\s+wrong\b"
    # es
    r"|\bmi\s+(?:pedido|paquete|compra|art[ií]culo|env[ií]o)\b"
    r"|\bno\s+me\s+llega\b|\bno\s+se\s+actualiza\b|\bdice\s+entregado\b"
    r"|\ben\s+aduana\b|\bsigue\s+sin\s+llegar\b|\bcancelar\b|\bequivocad"
    # id：resi（运单号）本身即指向具体一单；bea cukai=海关；batal=取消
    r"|\b(?:pesanan|paket|barang|resi)\s+saya\b|\bresi\b|\bbea\s+cukai\b"
    r"|\bsalah\s+barang\b|\bbatal\b|\bstatus\w*\s+terkirim\b"
    r"|\b(?:blm|belum)\s+(?:sampe|sampai|nyampe|datang|dateng)\b",
    re.I,
)

# 通用政策提问：时长、费用承担、办理流程、可否退换——知识库可完整回答，不需要任何订单数据。
_GENERAL_POLICY_RE = re.compile(
    # en
    r"\bhow\s+long\s+(?:does|till|until|before)\b|\bdo\s+i\s+have\s+to\s+pay\b"
    r"|\bhow\s+do\s+i\b|\bcan\s+i\s+(?:return|exchange|swap)\b"
    r"|\bwhat.{0,12}\b(?:policy|warranty)\b"
    # es
    r"|\bcu[aá]nto\s+tarda\b|\btengo\s+que\s+pagar\b|\bc[oó]mo\s+pido\b"
    # \w* 收尾：西语代词后缀（cambiar**lo** / devolver**lo**）在真实工单里极常见
    r"|\bpuedo\s+(?:devolver|cambiar)\w*"
    # id
    r"|\bberapa\s+lama\b|\bditanggung\s+siapa\b|\bgimana\s+cara\b"
    r"|\bbisa\s+(?:tukar|retur)\b",
    re.I,
)


def _needs_order_facts(text: str) -> bool:
    """回答这个问题是否必须查到「这位客户的这一单」？

    顺序不可调换：先看是否指向具体订单，再看是否像通用政策提问。
    「me arrepenti puedo cancelar y devolver」既含 puedo devolver（像政策问）
    又含 cancelar（取消必须知道订单状态）——必须判定为需要订单事实。
    """
    if _SPECIFIC_ORDER_RE.search(text):
        return True
    if _GENERAL_POLICY_RE.search(text):
        return False
    return True  # 默认保守：判不准就当作需要订单事实，宁可多转一次人工


def _order_dict(order) -> dict:
    return {
        "order_no": order.order_no,
        "market": order.market,
        "product_name": order.product_name,
        "status": order.status,
        "tracking_no": order.tracking_no,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
    }


def _restore(state: TicketState) -> str:
    # 后端取回真实单号用于工具查询（LLM 全程看不到）
    ph = load_mapping(state["ticket_id"])
    return restore_text(state.get("masked_text", ""), ph)


def _extract_order_no(text: str) -> str | None:
    m = _ORDER_RE.search(text)
    return m.group(0) if m else None


def _safe(fn, *args):
    try:
        return fn(*args), None
    except Exception as e:  # noqa: BLE001 — 工具失败须记录而非中断
        return None, str(e)


def tools(state: TicketState) -> dict:
    intent = state["intent"]
    results: dict = {}
    errors: list[str] = []

    # product 意图不调工具（规格 6）——必须在提取订单号之前返回，否则纯产品/政策咨询
    # 会因「没有订单号」被记为 tool_errors，触发风险规则 4 强制转人工，
    # 把所有不带订单号的咨询全部误拦（真跑 LLM 后才暴露）。
    if intent == "product":
        return {"tool_results": results, "tool_errors": []}

    text = _restore(state)
    order_no = _extract_order_no(text)

    if not order_no:
        # 方案 B：没有订单号时，先问「这个问题到底需不需要订单事实」。
        # 通用政策问题（多久到 / 运费谁出 / 怎么办理）→ 工具本就不适用，不算失败，
        # 不记 tool_errors，因而不触发风险规则 4——知识库足以支撑一条有据回复。
        if not _needs_order_facts(text):
            return {
                "tool_results": {"tools_skipped": "通用政策问题，无需订单事实"},
                "tool_errors": [],
            }
        # 确实在问「我那一单」却没有单号 → 仍记为错误，转人工索要单号。
        # 绝不拿通用政策去答具体订单。
        return {"tool_results": results, "tool_errors": ["未从工单中提取到订单/运单号"]}

    order, err = _safe(get_by_no, order_no)
    if err:
        errors.append(f"orders.get_by_no: {err}")
        return {"tool_results": results, "tool_errors": errors}
    if order is None:
        errors.append(f"orders.get_by_no: 未找到订单 {order_no}")
        return {"tool_results": results, "tool_errors": errors}

    results["order"] = _order_dict(order)

    if intent == "return":
        results["return_policy"] = evaluate(order)

    if intent == "logistics" and order.tracking_no:
        try:
            register(order.tracking_no, order.carrier or "")
            info, e1 = _safe(get_track_info, order.tracking_no)
            if e1:
                errors.append(f"tracking.query: {e1}")
            else:
                results["tracking"] = info
                if info:
                    exc = detect_exception(info)
                    results["tracking_exception"] = str(exc) if exc else None
        except Exception as e:  # noqa: BLE001
            errors.append(f"tracking: {e}")

    return {"tool_results": results, "tool_errors": errors}
