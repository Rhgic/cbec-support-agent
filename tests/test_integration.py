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
