"""分类节点单测（规格 4.2）。

classify 节点两层：规则层 classify_by_rules 优先，未命中走 LLM 层。
本文件测：
  - 规则层关键词契约：高频意图命中，无特征交 LLM
  - 规则命中时直接出结果（省一次 LLM）
  - 规则未命中时走 LLM 层（chat_json 被 mock）
"""
from app.graph.nodes import classify
from app.services.llm import LlmResult


def test_classify_by_rules_returns_none_for_unrecognizable():
    # 无特征文本（无关键词匹配）→ 规则层交 LLM
    assert classify.classify_by_rules("random words without keywords") is None


def test_rule_hit_shortcuts_llm(monkeypatch):
    rule = {"lang": "es", "intent": "return", "confidence": 0.99}
    monkeypatch.setattr(classify, "classify_by_rules", lambda text: rule)

    calls = {"n": 0}

    def fake_chat(*_a, **_k):
        calls["n"] += 1
        return LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0)

    monkeypatch.setattr(classify, "chat_json", fake_chat)
    out = classify.classify({"raw_text": "quiero devolver", "masked_text": "quiero devolver"})
    assert out["lang"] == "es"
    assert out["intent"] == "return"
    assert out["intent_confidence"] == 0.99
    assert out["intent_method"] == "rule"
    assert calls["n"] == 0  # 规则命中，不调 LLM


def test_llm_path_when_rule_misses(monkeypatch):
    from app.services import llm
    from app.services.llm import LlmResult

    monkeypatch.setattr(classify, "classify_by_rules", lambda text: None)
    monkeypatch.setattr(llm, "classify_prompt", lambda text: {"system": "s", "user": "u"})
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(
            data={"lang": "id", "intent": "product", "confidence": 0.88},
            token_in=2,
            token_out=2,
            cost_usd=0.0,
        ),
    )
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(
            data={"lang": "id", "intent": "product", "confidence": 0.88},
            token_in=2,
            token_out=2,
            cost_usd=0.0,
        ),
    )
    out = classify.classify({"raw_text": "where is my order", "masked_text": "where is my order"})
    assert out["lang"] == "id"
    assert out["intent"] == "product"
    assert out["intent_confidence"] == 0.88
    assert out["intent_method"] == "llm"


def test_llm_path_defaults_when_fields_missing(monkeypatch):
    from app.services import llm
    from app.services.llm import LlmResult

    monkeypatch.setattr(classify, "classify_by_rules", lambda text: None)
    monkeypatch.setattr(llm, "classify_prompt", lambda text: {"system": "s", "user": "u"})
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0),
    )
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0),
    )
    out = classify.classify({"raw_text": "???", "masked_text": "???"})
    assert out["lang"] == "unknown"
    assert out["intent"] == "other"
    assert out["intent_confidence"] == 0.0
    assert out["intent_method"] == "llm"


def test_llm_invalid_enum_fails_safe(monkeypatch):
    from app.services import llm

    monkeypatch.setattr(classify, "classify_by_rules", lambda text: None)
    monkeypatch.setattr(llm, "classify_prompt", lambda text: {"system": "s", "user": "u"})
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(
            data={"lang": "zh", "intent": "payment", "confidence": 0.99},
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = classify.classify({"raw_text": "x", "masked_text": "x"})
    assert out["lang"] == "unknown"
    assert out["intent"] == "other"
    assert out["intent_confidence"] == 0.0


def test_llm_confidence_is_clamped(monkeypatch):
    from app.services import llm

    monkeypatch.setattr(classify, "classify_by_rules", lambda text: None)
    monkeypatch.setattr(llm, "classify_prompt", lambda text: {"system": "s", "user": "u"})
    monkeypatch.setattr(
        classify,
        "chat_json",
        lambda *a, **k: LlmResult(
            data={"lang": "en", "intent": "product", "confidence": 3},
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = classify.classify({"raw_text": "x", "masked_text": "x"})
    assert out["intent_confidence"] == 1.0


def test_classify_prompt_contains_boundary_examples():
    from app.services.llm import classify_prompt

    prompt = classify_prompt("ambiguous message")
    assert "exchange this for a larger size" in prompt["system"]
    assert "Gracias por el envío rápido" in prompt["system"]
    assert "Makasih pengirimannya cepat" in prompt["system"]


def test_rule_layer_keywords_contract():
    """规则层关键词契约：高频意图命中，无法判定交 LLM。"""
    from app.graph.nodes.classify import classify_by_rules

    assert classify_by_rules("track my order") is not None
    # 无法判定时返回 None（交 LLM）
    assert classify_by_rules("随机长句无特征") is None


def test_loanwords_do_not_force_indonesian_messages_to_english():
    out = classify.classify_by_rules("gimana cara refund barangnya datang rusak")
    assert out == {"lang": "id", "intent": "return", "confidence": 0.85}
    assert classify.classify_by_rules("help me track my order")["lang"] == "en"


def test_return_phrases_override_shipping_and_size_keywords():
    assert classify.classify_by_rules("do i have to pay for return shipping or")["intent"] == "return"
    assert classify.classify_by_rules("puedo cambiarlo por una talla mas grande")["intent"] == "return"
    assert classify.classify_by_rules("Bisakah produk CBEC202400001 ditukar dengan ukuran lain?")["intent"] == "return"


def test_acknowledgements_and_address_changes_defer_to_llm():
    assert classify.classify_by_rules("thanks for the fast shipping!") is None
    assert classify.classify_by_rules("makasih pengiriman cepet!") is None
    assert classify.classify_by_rules("gracias por el envio rapido!") is None
    assert classify.classify_by_rules("solo queria decir que el producto es excelente") is None
    assert classify.classify_by_rules("can i change the shipping address i just ordered") is None


def test_spanish_return_shipping_is_not_logistics():
    out = classify.classify_by_rules("tengo que pagar yo el envio de devolucion")
    assert out == {"lang": "es", "intent": "return", "confidence": 0.85}
