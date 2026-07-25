"""arq worker：异步执行单条工单的 Agent 编排。

为什么用 arq：工单处理含 LLM / 检索 / 工具调用，可能耗时数秒；异步避免阻塞 HTTP 请求。
配合 PG checkpointer：worker 崩溃重启后从最后成功节点恢复，不重复计费（规格第 5 节）。

运行：arq app.tasks.worker.WorkerSettings
"""
import os

# 必须在 torch / pymilvus 被导入前设置：worker 会 fork 子进程执行任务，
# HF tokenizer 的并行线程 + Milvus 的 gRPC 事件循环在 fork 后会导致段错误/进程静默退出
# （日志表现为 "FD from fork parent still in poll list" 与 leaked semaphore）。
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "false")

from arq import func  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver

from app.config import get_settings
from app.database import SessionLocal
from app.graph.builder import build_graph
from app.graph.state import TicketState
from app.metrics.events import record_ticket_outcome
from app.models import Ticket
from app.services.guardrails import set_cache

settings = get_settings()


def _libpq_dsn(url: str) -> str:
    """把 SQLAlchemy 方言串转成原生 libpq 串。

    LangGraph 的 PostgresSaver 底层直接用 psycopg 连接，不认 `postgresql+psycopg://`
    这种 SQLAlchemy 方言前缀（会报 `missing "=" after ...`）。剥掉 `+驱动` 即可。
    """
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


async def process_ticket(ctx, ticket_id: int) -> dict:
    """执行一条工单的整条 LangGraph 流水线，并把结果写回 tickets 表。"""
    db = SessionLocal()
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return {"error": "ticket not found"}
        raw = ticket.raw_text
    finally:
        db.close()

    state: TicketState = {"ticket_id": ticket_id, "raw_text": raw}

    # checkpointer 连接生命周期限定在本次 invoke，避免连接泄漏
    with PostgresSaver.from_conn_string(_libpq_dsn(settings.database_url)) as checkpointer:
        checkpointer.setup()
        graph = build_graph().compile(checkpointer=checkpointer)
        result = graph.invoke(
            state, config={"configurable": {"thread_id": str(ticket_id)}}
        )

    # 把编排结果持久化到 tickets 表
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        t.masked_text = result.get("masked_text")
        t.lang = result.get("lang")
        t.intent = result.get("intent")
        t.intent_confidence = result.get("intent_confidence")
        t.intent_method = result.get("intent_method")
        t.retrieval_score = result.get("retrieval_score")
        t.short_circuited = result.get("short_circuited", False)
        t.draft_reply = result.get("draft_reply")
        t.citations = result.get("citations")
        t.risk_level = result.get("risk_level")
        t.action = result.get("action")

        fatal = bool(result.get("fatal_error"))
        if fatal or t.risk_level == "high" or t.action == "human_required":
            t.status = "awaiting_review" if t.draft_reply else "failed"
        elif t.action == "auto_send":
            # 自动出站：仅标记 closed（规格 1.2：本系统不实际发送，只起草）
            t.status = "closed"
        else:
            t.status = "awaiting_review"
        db.commit()
    finally:
        db.close()

    record_ticket_outcome(
        lang=result.get("lang", "unknown"),
        intent=result.get("intent", "other"),
        action=result.get("action", "human_required"),
        risk_level=result.get("risk_level", "high"),
    )

    # 结果缓存：相同脱敏文本 + 语种下次直接返回，不计费。
    # 注意：必须读 result（编排结果字典），不能读 ORM 对象 t——上面 db.close() 之后
    # t 已 detached，访问其属性会触发 DetachedInstanceError（只在真跑 worker 时暴露）。
    masked = result.get("masked_text")
    draft = result.get("draft_reply")
    if masked and draft:
        import json

        set_cache(
            masked,
            result.get("lang", "unknown"),
            json.dumps(
                {
                    "draft_reply": draft,
                    "citations": result.get("citations") or [],
                    "risk_level": result.get("risk_level"),
                    "action": result.get("action"),
                    "lang": result.get("lang"),
                    "intent": result.get("intent"),
                },
                ensure_ascii=False,
            ),
        )
    return result


class WorkerSettings:
    # arq 用 Redis 做队列；redis 不可用时任务无法入队（由 tickets 路由的护栏降级处理）
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # 失败最多重试 1 次（max_tries=2 = 首次 + 1 次重试）；重试耗尽标记 failed。
    # 不重复计费：checkpointer 会从已成功的节点续跑，已执行的 LLM 节点不重复调用（规格第 5 节）。
    functions = [func(process_ticket, max_tries=2)]
