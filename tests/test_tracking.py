"""17TRACK 业务逻辑单测（规格 7.1）。

detect_exception / _normalize / _utc_parse 都是纯函数，不调外部 API。
"""
from datetime import UTC, datetime, timedelta

from app.tools import tracking


def _track(events, delivered=False):
    return {"tracking_no": "TEST1", "delivered": delivered, "events": events}


def test_no_data_raises():
    assert isinstance(tracking.detect_exception(None), RuntimeError)


def test_empty_events_raises():
    assert isinstance(tracking.detect_exception(_track([])), RuntimeError)


def test_normal_no_exception():
    now = datetime.now(UTC).isoformat()
    ev = [{"status": "In transit", "time": now}]
    assert tracking.detect_exception(_track(ev)) is None


def test_timeout_exception():
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    ev = [{"status": "In transit", "time": old}]
    exc = tracking.detect_exception(_track(ev), no_update_days=7)
    assert isinstance(exc, TimeoutError)


def test_customs_stuck_exception():
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    ev = [
        {"status": "In transit", "time": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
        {"status": "Customs clearance", "time": old},
    ]
    exc = tracking.detect_exception(_track(ev), customs_days=14)
    assert isinstance(exc, RuntimeError)
    assert "清关" in str(exc)


def test_return_exception():
    now = datetime.now(UTC).isoformat()
    ev = [{"status": "Return to sender", "time": now}]
    exc = tracking.detect_exception(_track(ev))
    assert isinstance(exc, RuntimeError)
    assert "退回" in str(exc)


def test_delivered_no_timeout():
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    ev = [{"status": "Delivered", "time": old}]
    # 已签收，不判超时
    assert tracking.detect_exception(_track(ev, delivered=True)) is None


def test_normalize_from_17track_v3():
    raw = {
        "data": {
            "tracking": {
                "number": "SF123",
                "events": [
                    {"status": "Delivered", "date": "2026-01-02T10:00:00Z", "location": "SZ"},
                ],
            }
        }
    }
    norm = tracking._normalize(raw)
    assert norm["tracking_no"] == "SF123"
    assert norm["delivered"] is True
    assert norm["events"][0]["status"] == "Delivered"


def test_utc_parse_z_and_naive():
    assert tracking._utc_parse("2026-01-02T10:00:00Z") is not None
    assert tracking._utc_parse("2026-01-02T10:00:00") is not None
    assert tracking._utc_parse("") is None
    assert tracking._utc_parse("not-a-date") is None
