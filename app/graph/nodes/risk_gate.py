"""⑥ 风险闸门：规则层（作者）优先 → LLM 层分级 → 动作映射（规格 6）。

两层，规则层优先级高于 LLM 层；规则判定为 high 时不再询问 LLM。
动作映射（确定性，非作者自写）：
    low → auto_send（自动出站）
    mid → quick_review（人工一键通过）
    high → human_required（强制人工，Agent 仅起草）

降级方向（刻意与成本护栏相反，规格 15）：风险闸门自身异常 → 默认 high，拦截。
代价不对称——风控失效是资损，护栏失效只是多花钱。
"""
import re

from app.graph.state import TicketState
from app.services.llm import chat_json, risk_prompt

# 确定性动作映射（规格 6 表）
ACTION_MAP = {"low": "auto_send", "mid": "quick_review", "high": "human_required"}

# ===== 风险闸门规则层 =====
# 六条确定性规则，按优先级排序；命中即返回 high，不调 LLM。
# 规则清单的业务来源说明见每条注释。面试会问"高风险边界怎么划的？会不会漏？"。
# 原则：规则只拦截明确高风险场景；其余交 LLM 判 low/mid（代价不对称）。

# 金额正则：匹配含货币符号或金额关键词的文本，用于拦截退款/赔偿类回复。
# 注意：被扫的是 draft_reply，语种为 en/es/id（知识库是中文，但回复不是）——
# 故原实现里的中文关键词（金额/赔/退钱…）为死代码，永不命中，已删除。
# 目标市场为 US/MX/ID：覆盖 $ / € / Rp 及 USD/EUR/MXN/IDR；原实现覆盖了非市场的
# GBP、却漏了 MXN/IDR/Rp（即 es=墨西哥、id=印尼的本币），已修正。
# P4 金额归一化：符号/ISO 代码/币种词，且数字在币种前后皆可（$50 / 50 USD / 50 dollars / 50 bucks）。
# 已知代价：会连带命中合法价格（"costs 25 EUR"）→ 抬高「误升 high 率」，用 eval_risk 反向盯（§5.4）。
_AMOUNT_RE = re.compile(
    r"[＄$€]\s*\d[\d,.]*"                                   # 符号+数字：$100、€50、＄50.00
    r"|\bRp\.?\s*\d"                                        # 印尼盾：Rp50000 / Rp. 50.000
    r"|\b(?:USD|EUR|MXN|IDR)\s*\d"                          # ISO 代码+数字：USD 50
    r"|\b\d[\d,.]*\s*(?:USD|EUR|MXN|IDR)\b"                 # 数字+ISO：50 EUR
    r"|\b\d[\d,.]*\s*(?:dollars?|bucks?|euros?|pesos?|rupiah|d[oó]lares?)\b"  # 数字+币种词：50 dollars
    r"|refund.*amount|compensat",                          # 英文退款/赔偿语义
    re.I,
)

# 承诺词库（三语种）：含"保证 / 一定 / 确保 / guarantee / promise / aseguramos"等
# 客服回复中出现承诺词 + 未含免责声明 → 高风险
_PROMISE_RE = {
    "en": re.compile(
        r"\b(?:guarantee|promise|assure|certainly will|definitely|for sure|without fail|"
        r"100%|absolutely|we will refund|we will compensate)\b", re.I,
    ),
    "es": re.compile(
        # 加 devolveremos/devolvemos：eval 抓到的西语漏网（"Le devolveremos 30 EUR"）——
        # 精准补承诺词即可闭合，无需放宽金额正则去连带误伤合法价格。
        r"\b(?:garantizamos|prometemos|aseguramos|seguro|definitivamente|sin falta|"
        r"100%|absolutamente|reembolsaremos|compensaremos|devolveremos|devolvemos)\b", re.I,
    ),
    "id": re.compile(
        r"\b(?:kami jamin|janji|pastikan|pasti|tentu saja|tanpa gagal|"
        r"100%|mutlak|kami akan refund|kami akan ganti)\b", re.I,
    ),
}

# P4 否定豁免：承诺词前若干字符内出现否定词 → 该处不计承诺（避免 "we do not guarantee" 误判）。
# 注意 id 否定词不含 "tanpa"——"tanpa gagal"(without fail) 本身是承诺短语。
_NEG = {
    "en": re.compile(r"\b(?:no|not|never|cannot|can'?t|won'?t|don'?t|doesn'?t|isn'?t|aren'?t|unable)\b", re.I),
    "es": re.compile(r"\bno\b", re.I),
    "id": re.compile(r"\b(?:tidak|tak|bukan|belum|nggak|gak)\b", re.I),
}

# P4 敏感动作：回复要求改地址/改支付/索要验证码或密码 → 账户接管/诈骗风险，强制人工。
_SENSITIVE_RE = re.compile(
    r"\b(?:change|update|switch|modify)\b.{0,24}\b(?:address|payment|card|bank|account)\b"
    r"|\b(?:verification code|one[- ]?time code|otp|pin|cvv|security code|password)\b"
    r"|cambi\w*.{0,24}(?:direcci[oó]n|pago|tarjeta|cuenta)|c[oó]digo de verificaci[oó]n|contrase[ñn]a"
    r"|(?:ubah|ganti)\b.{0,24}\b(?:alamat|pembayaran|kartu|rekening)\b|kode verifikasi|kata sandi",
    re.I,
)


def _has_unnegated_promise(draft: str, lang: str) -> bool:
    """承诺词命中，且该处未被前置否定词豁免（P4）。全部命中都被否定 → 返回 False。"""
    pat = _PROMISE_RE.get(lang)
    if not pat:
        return False
    neg = _NEG.get(lang)
    for m in pat.finditer(draft):
        window = draft[max(0, m.start() - 35):m.start()]
        if not (neg and neg.search(window)):
            return True
    return False


def evaluate_risk_rules(state: TicketState) -> dict | None:
    """规则层：返回 {"level":"high","reasons":[...]} 表示命中（必须 high）；
    返回 None 表示规则未命中，交由 LLM 层判定 low/mid。

    六条规则按优先级排列，命中即拦截：
    1. intent == 'other' → 非业务意图，无依据不可自动出站
    2. intent_confidence < 0.7 → 分类不可靠，无依据不可自动出站
    3. short_circuited == True → 检索不足，无依据不可自动出站
    4. tool_errors 非空 → 工具调用失败，数据不可信
    5. 金额正则命中 → 涉及退款/赔偿，强制人工
    6. 承诺词库命中（三语种）→ 回复含确定性承诺，强制人工
    """
    reasons: list[str] = []

    # 规则 1：非业务意图
    if state.get("intent") == "other":
        reasons.append("intent=other，非业务意图")

    # 规则 2：分类置信度不足
    conf = state.get("intent_confidence")
    if conf is not None and conf < 0.7:
        reasons.append(f"分类置信度 {conf:.2f} < 0.7")

    # 规则 3：检索短路
    if state.get("short_circuited") is True:
        reasons.append("检索短路，无足够依据")

    # 规则 4：工具调用异常
    tool_errs = state.get("tool_errors") or []
    if tool_errs:
        reasons.append(f"工具调用失败: {tool_errs}")

    # 规则 5：金额正则（检查草稿回复）
    draft = state.get("draft_reply", "") or ""
    if draft and _AMOUNT_RE.search(draft):
        reasons.append("回复涉及金额/退款/赔偿")

    # 规则 6：承诺词库（按当前语种），P4 加否定豁免——"we do not guarantee" 不算承诺
    lang = state.get("lang", "en")
    if draft and _has_unnegated_promise(draft, lang):
        reasons.append(f"回复含确定性承诺（{lang}）")

    # 规则 7（P4）：敏感动作——要求改地址/改支付/索要验证码密码 → 账户接管/诈骗
    if draft and _SENSITIVE_RE.search(draft):
        reasons.append("回复涉及敏感动作（改地址/改支付/索要验证码）")

    if reasons:
        return {"level": "high", "reasons": reasons}
    return None


def _forced_high(reasons: list[str]) -> dict:
    return {"risk_level": "high", "risk_reasons": reasons, "action": "human_required"}


def risk_gate(state: TicketState) -> dict:
    # 上游已强制 high（如生成失败）→ 直接拦截，不再二次判定
    if state.get("risk_level") == "high":
        return _forced_high(state.get("risk_reasons", []) + ["上游已强制 high"])

    # 脱敏失败：原文未脱敏，必须转人工
    if state.get("fatal_error"):
        return _forced_high(["脱敏失败，转人工"])

    # 规则层优先（作者实现）
    rule = evaluate_risk_rules(state)
    if rule is not None:
        lvl = rule["level"]
        return {
            "risk_level": lvl,
            "risk_reasons": rule.get("reasons", []),
            "action": ACTION_MAP[lvl],
        }

    # LLM 层：作者构造 prompt，输出 low / mid
    prompt = risk_prompt(state.get("draft_reply", ""))
    res = chat_json(prompt["system"], prompt["user"])

    # 风险闸门异常 → 默认 high，拦截（与成本护栏降级方向相反）
    if res.error:
        return _forced_high(["风险闸门异常，默认拦截"])

    d = res.data
    lvl = d.get("level", "mid")
    if lvl not in ACTION_MAP:
        lvl = "mid"
    return {
        "risk_level": lvl,
        "risk_reasons": d.get("reasons", []),
        "action": ACTION_MAP[lvl],
    }
