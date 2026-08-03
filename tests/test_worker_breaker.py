"""worker 侧的成本熔断（补齐队列缺口）。

背景：熔断此前只在 API 入口检查。批量涌入时这个缺口最致命——
160 条工单入队之后再触发熔断，护栏一分钱也拦不住：新提交被挡住了，
积压的却继续调 LLM 直到排空。这里锁住"消费前也要检查"这条契约。

两个方向都要测：
  熔断触发   → 不进流水线、不花钱、落 deferred（不是 failed，工单没出错）
  熔断读取失败 → 放行（护栏故障只该多花钱，不该把工单卡死；方向与风险闸门刻意相反）
"""
import asyncio
from types import SimpleNamespace

from app.tasks import worker


class _FakeSession:
    """够用的 Session 桩：只实现 worker 熔断分支用到的四个方法。"""

    def __init__(self, ticket):
        self.ticket = ticket
        self.committed = False

    def get(self, _model, _pk):
        return self.ticket

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _patch_session(monkeypatch, ticket):
    session = _FakeSession(ticket)
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    return session


def test_breaker_open_skips_pipeline(monkeypatch):
    """熔断触发：流水线一次都不能被调用，工单落 deferred。"""
    ticket = SimpleNamespace(raw_text="where is my order", status="processing")
    session = _patch_session(monkeypatch, ticket)
    monkeypatch.setattr(worker, "check_breaker", lambda: False)

    # 一旦被调用就说明熔断没拦住——那正是这次要修的缺口
    def _boom():
        raise AssertionError("熔断已触发，不得再构图调用 LLM")

    monkeypatch.setattr(worker, "build_graph", _boom)

    out = asyncio.run(worker.process_ticket({}, 42))

    assert out["deferred"] == "cost_breaker"
    assert ticket.status == "deferred"
    assert session.committed is True


def test_breaker_open_does_not_mark_failed(monkeypatch):
    """deferred ≠ failed：工单本身没有出错，只是预算用完了。

    记成 failed 会让 /metrics 的失败率把"省钱"算成"故障"。
    """
    ticket = SimpleNamespace(raw_text="hola", status="processing")
    _patch_session(monkeypatch, ticket)
    monkeypatch.setattr(worker, "check_breaker", lambda: False)
    monkeypatch.setattr(worker, "build_graph", lambda: None)

    asyncio.run(worker.process_ticket({}, 7))

    assert ticket.status == "deferred"
    assert ticket.status != "failed"


def test_breaker_read_failure_lets_ticket_through(monkeypatch):
    """Redis 故障时 check_breaker 返回 True → 工单必须继续处理。

    降级方向与风险闸门刻意相反（规格 15）：护栏坏了只该多花钱，
    不该把工单卡在队列里。这条一旦写反，Redis 抖动就会让业务停摆。
    """
    ticket = SimpleNamespace(raw_text="where is my order", status="processing")
    _patch_session(monkeypatch, ticket)
    # guardrails 的 _safe 包装在 Redis 异常时返回 True，这里直接模拟其结果
    monkeypatch.setattr(worker, "check_breaker", lambda: True)

    reached = {"pipeline": False}

    def _fake_build_graph():
        reached["pipeline"] = True
        raise RuntimeError("stop-here")  # 走到这里即证明没被熔断挡下

    monkeypatch.setattr(worker, "build_graph", _fake_build_graph)

    try:
        asyncio.run(worker.process_ticket({}, 9))
    except Exception:  # noqa: BLE001 — 只关心是否走到了流水线
        pass

    assert reached["pipeline"] is True
    assert ticket.status != "deferred"
