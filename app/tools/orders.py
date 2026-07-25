"""mock 订单查询：纯本地查询，不调外部 API。

为什么离线：订单主体是 mock（规格 1.2 / 15.2），仅运单号真实；查询只是本地库读取。
找不到返回 None，由调用方（tools 节点）处理兜底。
"""
from app.database import SessionLocal
from app.models import Order


def get_by_no(order_no: str) -> Order | None:
    """按订单号查订单；不存在返回 None。"""
    db = SessionLocal()
    try:
        return db.query(Order).filter(Order.order_no == order_no).first()
    finally:
        db.close()
