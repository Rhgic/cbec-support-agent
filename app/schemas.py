"""Pydantic 请求 / 响应模型。

S1 阶段只定义了健康检查相关结构；后续阶段的工单 / 审核 / 知识库 schema
会在此追加，保持请求校验与响应序列化集中在一处。
"""
from pydantic import BaseModel


class DependencyStatus(BaseModel):
    """单个依赖的健康状态。"""

    status: str  # ok / down / configured / no_api_key
    detail: str | None = None


class HealthResponse(BaseModel):
    """/health 响应。"""

    status: str  # ok / degraded
    dependencies: dict[str, DependencyStatus]


# ===== 工单 =====
class TicketCreate(BaseModel):
    """提交工单请求。lang 可省略，由分类节点自动识别。"""

    text: str
    lang: str | None = None


class AgentRunOut(BaseModel):
    """节点级执行记录（trace 用）。"""

    node: str
    latency_ms: int
    token_in: int | None = None
    token_out: int | None = None
    cost_usd: float | None = None
    ok: bool
    error: str | None = None


class TicketOut(BaseModel):
    """工单状态与结果。"""

    ticket_id: int
    status: str
    lang: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    intent_method: str | None = None
    retrieval_score: float | None = None
    short_circuited: bool | None = None
    draft_reply: str | None = None
    citations: list[str] = []
    risk_level: str | None = None
    action: str | None = None


class TicketTrace(BaseModel):
    """全链路 trace。"""

    ticket_id: int
    runs: list[AgentRunOut]


# ===== 审核 =====
class ReviewCreate(BaseModel):
    """审核动作。"""

    action: str  # approved / edited / rejected
    final_reply: str | None = None
    failure_tags: list[str] | None = None


class ReviewOut(BaseModel):
    """待审队列项。"""

    ticket_id: int
    risk_level: str
    draft_reply: str
    action: str | None = None
