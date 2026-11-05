"""Opt-in tests against isolated PostgreSQL/Redis services provisioned by CI."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(os.getenv("CBEC_INTEGRATION") != "1", reason="requires isolated PG/Redis services")


def test_concurrent_reviews_only_one_succeeds():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import SessionLocal
    from app.models import Ticket
    from app.routers.review import router

    with SessionLocal() as db:
        ticket = Ticket(raw_text="integration", status="awaiting_review", draft_reply="draft", risk_level="high")
        db.add(ticket)
        db.commit()
        tid = ticket.id
    app = FastAPI()
    app.include_router(router)

    def approve():
        with TestClient(app) as client:
            return client.post(
                f"/review/{tid}", json={"action": "approved"}, headers={"Authorization": "Bearer dev-token"}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: approve(), range(2)))
    assert sorted(statuses) == [200, 409]


def test_vault_reexecution_is_idempotent():
    from app.database import SessionLocal
    from app.models import Ticket
    from app.services.pii import load_mapping, store_mapping

    with SessionLocal() as db:
        ticket = Ticket(raw_text="integration", status="processing")
        db.add(ticket)
        db.commit()
        tid = ticket.id
    mapping = {"customer@example.com": "[EMAIL_1]"}
    store_mapping(tid, mapping)
    store_mapping(tid, mapping)
    assert load_mapping(tid) == {"[EMAIL_1]": "customer@example.com"}


def test_redis_duplicate_enqueue_is_rejected():
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.config import get_settings

    async def run():
        redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            job_id = f"integration:{uuid4().hex}"
            assert await redis.enqueue_job("process_ticket", 1, _job_id=job_id) is not None
            assert await redis.enqueue_job("process_ticket", 1, _job_id=job_id) is None
        finally:
            await redis.aclose()

    asyncio.run(run())


def test_postgres_checkpoint_roundtrip():
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, StateGraph

    from app.config import get_settings
    from app.graph.state import TicketState
    from app.tasks.worker import _libpq_dsn, _resume_graph

    calls = []
    builder = StateGraph(TicketState)

    def node(state):
        calls.append(1)
        return {"masked_text": "safe"}

    builder.add_node("safe", node)
    builder.set_entry_point("safe")
    builder.add_edge("safe", END)
    with PostgresSaver.from_conn_string(_libpq_dsn(get_settings().database_url)) as saver:
        saver.setup()
        graph = builder.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": f"integration:{uuid4().hex}"}}
        assert _resume_graph(graph, {"ticket_id": 1}, config)["masked_text"] == "safe"
        assert _resume_graph(graph, {"ticket_id": 1}, config)["masked_text"] == "safe"
        assert calls == [1]


def test_live_queue_and_operations_exclude_evaluation_data():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import SessionLocal
    from app.models import AgentRun, Ticket
    from app.routers import metrics, tickets

    with SessionLocal() as db:
        online = Ticket(raw_text="private@example.com", masked_text="[EMAIL_1]", status="failed")
        synthetic = Ticket(raw_text="synthetic", status="failed", eval_run_id=str(uuid4()))
        db.add_all([online, synthetic])
        db.flush()
        tid = online.id
        db.add_all(
            [
                AgentRun(ticket_id=tid, node="classify", latency_ms=10, ok=True, cost_usd=0.1),
                AgentRun(ticket_id=tid, node="generate", latency_ms=30, ok=True, cost_usd=0.1),
                AgentRun(ticket_id=synthetic.id, node="generate", latency_ms=999999, ok=True, cost_usd=100),
            ]
        )
        db.commit()
    app = FastAPI()
    app.include_router(tickets.router)
    app.include_router(metrics.router)
    headers = {"Authorization": "Bearer dev-token"}
    with TestClient(app) as client:
        response = client.get(f"/tickets?status=failed&after_id={tid - 1}&limit=1", headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert [t["ticket_id"] for t in page["items"]] == [tid]
        assert page["items"][0]["customer_message"] == "[EMAIL_1]"
        assert "private@example.com" not in response.text
        assert page["next_cursor"] is None
        assert client.get("/tickets?status=invalid", headers=headers).status_code == 422
        result = client.get("/metrics/operations", headers=headers)
        assert result.status_code == 200
        snapshot = result.json()
        assert snapshot["estimated_cost_usd"] == pytest.approx(0.2)
        assert snapshot["agent_processing_ms"]["p50"] == 40
        assert any(alert["code"] == "failed_tickets" for alert in snapshot["alerts"])
