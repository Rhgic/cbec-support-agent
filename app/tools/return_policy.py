"""退货期计算器：纯计算，不调 LLM（规格 7.3）。

基于 delivered_at + 政策天数。明确处理边界：未签收（delivered_at 为空）、跨时区
（统一按 UTC 比较）。返回 {eligible, days_left, reason} 供回复生成引用。
"""
from datetime import UTC, datetime


def _utc(dt: datetime) -> datetime:
    # 跨时区：无时区信息的入库时间按 UTC 处理，避免本地时区导致天数偏差
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def evaluate(order, policy_days: int = 30) -> dict:
    """评估某订单是否在退货期内。

    order：ORM Order（至少需要 delivered_at 字段）。
    边界：未签收 → 不计时，直接判不可退并说明原因。
    """
    if order.delivered_at is None:
        # 未签收：退货期从签收日起算，故此时无法判定为可退
        return {
            "eligible": False,
            "days_left": 0,
            "reason": "订单尚未签收，退货期自签收日起算，暂不能退货",
        }

    delivered = _utc(order.delivered_at)
    now = datetime.now(UTC)
    days_since = (now - delivered).days
    days_left = policy_days - days_since
    eligible = days_left >= 0
    reason = (
        f"在 {policy_days} 天退货期内，剩余 {days_left} 天"
        if eligible
        else f"已超过 {policy_days} 天退货期 {abs(days_left)} 天"
    )
    return {"eligible": eligible, "days_left": days_left, "reason": reason}
