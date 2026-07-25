"""⑤ 生成节点：多语种回复 + 证据引用。

输入：中文检索片段 + 工具结果 + 目标语种 → 输出：目标语种回复 + 引用的 source_url 列表。
- 必须携带证据引用；无引用（且确有检索结果）视为无效，升级 high
- 回复中的占位符保持不变，真实值只在出站前一步还原
- LLM 失败重试 1 次（llm.py 内），再失败 → 兜底模板（「已收到，正在人工核实」）+ 风险置 high

注意：生成失败会直接置 risk_level="high"；风险闸门（risk_gate）尊重上游已强制的 high，
不再二次判定——避免「生成都失败了还放行」。
"""
from app.graph.state import TicketState
from app.services.llm import chat_json, generate_prompt

# 兜底模板（三语种），无引用/生成失败时使用
FALLBACK = {
    "en": "We have received your request and are verifying it manually. We will get back to you shortly.",
    "es": (
        "Hemos recibido su solicitud y la estamos verificando manualmente. "
        "Le responderemos pronto."
    ),
    "id": (
        "Kami telah menerima permintaan Anda dan sedang memverifikasinya secara manual. "
        "Kami akan segera menghubungi Anda."
    ),
}


def _format_tools(tool_results: dict) -> str:
    if not tool_results:
        return ""
    parts = []
    if order := tool_results.get("order"):
        parts.append(f"订单状态: {order.get('status')}; 物流单号: {order.get('tracking_no')}")
    if tp := tool_results.get("return_policy"):
        parts.append(f"退货政策: {tp.get('reason')}")
    if tr := tool_results.get("tracking"):
        ev = tr.get("events", [])
        if ev:
            latest = ev[0]
            parts.append(f"最新轨迹: {latest.get('status')} @ {latest.get('time')}")
    if exc := tool_results.get("tracking_exception"):
        parts.append(f"异常提示: {exc}")
    return "\n".join(parts)


def _fabricated_citations(citations: list[str], chunks: list[dict]) -> list[str]:
    """P2 引用真实性校验：返回不在本次检索 source_url 集合内的引用（疑似 LLM 编造的来源）。

    「有引用 ≠ 有据」——模型可能引用一个本次检索根本没返回的 source_url。纯函数，无需 LLM。
    """
    allowed = {c.get("source_url") for c in chunks if c.get("source_url")}
    return [u for u in (citations or []) if u not in allowed]


def generate(state: TicketState) -> dict:
    lang = state.get("lang", "en")
    chunks = state.get("chunks", []) or []
    tool_results = state.get("tool_results", {}) or {}

    # 每段必须带上 source_url，否则模型不知道能引用哪些来源，citations 永远为空
    # → 被 P2 引用校验判为「有检索却无引用」强制升 high（真跑 LLM 后才暴露）
    retrieved = "\n---\n".join(
        f"[source: {c.get('source_url', '')}]\n{c['content']}" for c in chunks
    )
    tool_info = _format_tools(tool_results)

    # 传脱敏后的客户原话——LLM 全程不见原文（PII 只在出站前一步还原）
    customer_text = state.get("masked_text") or state.get("raw_text") or ""
    prompt = generate_prompt(customer_text, retrieved, tool_info, lang)
    res = chat_json(prompt["system"], prompt["user"])

    if res.error or not res.data.get("reply"):
        # 兜底：生成失败，升级 high，由风险闸门强制人工
        return {
            "draft_reply": FALLBACK.get(lang, FALLBACK["en"]),
            "citations": [],
            "risk_level": "high",
            "risk_reasons": ["生成失败，兜底模板 + 升级 high"],
        }

    reply = res.data["reply"]
    citations = res.data.get("citations", []) or []

    # 有检索结果却无引用 → 视为无依据，升级 high
    if not citations and chunks:
        return {
            "draft_reply": reply,
            "citations": [],
            "risk_level": "high",
            "risk_reasons": ["回复缺少证据引用，升级 high"],
        }

    # P2 引用真实性校验：引用必须来自本次检索返回的来源，否则视为编造 → 升 high，
    # 并只保留真实的引用。这是"有引用 ≠ 有据"里最便宜、最硬的那半。
    fabricated = _fabricated_citations(citations, chunks)
    if fabricated:
        return {
            "draft_reply": reply,
            "citations": [u for u in citations if u not in fabricated],
            "risk_level": "high",
            "risk_reasons": [f"引用了未检索到的来源（疑似编造）：{fabricated}"],
        }

    return {"draft_reply": reply, "citations": citations}
