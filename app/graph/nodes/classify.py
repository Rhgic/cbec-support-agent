"""② 分类节点：语种识别 + 意图分类。

两层结构（与作者上一项目「规则粗筛 + LLM 精排」同构）：
- 规则层 classify_by_rules：高频模式直接命中，intent_method="rule"，**不调 LLM**，直接决定单均成本
- LLM 层：规则未命中时调 DeepSeek（json_mode），语种与意图一次调用完成

intent=="other" 或 confidence<0.7 → 跳过检索与生成，直接进风险闸门（由 builder 路由）。
"""
# ===== 规则分类器：三语种关键词 + 正则 =====
# 语种与意图的关键词表按实战高频模式维护（规格 4.2）。规则层命中直接出结果，
# 省一次 LLM 调用——面试会问"规则命中率多少、剩下的为什么规则覆盖不了"。
# 原则：宁可漏判（交 LLM）不可误判（wrong intent → 无匹配工具 → 用户体验差）。
import re

from app.graph.state import TicketState
from app.services.llm import chat_json

# 语种关键词：按特征词首匹配（先 en 再 es 再 id，避免共用词交叉命中）。
# en 排第一因为最常用；id 排最后因为印尼语常混英语词。
_LANG_PATTERNS = [
    ("en", re.compile(
        r"\b(?:where|track|order|refund|return|shipping|delivery|payment|item|package|help|"
        r"received|broken|damaged|wrong|missing|cancel|exchange|size|color|price)\b",
        re.I,
    )),
    ("es", re.compile(
        r"\b(?:dónde|seguimiento|pedido|reembolso|devolución|envío|entrega|pago|artículo|"
        r"paquete|ayuda|recibido|roto|dañado|equivocado|falta|cancelar|cambio|talla|color|precio)\b",
        re.I,
    )),
    ("id", re.compile(
        r"\b(?:dimana|lacak|pesanan|pengembalian|dana|pengiriman|barang|paket|bantuan|"
        r"diterima|rusak|cacat|salah|kurang|batal|tukar|ukuran|warna|harga)\b",
        re.I,
    )),
]

# 借词（refund / shipping / product）会让印尼语消息被误判为英语。
# 先用具有语法区分度的强特征判语种，再回退到上面的通用词表。
_LANG_STRONG_PATTERNS = [
    ("es", re.compile(
        r"\b(?:quiero|puedo|puede|gracias|cu[aá]nto|cu[aá]ndo|cambiarlo|cambiarla|"
        r"env[ií]o|devoluci[óo]n|reembolso|pedido|talla|m[aá]s grande)\b",
        re.I,
    )),
    ("id", re.compile(
        r"\b(?:saya|gimana|bagaimana|cara|barangnya|datang|untuk|baru|masuk|berapa|"
        r"bisakah|ditukar|dengan|lain|minta|peninjauan|makasih|cepet)\b",
        re.I,
    )),
]

# 这些文本虽然含 shipping / pengiriman 等高频词，但不是物流查询。
# 不在规则层强判，交给 LLM 处理，符合“宁可漏判、不可误判”的原则。
_RULE_BYPASS = re.compile(
    r"(?:\b(?:thanks?|thank you|gracias|makasih|terima kasih)\b.*"
    r"\b(?:shipping|delivery|env[ií]o|entrega|pengiriman|kirim)\b|"
    r"\bchange\b.*\bshipping address\b|"
    r"\bsolo quer[ií]a decir\b.*\b(?:excelente|genial|bueno)\b)",
    re.I,
)

# 容易被物流/产品关键词抢先命中的退换货表达，必须在通用优先级之前处理。
_RETURN_OVERRIDES = {
    "en": re.compile(r"\breturn shipping\b", re.I),
    "es": re.compile(r"(?:\bcambiar(?:lo|la)?\b.*\btalla\b|\benv[ií]o de devoluci[óo]n\b)", re.I),
    "id": re.compile(r"\b(?:ditukar|tukar)\b.*\b(?:ukuran|barang)\b", re.I),
}

# 意图关键词：按优先级 logistics → return → product → other
_INTENTS = {
    "logistics": {
        "en": re.compile(
            r"\b(?:track|tracking|shipping|shipped|dispatch|deliver|delivered|delivery|courier|"
            r"carrier|customs|arriv|eta|estimated|stuck|delay|"
            r"where.*(?:order|package|parcel|item|stuff|it)|haven'?t.*(?:got|arriv|receiv)|"
            r"hasn'?t.*(?:arriv|updat|mov)|not.*(?:here|arriv|receiv)|still.*(?:here|not|come)|"
            r"when.*(?:will|get|arriv)|out for delivery)\b",
            re.I,
        ),
        "es": re.compile(
            r"\b(?:seguimiento|env[ií]o|entrega|entregado|reparto|mensajer[ií]a|transportista|"
            r"aduana|eta|estimado|lleg|sali[óo]|atascado|retraso|"
            r"d[óo]nde.*(?:pedido|paquete|art[íi]culo)|no.*lleg|sigue sin|todav[íi]a.*no|"
            r"cu[áa]ndo.*(?:llega|sale))\b",
            re.I,
        ),
        "id": re.compile(
            r"\b(?:lacak|resi|kirim|dikirim|pengiriman|kurir|pengangkut|bea cukai|cukai|eta|"
            r"perkiraan|tiba|sampai|sampe|nyangkut|terlambat|"
            r"belum.*(?:sampai|sampe|datang|tiba)|blm.*(?:sampe|nyampe|datang)|"
            r"dimana.*(?:pesanan|barang|paket)|kapan.*(?:sampai|sampe|tiba))\b",
            re.I,
        ),
    },
    "return": {
        "en": re.compile(
            r"\b(?:refund|return|exchange|swap|reimburs|money back|get my money|want.*money|"
            r"send.*back|cancel.*(?:order|it)|broken|cracked|damaged|defect|defective|"
            r"wrong.*(?:item|thing|one|color)|doesn'?t fit|don'?t fit)\b",
            re.I,
        ),
        "es": re.compile(
            r"\b(?:reembols|devoluci[óo]n|devolv|cambiar|cambio|reintegr|dinero.*(?:dev|atr[áa]s)|"
            r"quiero.*dinero|cancelar.*pedido|roto|partido|da[ñn]ado|defect|equivoc|"
            r"no me.*(?:queda|sirve|vale))\b",
            re.I,
        ),
        "id": re.compile(
            r"\b(?:retur|refund|pengembalian|tukar|kembali(?:kan|in)?|uang.*(?:kembali|balik)|"
            r"batal|kirim.*balik|balikin|rusak|cacat|pecah|salah.*(?:barang|kirim)|"
            r"ga.*(?:muat|cocok)|tidak.*(?:muat|cocok))\b",
            re.I,
        ),
    },
    "product": {
        "en": re.compile(
            r"\b(?:product|batter|charg|power|waterproof|water[- ]?resist|water|shower|"
            r"fits?|sized?|colou?r|material|weight|dimension|specification|spec|compatib|"
            r"work.*with|iphone|android|last.*(?:hour|hr|long)|how (?:big|small|large|long)|manual)\b",
            re.I,
        ),
        "es": re.compile(
            r"\b(?:producto|bater[íi]a|cargar|carga|impermeable|resistente al agua|agua|ducha|"
            r"talla|talle|color|material|peso|dimensi[óo]n|especificaci[óo]n|compatible|funciona|"
            r"iphone|dura[n]?.*(?:hora|mucho|poco)|audi[fó]|manual)\b",
            re.I,
        ),
        "id": re.compile(
            r"\b(?:produk|baterai|ngecas|cas|isi daya|tahan air|anti air|air|mandi|ukuran|warna|"
            r"bahan|berat|dimensi|spesifikasi|kompatibel|cocok.*(?:iphone|hp|dengan)|iphone|"
            r"earphone|earbud|tahan.*(?:jam|lama|berapa)|manual)\b",
            re.I,
        ),
    },
}

# 支持语种集合（常量，对齐 TicketState.lang 的 Literal 约束）
_SUPPORTED_LANGS = {"en", "es", "id"}
_SUPPORTED_INTENTS = {"logistics", "return", "product", "other"}


def classify_by_rules(text: str) -> dict | None:
    """规则层：用三语种关键词 + 正则命中语言与意图。

    两级匹配（先语种后意图）：
    1. 语种：按 _LANG_PATTERNS 顺序首命中即确定；全部未命中返回 None。
    2. 意图：在确定的语种下按 logistics → return → product 顺序首命中即确定。

    返回 {"lang": ..., "intent": ..., "confidence": 0.85}
    返回 None → 交由 LLM 层（chat_json + classify_prompt）。

    设计理由：confidence 硬编码为 0.85（关键词匹配不是概率模型），但在阈值 0.7
    之上，足以通过 builder 的 conf<0.7 → risk_gate 路由检查。
    """
    if not text or not text.strip():
        return None

    if _RULE_BYPASS.search(text):
        return None

    # 语种识别：先强特征，再用通用词表首命中。
    detected_lang: str | None = None
    for lang, pat in (*_LANG_STRONG_PATTERNS, *_LANG_PATTERNS):
        if pat.search(text):
            detected_lang = lang
            break
    if detected_lang is None:
        return None  # 无法识别语种 → 交 LLM

    override = _RETURN_OVERRIDES[detected_lang]
    if override.search(text):
        return {"lang": detected_lang, "intent": "return", "confidence": 0.85}

    # 意图识别（首命中）
    for intent in ("logistics", "return", "product"):
        pat = _INTENTS[intent][detected_lang]
        if pat.search(text):
            return {"lang": detected_lang, "intent": intent, "confidence": 0.85}

    # 语种可识别但无明确意图 → 规则层无法判定，交 LLM
    return None


def classify(state: TicketState) -> dict:
    text = state.get("masked_text") or state["raw_text"]

    # 规则层优先：命中即出结果，省一次 LLM 调用
    rule = classify_by_rules(text)
    if rule is not None:
        return {
            "lang": rule["lang"],
            "intent": rule["intent"],
            "intent_confidence": float(rule["confidence"]),
            "intent_method": "rule",
        }

    # LLM 层：作者构造 prompt（classify_prompt 为作者自写，见 services/llm.py）
    from app.services.llm import classify_prompt

    prompt = classify_prompt(text)
    res = chat_json(prompt["system"], prompt["user"])
    # chat_json 返回 LlmResult（非 dict）；失败时降级为 other，避免整条链路崩（风险闸门会拦截）
    if res.error:
        return {
            "lang": "unknown",
            "intent": "other",
            "intent_confidence": 0.0,
            "intent_method": "llm",
        }
    d = res.data
    lang = d.get("lang")
    intent = d.get("intent")
    try:
        confidence = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    # 模型输出属于不可信边界：非法枚举或置信度不能进入 LangGraph 状态。
    # 失败时走 other + 低置信度，让风险闸门接管，而不是猜一个最接近的值。
    if lang not in _SUPPORTED_LANGS or intent not in _SUPPORTED_INTENTS:
        return {
            "lang": "unknown",
            "intent": "other",
            "intent_confidence": 0.0,
            "intent_method": "llm",
        }
    return {
        "lang": lang,
        "intent": intent,
        "intent_confidence": max(0.0, min(1.0, confidence)),
        "intent_method": "llm",
    }
