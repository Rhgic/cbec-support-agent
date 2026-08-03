"""LangGraph 状态定义（规格第 5 节）。

为什么用 TypedDict + NotRequired：节点之间只透传需要的字段，缺失字段即「该节点
尚未产生」。checkpointer 会按字段增量持久化，worker 崩溃后从最后成功节点恢复。
"""
from typing import Literal, NotRequired, TypedDict


class Chunk(TypedDict):
    chunk_id: int
    content: str
    score: float
    source_url: str


class TicketState(TypedDict):
    # 输入
    ticket_id: int
    raw_text: str
    # 当前用户此前的脱敏会话摘要；仅在实时接入场景由接入层填充。
    conversation_context: NotRequired[str]

    # ① 脱敏
    masked_text: NotRequired[str]
    pii_placeholders: NotRequired[dict[str, str]]

    # ② 分类
    lang: NotRequired[Literal["en", "es", "id", "unknown"]]
    intent: NotRequired[Literal["logistics", "return", "product", "other"]]
    intent_confidence: NotRequired[float]
    intent_method: NotRequired[Literal["rule", "llm"]]

    # ③ 检索
    chunks: NotRequired[list[Chunk]]
    retrieval_score: NotRequired[float]
    short_circuited: NotRequired[bool]

    # ④ 工具
    tool_results: NotRequired[dict]
    tool_errors: NotRequired[list[str]]

    # ⑤ 生成
    draft_reply: NotRequired[str]
    citations: NotRequired[list[str]]

    # ⑥ 风险
    risk_level: NotRequired[Literal["low", "mid", "high"]]
    risk_reasons: NotRequired[list[str]]
    action: NotRequired[Literal["auto_send", "quick_review", "human_required"]]

    # 通用
    fatal_error: NotRequired[str]
