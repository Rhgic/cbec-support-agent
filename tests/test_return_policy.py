"""退货期计算器单测（规格 7.3，纯函数）。

不依赖 LLM、不依赖 DB：evaluate 只吃一个带 delivered_at 的对象。
"""
from datetime import UTC, datetime, timedelta

from app.tools.return_policy import evaluate


class _Order:
    """最少的 duck-typed Order：只要有 delivered_at 即可。"""

    def __init__(self, delivered_at):
        self.delivered_at = delivered_at


def _utc_days_ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def test_eligible_within_window():
    # 10 天前签收，政策 30 天 → 可退，剩余约 20 天
    order = _Order(_utc_days_ago(10))
    r = evaluate(order, policy_days=30)
    assert r["eligible"] is True
    assert r["days_left"] == 20
    assert "退货期" in r["reason"]


def test_not_eligible_past_window():
    # 40 天前签收，政策 30 天 → 不可退
    order = _Order(_utc_days_ago(40))
    r = evaluate(order, policy_days=30)
    assert r["eligible"] is False
    assert r["days_left"] == -10
    assert "已超过" in r["reason"]


def test_boundary_zero_day():
    # 恰好等于政策天数（按天取整）→ days_left=0 视为可退
    order = _Order(_utc_days_ago(30))
    r = evaluate(order, policy_days=30)
    assert r["eligible"] is True
    assert r["days_left"] == 0


def test_not_delivered_is_ineligible():
    # 未签收：无法起算退货期，直接判不可退并说明
    r = evaluate(_Order(None), policy_days=30)
    assert r["eligible"] is False
    assert r["days_left"] == 0
    assert "尚未签收" in r["reason"]


def test_custom_policy_days():
    # 政策改成 7 天，签收 5 天前 → 可退剩 2 天
    r = evaluate(_Order(_utc_days_ago(5)), policy_days=7)
    assert r["eligible"] is True
    assert r["days_left"] == 2


def test_naive_datetime_treated_as_utc():
    # 朴素时间（无时区）按 UTC 处理，避免本地时区偏差
    naive = datetime.utcnow() - timedelta(days=10)  # noqa: DTZ003 — 故意朴素
    r = evaluate(_Order(naive), policy_days=30)
    assert r["eligible"] is True
