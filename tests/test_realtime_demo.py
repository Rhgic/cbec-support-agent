"""实时接入演示的协议测试。

不启动 PG / Milvus / LLM；替换真实 pipeline 后验证 Webhook 的用户会话聚合与
处理结果返回。这样平台适配层的回归不会依赖外部服务。
"""
from fastapi.testclient import TestClient

from scripts import demo_server


def _result(ticket_id: int) -> dict:
    return {
        "ticket": {
            "ticket_id": ticket_id,
            "customer_message": "[ORDER_1] tracking has not updated",
            "intent": "logistics",
            "risk_level": "low",
            "action": "auto_send",
            "conversation": {"user_id": "customer_001", "history_count": 0, "recent_messages": []},
        },
        "trace": {"ticket_id": ticket_id, "runs": []},
    }


def test_webhook_aggregates_a_masked_user_conversation(monkeypatch):
    demo_server.RESULTS.clear()
    demo_server.CONVERSATIONS.clear()
    ticket_id = 901

    def fake_run(raw_text: str, user_id: str | None = None) -> int:
        assert raw_text == "Where is my order?"
        assert user_id == "customer_001"
        demo_server.RESULTS[ticket_id] = _result(ticket_id)
        return ticket_id

    monkeypatch.setattr(demo_server, "_run_pipeline", fake_run)
    client = TestClient(demo_server.app)
    response = client.post(
        "/webhook/messages",
        headers={"Authorization": f"Bearer {demo_server.settings.demo_token}"},
        json={"user_id": "customer_001", "channel": "shopify", "message": "Where is my order?"},
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "ticket_id": 901, "risk_level": "low", "action": "auto_send"}
    assert demo_server.CONVERSATIONS["customer_001"] == [
        {
            "ticket_id": 901,
            "message": "[ORDER_1] tracking has not updated",
            "intent": "logistics",
            "channel": "shopify",
        }
    ]


def test_history_limits_context_to_six_masked_messages():
    history = [{"message": f"[ORDER_{index}] message"} for index in range(8)]
    context = demo_server._format_history(history)

    assert "[ORDER_0]" not in context
    assert "[ORDER_1]" not in context
    assert "[ORDER_2]" in context
    assert "[ORDER_7]" in context
