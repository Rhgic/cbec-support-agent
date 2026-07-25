"""本地 live demo 服务：面试时一条命令起，屏幕共享现场输工单真跑。

它做什么：
  - 同源伺服 web/ 控制台，并在伺服 app.js 时把 USE_MOCK 临时置 false（源文件不动，
    GitHub Pages 那份仍是样例模式）。
  - 实现控制台 live 路径需要的三个接口（POST /tickets → GET /tickets/{id} → /trace），
    但**同步内联**跑真实管道（mask→classify→retrieve→tools→generate→risk_gate），
    不依赖 arq/worker。路由与 app/graph/builder.py 一致。
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

import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
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

settings = get_settings()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="cbec live demo")
RESULTS: dict[int, dict] = {}


class TicketIn(BaseModel):
    text: str
    lang: str | None = None


def _run_pipeline(raw_text: str) -> int:
    db = SessionLocal()
    try:
        tk = Ticket(raw_text=raw_text, masked_text=None, status="processing")
        db.add(tk)
        db.commit()
        db.refresh(tk)
        tid = tk.id
    finally:
        db.close()

    state: dict = {"ticket_id": tid, "raw_text": raw_text}
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
    ticket_out = {
        "ticket_id": tid,
        "status": "closed" if action == "auto_send" else "awaiting_review",
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
    }
    RESULTS[tid] = {"ticket": ticket_out, "trace": {"ticket_id": tid, "runs": trace}}
    return tid


@app.post("/tickets")
def create_ticket(payload: TicketIn, authorization: str | None = Header(default=None)):
    if authorization != f"Bearer {settings.demo_token}":
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    tid = _run_pipeline(payload.text)
    return {"ticket_id": tid, "task_id": None}


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
