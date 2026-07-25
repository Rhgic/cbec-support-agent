"""埋点写入（规格 10）。

每工单维度：lang / intent / intent_method / short_circuited / risk_level / action /
tokens / cost_usd / 各节点 latency_ms。
- Prometheus 指标按 lang、intent 维度聚合，支撑「自动解决率 / 拒答率 / 规则命中率 /
  单均成本」等；registry 复用 observability 的，避免多 registry 冲突
- 节点级执行记录写入 agent_runs 表，供 /tickets/{id}/trace 演示与离线归因
"""
from prometheus_client import Counter, Histogram

from app.models import AgentRun
from app.observability import REGISTRY

# 按 (lang, intent, action, risk_level) 四维聚合，可直接算出自动解决率、拒答率等
TICKETS_TOTAL = Counter(
    "tickets_total",
    "工单总数（按语种/意图/动作/风险）",
    ["lang", "intent", "action", "risk_level"],
    registry=REGISTRY,
)
# 单均成本 = sum(cost_total_usd) / tickets_total
COST_TOTAL = Counter("cost_total_usd", "累计 LLM 成本(USD)", registry=REGISTRY)
# 节点耗时直方图，按节点分组
NODE_LATENCY = Histogram(
    "node_latency_seconds", "各节点耗时(秒)", ["node"], registry=REGISTRY
)


def record_agent_run(
    db,
    ticket_id: int,
    node: str,
    latency_ms: int,
    token_in: int | None = None,
    token_out: int | None = None,
    cost_usd: float | None = None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """写入一条节点级执行记录（agent_runs）。"""
    db.add(
        AgentRun(
            ticket_id=ticket_id,
            node=node,
            latency_ms=latency_ms,
            token_in=token_in,
            token_out=token_out,
            cost_usd=cost_usd,
            ok=ok,
            error=error,
        )
    )


def record_ticket_outcome(
    lang: str,
    intent: str,
    action: str,
    risk_level: str,
    cost_usd: float = 0.0,
    tokens: int = 0,
) -> None:
    """工单结算时累加聚合指标。"""
    TICKETS_TOTAL.labels(
        lang=lang or "unknown",
        intent=intent or "other",
        action=action or "human_required",
        risk_level=risk_level or "high",
    ).inc()
    COST_TOTAL.inc(cost_usd)
    # tokens 仅记录，不入 Prometheus 主指标（成本已覆盖）
    _ = tokens
