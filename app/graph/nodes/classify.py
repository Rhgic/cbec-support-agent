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

    # 语种识别（首命中）
    detected_lang: str | None = None
    for lang, pat in _LANG_PATTERNS:
        if pat.search(text):
            detected_lang = lang
            break
    if detected_lang is None:
        return None  # 无法识别语种 → 交 LLM

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
    return {
        "lang": d.get("lang", "unknown"),
        "intent": d.get("intent", "other"),
        "intent_confidence": float(d.get("confidence", 0.0)),
        "intent_method": "llm",
    }
