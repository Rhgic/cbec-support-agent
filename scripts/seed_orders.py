"""mock 订单生成（规格 scripts/seed_orders.py）。

订单主体为 mock，但运单号必须真实可查（规格 4.3 红线）。本脚本把 TRACKING_NUMBERS
里的真实单号写入 orders 表；**使用者必须把 TRACKING_NUMBERS 替换为自己 17TRACK 账号下
真实可追踪的单号**——脚本无法替你保证真实，请勿用纯随机串冒充。
"""
import argparse
from datetime import UTC, datetime, timedelta

from app.database import SessionLocal
from app.models import Order

# 使用者须替换为真实可查的运单号（carrier 对应 17TRACK 承运商代码）
# 17TRACK 新账号一次性 200 单号额度：注册成功后即可长期追踪，反复查询不扣额度
TRACKING_NUMBERS = [
    ("SF1234567890123", "sf-express"),
    ("YT9876543210001", "yto"),
    ("JD0099887766554", "jd"),
    ("777123456789012", "ups"),
    ("1Z999AA10123456784", "ups"),
]


def seed(n: int = 20) -> int:
    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        count = 0
        for i in range(n):
            tno, carrier = TRACKING_NUMBERS[i % len(TRACKING_NUMBERS)]
            order_no = f"CBEC2024{i:05d}"
            ordered = now - timedelta(days=20 + i)
            shipped = ordered + timedelta(days=2)
            delivered = shipped + timedelta(days=7)
            db.add(
                Order(
                    order_no=order_no,
                    buyer_ref=f"buyer_{i:04d}",
                    market=["US", "MX", "ID"][i % 3],
                    sku=f"SKU-{1000 + i}",
                    product_name=["蓝牙耳机", "手机支架", "加湿器"][i % 3],
                    qty=(i % 3) + 1,
                    amount=(19.9 + i) * (i % 3 + 1),
                    currency=["USD", "MXN", "IDR"][i % 3],
                    ordered_at=ordered,
                    shipped_at=shipped,
                    delivered_at=delivered if i % 4 != 0 else None,  # 部分未签收
                    tracking_no=tno,
                    carrier=carrier,
                    status="delivered" if i % 4 != 0 else "shipped",
                )
            )
            count += 1
        db.commit()
        return count
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    print(f"seeded {seed(args.n)} orders (请确认 TRACKING_NUMBERS 为真实单号)")


if __name__ == "__main__":
    main()
