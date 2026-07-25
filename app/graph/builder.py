"""StateGraph 装配 + 路由。

图结构（规格第 5 节）：
    mask → classify → ┬─(intent=other 或 conf<0.7)────────→ risk_gate
                      └─(其余)→ retrieve → ┬─(short_circuit)→ risk_gate
                                          └→ tools → generate → risk_gate → END

checkpointer：使用 LangGraph 的 PostgresSaver（后端为 PostgreSQL，与业务同库）。任务中断
后从最后成功节点恢复，配合 arq 不重复计费。checkpointer 的连接生命周期由 worker 管理
（见 tasks/worker.py 的 `with PostgresSaver.from_conn_string(...)`）。
"""
from langgraph.graph import END, StateGraph

from app.graph.nodes.classify import classify
from app.graph.nodes.generate import generate
from app.graph.nodes.mask import mask
from app.graph.nodes.retrieve import retrieve
from app.graph.nodes.risk_gate import risk_gate
from app.graph.nodes.tools import tools
from app.graph.state import TicketState


def _route_after_mask(state: TicketState) -> str:
    # 脱敏失败：绝不让原文进入 classify（其 masked_text 为空会回退到 raw_text，
    # 规则未命中时会把带 PII 的原文发给 LLM）——直接转风险闸门，守住「LLM 不见原文」红线。
    if state.get("fatal_error"):
        return "risk_gate"
    return "classify"


def _route_after_classify(state: TicketState) -> str:
    intent = state.get("intent")
    conf = state.get("intent_confidence", 1.0)
    # intent=other 或置信度不足：无依据不得自动出站，直接进风险闸门
    if intent == "other" or conf is None or conf < 0.7:
        return "risk_gate"
    return "retrieve"


def _route_after_retrieve(state: TicketState) -> str:
    # 检索不足短路：直接进风险闸门（无依据不得自动出站）
    if state.get("short_circuited"):
        return "risk_gate"
    return "tools"


def _traced(name: str, fn):
    """包装节点：记录耗时/成败到 agent_runs（规格第 10 节埋点）。

    为什么用装饰器统一包装而不是逐个节点内部埋点：节点逻辑保持纯粹，埋点集中一处，
    新增节点自动获得 trace。异常不吞——记录后重新抛出，交由 LangGraph/worker 处理。
    """
    import time

    from app.database import SessionLocal
    from app.metrics.events import record_agent_run

    def wrapper(state):
        t0 = time.perf_counter()
        err = None
        try:
            return fn(state)
        except Exception as e:  # noqa: BLE001 — 记录后原样抛出
            err = str(e)[:500]
            raise
        finally:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            tid = state.get("ticket_id")
            if tid:
                db = SessionLocal()
                try:
                    record_agent_run(
                        db, ticket_id=tid, node=name,
                        latency_ms=latency_ms, ok=err is None, error=err,
                    )
                    db.commit()
                except Exception:  # noqa: BLE001 — 埋点失败不能影响主链路
                    db.rollback()
                finally:
                    db.close()

    return wrapper


def build_graph() -> StateGraph:
    """装配节点与边，返回未编译的 StateGraph。

    之所以返回 StateGraph 而非直接 compile：checkpointer 需要存活的数据库连接，
    由 worker 在 `with PostgresSaver.from_conn_string(...)` 内 compile+invoke。
    """
    g = StateGraph(TicketState)
    g.add_node("mask", _traced("mask", mask))
    g.add_node("classify", _traced("classify", classify))
    g.add_node("retrieve", _traced("retrieve", retrieve))
    g.add_node("tools", _traced("tools", tools))
    g.add_node("generate", _traced("generate", generate))
    g.add_node("risk_gate", _traced("risk_gate", risk_gate))

    g.set_entry_point("mask")
    g.add_conditional_edges(
        "mask",
        _route_after_mask,
        {"risk_gate": "risk_gate", "classify": "classify"},
    )
    g.add_conditional_edges(
        "classify",
        _route_after_classify,
        {"risk_gate": "risk_gate", "retrieve": "retrieve"},
    )
    g.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        {"risk_gate": "risk_gate", "tools": "tools"},
    )
    g.add_edge("tools", "generate")
    g.add_edge("generate", "risk_gate")
    g.add_edge("risk_gate", END)
    return g
