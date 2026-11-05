"""Regressions for live operator access and cost accounting."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import metrics, review, tickets
from app.services import llm
from app.services.llm_usage import Usage, current_usage


@pytest.mark.parametrize("path", ["/tickets", "/metrics/operations", "/review/1/history"])
def test_operator_endpoints_require_token(path):
    app = FastAPI()
    for router in (metrics.router, review.router, tickets.router):
        app.include_router(router)
    with TestClient(app) as client:
        assert client.get(path).status_code == 401


def test_cost_records_invalid_json_and_retries(monkeypatch, fake_redis):
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))],
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    monkeypatch.setattr(llm, "_client", lambda: client)
    usage = Usage()
    token = current_usage.set(usage)
    try:
        result = llm.chat_json("system", "user", max_retries=1)
    finally:
        current_usage.reset(token)
    assert result.error
    assert usage.token_in == 200
    assert usage.token_out == 100
    assert usage.cost_usd == pytest.approx(2 * (0.1 * llm._PRICE["in"] + 0.05 * llm._PRICE["out"]))


def test_node_trace_records_llm_usage(monkeypatch):
    from app import database
    from app.graph.builder import _traced
    from app.metrics import events
    from app.services.llm_usage import add_usage

    captured = {}

    class DB:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", DB)
    monkeypatch.setattr(events, "record_agent_run", lambda db, **values: captured.update(values))

    def node(state):
        add_usage(100, 25, 0.005)
        return {"draft_reply": "ok"}

    assert _traced("generate", node)({"ticket_id": 1}) == {"draft_reply": "ok"}
    assert (captured["token_in"], captured["token_out"], captured["cost_usd"]) == (100, 25, 0.005)
    assert current_usage.get() is None
