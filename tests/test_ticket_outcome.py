from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.ticket_outcome import apply_ticket_result


def _ticket():
    return SimpleNamespace(
        masked_text=None,
        lang=None,
        intent=None,
        intent_confidence=None,
        intent_method=None,
        retrieval_score=None,
        short_circuited=None,
        draft_reply=None,
        citations=None,
        risk_level=None,
        action=None,
        status="processing",
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"action": "auto_send", "risk_level": "low", "draft_reply": "ok"}, "closed"),
        (
            {"action": "human_required", "risk_level": "mid", "draft_reply": "review"},
            "awaiting_review",
        ),
        ({"action": "human_required", "risk_level": "high"}, "failed"),
        ({"fatal_error": "boom"}, "failed"),
        ({"action": None, "risk_level": "mid", "draft_reply": "review"}, "awaiting_review"),
        # 以下三条是编排"刻意跳过生成"的全部入口（镜像 builder.py 的路由判定），
        # 都属主动拒答而非故障。把它们并入 failed 会让 /metrics 显示成两位数失败率，
        # 而本系统的卖点恰恰就是"无依据不出站"。
        (
            {"action": "human_required", "risk_level": "high", "short_circuited": True},
            "refused",
        ),
        (
            {"action": "human_required", "risk_level": "high", "intent": "other"},
            "refused",
        ),
        (
            {
                "action": "human_required",
                "risk_level": "high",
                "intent": "logistics",
                "intent_confidence": 0.42,
            },
            "refused",
        ),
        # 置信度足够却仍然没有草稿 → 生成环节真的出了问题，仍记 failed
        (
            {
                "action": "human_required",
                "risk_level": "high",
                "intent": "logistics",
                "intent_confidence": 0.95,
            },
            "failed",
        ),
        # 脱敏失败仍是真故障，即使同时短路也记 failed——合规红线不能被降级成"拒答"。
        (
            {"fatal_error": "pii_vault 写入失败", "short_circuited": True},
            "failed",
        ),
        # 短路但仍产出了草稿（兜底模板）→ 有东西可审，走人工复核而非拒答
        (
            {
                "action": "human_required",
                "risk_level": "high",
                "short_circuited": True,
                "draft_reply": "兜底模板",
            },
            "awaiting_review",
        ),
    ],
)
def test_apply_ticket_result_status(result, expected):
    ticket = _ticket()
    db = MagicMock()
    db.get.return_value = ticket

    apply_ticket_result(db, 42, result)

    assert ticket.status == expected


def test_apply_ticket_result_writes_pipeline_fields():
    ticket = _ticket()
    db = MagicMock()
    db.get.return_value = ticket
    result = {
        "masked_text": "masked",
        "lang": "es",
        "intent": "return",
        "intent_confidence": 0.9,
        "intent_method": "rule",
        "retrieval_score": 0.8,
        "short_circuited": False,
        "draft_reply": "reply",
        "citations": ["file://policy.md"],
        "risk_level": "low",
        "action": "auto_send",
    }

    apply_ticket_result(db, 42, result)

    for field, value in result.items():
        assert getattr(ticket, field) == value


def test_apply_ticket_result_rejects_missing_ticket():
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(LookupError, match="ticket not found"):
        apply_ticket_result(db, 404, {})
