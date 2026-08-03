"""本地 live demo 服务：面试时一条命令起，屏幕共享现场输工单真跑。

它做什么：
  - 同源伺服 web/ 控制台，并在伺服 app.js 时把 USE_MOCK 临时置 false（源文件不动，
    GitHub Pages 那份仍是样例模式）。
  - 实现控制台 live 路径需要的接口（POST /tickets → GET /tickets/{id} → /trace），
    但**同步内联**跑真实管道（mask→classify→retrieve→tools→generate→risk_gate），
    不依赖 arq/worker。路由与 app/graph/builder.py 一致。
  - 提供通用 POST /webhook/messages + GET /events：平台新消息按 user_id 聚合
    已脱敏历史，Agent 完成后通过 SSE 推送建议到客服侧边栏。
  - generate / LLM 分类 / LLM 风险层需要 DeepSeek key（.env 的 DEEPSEEK_API_KEY）；
    没 key 时优雅降级为转人工——mask/规则/Milvus 检索/规则风险仍是真的。

跑法（项目根目录，先确保 PG 起了、知识库已灌 Milvus）：
  DEEPSEEK_API_KEY=sk-xxx TOKENIZERS_PARALLELISM=false \
    .venv/bin/uvicorn scripts.demo_server:app --port 8100
然后浏览器开 http://localhost:8100/
"""
import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_settings
from app.database import SessionLocal
from app.graph.nodes.classify import classify
from app.graph.nodes.generate import generate
from app.graph.nodes.mask import mask
from app.graph.nodes.retrieve import THRESHOLD, retrieve
from app.graph.nodes.risk_gate import risk_gate
from app.graph.nodes.tools import tools
from app.models import Ticket
from app.services.llm import chat_json
from app.services.ticket_outcome import apply_ticket_result

settings = get_settings()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="cbec live demo")
RESULTS: dict[int, dict] = {}
# 演示态内存会话：生产接入应换成 Redis / 数据库，并按租户隔离。
CONVERSATIONS: dict[str, list[dict[str, str | int | None]]] = defaultdict(list)
SUBSCRIBERS: set[asyncio.Queue[dict]] = set()


class TicketIn(BaseModel):
    text: str
    lang: str | None = None


class ReviewIn(BaseModel):
    action: str
    final_reply: str | None = None


class TranslateIn(BaseModel):
    """翻译只接收控制台已脱敏的文本，避免把原始 PII 再发给模型。"""

    text: str
    target_lang: str = "zh"


class PlatformMessageIn(BaseModel):
    """平台标准化后的新消息事件。

    平台适配器只要转成这三个字段即可接入；不要把邮箱、手机号等原始 PII 放进
    customer_id 或 metadata。原始 message 会先经过现有 mask 节点。
    """

    user_id: str
    message: str
    channel: str = "platform"


def _format_history(history: list[dict[str, str | int | None]]) -> str:
    """只把已脱敏的近 6 条历史提供给模型，避免无限增长和重复发送 PII。"""
    lines = []
    for item in history[-6:]:
        text = item.get("message")
        if isinstance(text, str) and text:
            lines.append(f"客户此前消息：{text}")
    return "\n".join(lines)


def _run_pipeline(raw_text: str, user_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        tk = Ticket(raw_text=raw_text, masked_text=None, status="processing")
        db.add(tk)
        db.commit()
        db.refresh(tk)
        tid = tk.id
    finally:
        db.close()

    history = CONVERSATIONS.get(user_id or "", [])
    state: dict = {
        "ticket_id": tid,
        "raw_text": raw_text,
        "conversation_context": _format_history(history),
    }
    trace: list[dict] = []

    def step(name, fn) -> None:
        t0 = time.perf_counter()
        ok, err = True, None
        try:
            state.update(fn(state) or {})
        except Exception as e:  # noqa: BLE001 — demo 里任一节点异常都记下、不崩服务
            ok, err = False, str(e)[:200]
        run = {"node": name, "latency_ms": int((time.perf_counter() - t0) * 1000), "ok": ok}
        if err:
            run["error"] = err
        trace.append(run)

    # 路由与 builder.py 一致
    step("mask", mask)
    if not state.get("fatal_error"):
        step("classify", classify)
        conf = state.get("intent_confidence", 1.0)
        if not (state.get("intent") == "other" or conf is None or conf < 0.7):
            step("retrieve", retrieve)
            if state.get("short_circuited"):
                trace[-1]["error"] = f"短路：top1 {state.get('retrieval_score', 0):.2f} < {THRESHOLD}"
            else:
                step("tools", tools)
                step("generate", generate)
    step("risk_gate", risk_gate)
    if state.get("risk_level") == "high" and not trace[-1].get("error"):
        reasons = state.get("risk_reasons") or []
        trace[-1]["error"] = reasons[0] if reasons else "规则命中，转人工"

    action = state.get("action")

    # 回写编排结果——与 worker / eval_e2e 共用 apply_ticket_result。
    # 漏掉这一步的后果不是"少存一条记录"：工单会永远停在 status='processing'、action=NULL，
    # 而 /metrics 的 auto_solve_rate 是 auto_send/总数，于是**每演示一条就把指标拉低一次**。
    # 状态判定也必须走同一个函数，否则这里会出现第四份各自为政的映射
    # （原实现少了 fatal / 无草稿这两种情况）。
    db = SessionLocal()
    try:
        apply_ticket_result(db, tid, state)
        db.commit()
        persisted_status = db.get(Ticket, tid).status  # 在 close 前读出，避免 DetachedInstanceError
    finally:
        db.close()

    ticket_out = {
        "ticket_id": tid,
        "status": persisted_status,
        "lang": state.get("lang"),
        "intent": state.get("intent"),
        "intent_confidence": state.get("intent_confidence"),
        "intent_method": state.get("intent_method"),
        "retrieval_score": state.get("retrieval_score"),
        "short_circuited": state.get("short_circuited"),
        "draft_reply": state.get("draft_reply"),
        "citations": state.get("citations") or [],
        "risk_level": state.get("risk_level"),
        "action": action,
        # 展示层只拿脱敏后的客户消息；原始内容仍只在后端处理链路中使用。
        "customer_message": state.get("masked_text"),
        # 工具结果不含原始 PII，供演示台展示订单与物流依据。
        "tool_results": state.get("tool_results") or {},
        "conversation": {
            "user_id": user_id,
            "history_count": len(history),
            "recent_messages": history[-3:],
        },
    }
    RESULTS[tid] = {"ticket": ticket_out, "trace": {"ticket_id": tid, "runs": trace}}
    return tid


@app.post("/tickets")
def create_ticket(payload: TicketIn, authorization: str | None = Header(default=None)):
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    tid = _run_pipeline(payload.text)
    return {"ticket_id": tid, "task_id": None}


async def _publish(event: dict) -> None:
    """SSE 仅推送处理后的脱敏工单结果；慢客户端不阻塞 Agent 主链路。"""
    for queue in list(SUBSCRIBERS):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            continue


@app.post("/webhook/messages")
async def receive_platform_message(
    payload: PlatformMessageIn,
    authorization: str | None = Header(default=None),
):
    """通用平台入口：新消息到达即带历史上下文运行 Agent，并推送建议。"""
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")

    ticket_id = await asyncio.to_thread(_run_pipeline, payload.message, payload.user_id)
    result = RESULTS[ticket_id]
    ticket = result["ticket"]
    # 保存的始终是 mask 节点输出，不保留用于模型上下文的原文。
    CONVERSATIONS[payload.user_id].append(
        {
            "ticket_id": ticket_id,
            "message": ticket.get("customer_message"),
            "intent": ticket.get("intent"),
            "channel": payload.channel,
        }
    )
    ticket["conversation"]["history_count"] = len(CONVERSATIONS[payload.user_id]) - 1
    event = {
        "type": "agent_suggestion",
        "user_id": payload.user_id,
        "channel": payload.channel,
        "ticket": ticket,
        "trace": result["trace"],
    }
    await _publish(event)
    return {
        "accepted": True,
        "ticket_id": ticket_id,
        "risk_level": ticket.get("risk_level"),
        "action": ticket.get("action"),
    }


@app.get("/events")
async def events(token: str = Query(default="")):
    """客服插件的实时建议流。演示使用 query token；生产应改为短期会话令牌。"""
    if token != settings.demo_token:
        raise HTTPException(status_code=401, detail="无效或缺失 token")

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
        SUBSCRIBERS.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: agent_suggestion\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            SUBSCRIBERS.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    r = RESULTS.get(ticket_id)
    if not r:
        raise HTTPException(status_code=404, detail="工单不存在")
    return r["ticket"]


@app.get("/tickets/{ticket_id}/trace")
def get_trace(ticket_id: int):
    r = RESULTS.get(ticket_id)
    if not r:
        raise HTTPException(status_code=404, detail="工单不存在")
    return r["trace"]


@app.get("/health")
def health():
    """给演示前端判断“实时后端”状态，避免静态页面误报。"""
    return {"status": "ok", "mode": "live_demo"}


@app.post("/review/{ticket_id}")
def review_ticket(ticket_id: int, payload: ReviewIn, authorization: str | None = Header(default=None)):
    """演示审核闭环：更新内存工单状态，不执行任何外部消息发送。"""
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    if payload.action not in ("approved", "edited", "rejected"):
        raise HTTPException(status_code=422, detail="action 必须是 approved、edited 或 rejected")
    result = RESULTS.get(ticket_id)
    if not result:
        raise HTTPException(status_code=404, detail="工单不存在")
    ticket = result["ticket"]
    ticket["status"] = "failed" if payload.action == "rejected" else "closed"
    if payload.action == "edited" and payload.final_reply:
        ticket["draft_reply"] = payload.final_reply
    return ticket


@app.post("/translate")
def translate(payload: TranslateIn, authorization: str | None = Header(default=None)):
    """实时翻译接口：有 DeepSeek key 才实际调用；无 key 明确报不可用。"""
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    if payload.target_lang != "zh":
        raise HTTPException(status_code=422, detail="演示版目前只支持翻译成中文")
    if not settings.deepseek_api_key:
        raise HTTPException(status_code=503, detail="翻译服务未配置 DeepSeek API key")

    result = chat_json(
        system_prompt=(
            "You are a professional customer-service translator. Translate the provided text into "
            "Simplified Chinese. Preserve all amounts, dates, order numbers, tracking numbers, and "
            "placeholder tokens such as [EMAIL_1] exactly. Output one JSON object with exactly the "
            'field "translation".'
        ),
        user_prompt=f"Translate this text into Simplified Chinese:\n{payload.text}",
    )
    translation = result.data.get("translation") if not result.error else None
    if not isinstance(translation, str) or not translation.strip():
        raise HTTPException(status_code=502, detail="翻译服务返回异常")
    return {"translation": translation, "mode": "live"}


@app.get("/app.js")
def live_appjs():
    # 伺服时把 USE_MOCK 置 false，切实时后端；源文件不改
    src = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    src = src.replace("const USE_MOCK = true", "const USE_MOCK = false")
    return Response(content=src, media_type="application/javascript")


@app.on_event("startup")
def _warm() -> None:
    # 预热 BGE-m3 + Milvus，让第一条工单不用等模型加载
    try:
        from app.services.vectorstore import search

        search(None, "logistics", "warmup", k=1)
        print("[demo] model + Milvus warmed")
    except Exception as e:  # noqa: BLE001
        print(f"[demo] warmup skipped: {e}")


# 静态站点挂最后（API 路由优先匹配）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
