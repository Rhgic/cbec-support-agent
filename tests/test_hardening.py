"""Behavioral regressions for operator access, review transitions and PII boundaries."""

import asyncio
import base64
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.graph.nodes import generate, tools
from app.routers import review, tickets
from app.schemas import ReviewCreate, TicketCreate
from app.services import pii
from app.services.llm import LlmResult
from app.tasks import worker


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tickets.router)
    app.include_router(review.router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/tickets/1", "/tickets/1/trace", "/review/queue"])
def test_read_apis_require_auth(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401


@pytest.mark.parametrize("text", ["", "   ", "x" * 20001])
def test_invalid_ticket_input(text):
    with pytest.raises(ValidationError):
        TicketCreate(text=text)


@pytest.mark.parametrize("reply", [None, "", "  "])
def test_edit_requires_reply(reply):
    with pytest.raises(ValidationError):
        ReviewCreate(action="edited", final_reply=reply)


class Session:
    def __init__(self, ticket):
        self.ticket = ticket
        self.added = []
        self.committed = False

    def execute(self, statement, *args):
        self.statement = statement
        return SimpleNamespace(scalar_one_or_none=lambda: self.ticket)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.mark.parametrize("status", ["processing", "failed", "ready_to_send"])
def test_review_rejects_nonpending(client, monkeypatch, status):
    db = Session(SimpleNamespace(status=status, draft_reply="draft"))
    monkeypatch.setattr(review, "SessionLocal", lambda: db)
    response = client.post("/review/1", headers={"Authorization": "Bearer dev-token"}, json={"action": "approved"})
    assert response.status_code == 409
    assert not db.added


@pytest.mark.parametrize("action,reply,expected", [("approved", None, "draft"), ("edited", "fixed", "fixed")])
def test_review_saves_final_reply(client, monkeypatch, action, reply, expected):
    ticket = SimpleNamespace(status="awaiting_review", draft_reply="draft", risk_level="high")
    db = Session(ticket)
    monkeypatch.setattr(review, "SessionLocal", lambda: db)
    response = client.post(
        "/review/1", headers={"Authorization": "Bearer dev-token"}, json={"action": action, "final_reply": reply}
    )
    assert response.status_code == 200
    assert ticket.status == "ready_to_send"
    assert ticket.draft_reply == db.added[0].final_reply == expected
    assert db.statement._for_update_arg is not None


def test_generation_does_not_receive_tracking_number(monkeypatch):
    seen = {}

    def prompt(customer, retrieved, tool_info, lang):
        seen["tools"] = tool_info
        return {"system": "", "user": tool_info}

    monkeypatch.setattr(generate, "generate_prompt", prompt)
    monkeypatch.setattr(generate, "chat_json", lambda *a: LlmResult({}, 0, 0, 0, "offline"))
    generate.generate({"tool_results": {"order": {"status": "shipped", "tracking_no": "SECRET123456"}}})
    assert "SECRET123456" not in seen["tools"]
    assert "shipped" in seen["tools"]


def test_tools_restore_vault_mapping(monkeypatch):
    monkeypatch.setattr(tools, "load_mapping", lambda tid: {"[TRACKING_1]": "CBEC123456"})
    assert tools._restore({"ticket_id": 1, "masked_text": "order [TRACKING_1]"}) == "order CBEC123456"


def test_encryption_randomized_and_legacy_readable():
    first, second = pii._encrypt("sensitive"), pii._encrypt("sensitive")
    assert first.startswith("v1:") and first != second
    assert pii._decrypt(first) == pii._decrypt(second) == "sensitive"
    body = pii._xor(b"legacy", pii._key())
    legacy = base64.b64encode(hmac.new(pii._key(), body, hashlib.sha256).digest() + body).decode()
    assert pii._decrypt(legacy) == "legacy"


@pytest.mark.parametrize("env", ["prod", "production"])
def test_prod_rejects_default_secrets(env):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env=env, demo_token="dev-token", pii_secret="change-me-in-prod")
    Settings(_env_file=None, app_env=env, demo_token="t" * 32, pii_secret="p" * 32)


def test_async_worker_uses_thread(monkeypatch):
    import threading

    current = threading.get_ident()
    monkeypatch.setattr(worker, "_locked_process", lambda ctx, tid: threading.get_ident())
    assert asyncio.run(worker.process_ticket({}, 1)) != current


def test_checkpoint_resume_does_not_repeat_completed_nodes():
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, StateGraph

    class State(TypedDict, total=False):
        ticket_id: int
        first_done: bool
        second_done: bool

    calls = {"first": 0, "second": 0}

    def first(state):
        calls["first"] += 1
        return {"first_done": True}

    def second(state):
        calls["second"] += 1
        if calls["second"] == 1:
            raise RuntimeError("temporary outage")
        return {"second_done": True}

    builder = StateGraph(State)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.set_entry_point("first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "resume-test"}}
    with pytest.raises(RuntimeError):
        worker._resume_graph(graph, {"ticket_id": 1}, config)
    result = worker._resume_graph(graph, {"ticket_id": 1}, config)
    assert result["second_done"] is True
    assert calls == {"first": 1, "second": 2}
    assert worker._resume_graph(graph, {"ticket_id": 1}, config) == result
    assert calls == {"first": 1, "second": 2}


@pytest.mark.parametrize("attempt,expected", [(1, "retrying"), (2, "failed")])
def test_worker_failure_status(monkeypatch, attempt, expected):
    ticket = SimpleNamespace(status="processing")

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return SimpleNamespace(scalar=lambda: True)

        def get(self, *args):
            return ticket

        def commit(self):
            pass

    monkeypatch.setattr(worker, "SessionLocal", DB)
    monkeypatch.setattr(worker, "_process_ticket", lambda *args: (_ for _ in ()).throw(RuntimeError("outage")))
    with pytest.raises(RuntimeError):
        worker._locked_process({"job_try": attempt}, 1)
    assert ticket.status == expected


def test_duplicate_worker_is_skipped(monkeypatch):
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return SimpleNamespace(scalar=lambda: False)

    monkeypatch.setattr(worker, "SessionLocal", DB)
    monkeypatch.setattr(worker, "_process_ticket", lambda *args: pytest.fail("duplicate processing"))
    assert worker._locked_process({}, 1)["skipped"] == "already_running"


def test_risk_gate_unexpected_exception_blocks(monkeypatch):
    from app.graph.nodes import risk_gate

    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda state: 1 / 0)
    assert risk_gate.risk_gate({})["action"] == "human_required"


def test_arq_retry_is_explicit(monkeypatch):
    from arq import Retry

    monkeypatch.setattr(worker, "_locked_process", lambda *args: (_ for _ in ()).throw(RuntimeError("outage")))
    with pytest.raises(Retry):
        asyncio.run(worker.process_ticket({"job_try": 1}, 1))
    with pytest.raises(RuntimeError):
        asyncio.run(worker.process_ticket({"job_try": 2}, 1))
