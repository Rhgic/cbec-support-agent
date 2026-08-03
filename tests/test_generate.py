"""生成节点单测（规格 5）。

覆盖：LLM 失败兜底 → 升级 high；有检索结果却无引用 → 升级 high；
正常路径返回 reply + citations；未知语种回退 en 兜底模板。
generate_prompt / chat_json 均 mock。
"""
from app.graph.nodes import generate
from app.services.llm import LlmResult, generate_prompt


def _patch_llm(monkeypatch, result: LlmResult):
    # 签名含 customer_text（客户脱敏原话）——曾遗漏该参数导致模型不知道客户问了什么
    monkeypatch.setattr(
        generate, "generate_prompt", lambda cust, r, t, lang: {"system": "s", "user": "u"}
    )
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: result)


def test_fallback_on_error(monkeypatch):
    _patch_llm(monkeypatch, LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0, error="boom"))
    out = generate.generate({"lang": "en", "chunks": [{"content": "c"}]})
    assert out["draft_reply"] == generate.FALLBACK["en"]
    assert out["citations"] == []
    assert out["risk_level"] == "high"


def test_missing_citation_with_chunks_escalates(monkeypatch):
    _patch_llm(
        monkeypatch,
        LlmResult(data={"reply": "no source here"}, token_in=1, token_out=1, cost_usd=0.0),
    )
    out = generate.generate({"lang": "es", "chunks": [{"content": "evidence"}]})
    # 有检索结果但无引用 → 视为无依据，升级 high
    assert out["risk_level"] == "high"
    assert "引用" in out["risk_reasons"][0]


def test_normal_path_returns_reply_and_citations(monkeypatch):
    _patch_llm(
        monkeypatch,
        LlmResult(
            data={
                "reply": "Su pedido llegará pronto",
                "citations": ["https://help.example/1"],
                "support": [
                    {"source_url": "https://help.example/1", "quote": "delivery is on track"}
                ],
            },
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    # chunk 必须带 source_url（真实检索返回一定有）——P2 引用真实性校验要求
    # citations ⊆ 本次检索来源集合，否则视为编造
    out = generate.generate(
        {
            "lang": "es",
            "chunks": [
                {"content": "Current evidence: delivery is on track", "source_url": "https://help.example/1"}
            ],
        }
    )
    assert out["draft_reply"] == "Su pedido llegará pronto"
    assert out["citations"] == ["https://help.example/1"]
    assert "risk_level" not in out  # 正常不强制 high


def test_fabricated_citation_forced_high(monkeypatch):
    """P2：引用了本次检索没返回的来源 → 视为编造，升 high 并剔除假引用。"""
    _patch_llm(
        monkeypatch,
        LlmResult(
            data={"reply": "x", "citations": ["https://help.example/1", "https://made-up.example/9"]},
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = generate.generate(
        {"lang": "en", "chunks": [{"content": "evidence", "source_url": "https://help.example/1"}]}
    )
    assert out["risk_level"] == "high"
    assert out["citations"] == ["https://help.example/1"]  # 假引用被剔除
    assert "编造" in out["risk_reasons"][0]


def test_valid_support_recovers_missing_citation(monkeypatch):
    _patch_llm(
        monkeypatch,
        LlmResult(
            data={
                "reply": "Delivery normally takes 7 days.",
                "citations": [],
                "support": [
                    {"source_url": "file://shipping.md", "quote": "Delivery normally takes 7 days"}
                ],
            },
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = generate.generate(
        {
            "lang": "en",
            "chunks": [
                {
                    "content": "Delivery normally takes 7 days for this route.",
                    "source_url": "file://shipping.md",
                }
            ],
        }
    )
    assert out["citations"] == ["file://shipping.md"]
    assert "risk_level" not in out


def test_citation_without_verbatim_support_forced_high(monkeypatch):
    _patch_llm(
        monkeypatch,
        LlmResult(
            data={
                "reply": "Delivery is fast.",
                "citations": ["file://shipping.md"],
                "support": [
                    {"source_url": "file://shipping.md", "quote": "a sentence not in the source"}
                ],
            },
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = generate.generate(
        {
            "lang": "en",
            "chunks": [{"content": "Real shipping policy.", "source_url": "file://shipping.md"}],
        }
    )
    assert out["risk_level"] == "high"
    assert "可核验" in out["risk_reasons"][0]


def test_number_not_present_in_evidence_forced_high(monkeypatch):
    _patch_llm(
        monkeypatch,
        LlmResult(
            data={
                "reply": "Your refund will arrive in 9 days.",
                "citations": ["file://refund.md"],
                "support": [
                    {"source_url": "file://refund.md", "quote": "Refunds return to the original method"}
                ],
            },
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = generate.generate(
        {
            "lang": "en",
            "chunks": [
                {
                    "content": "Refunds return to the original method.",
                    "source_url": "file://refund.md",
                }
            ],
        }
    )
    assert out["risk_level"] == "high"
    assert "数字事实" in out["risk_reasons"][0]


def test_unknown_lang_falls_back_to_en(monkeypatch):
    _patch_llm(monkeypatch, LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0, error="x"))
    out = generate.generate({"lang": "xx", "chunks": []})
    assert out["draft_reply"] == generate.FALLBACK["en"]


def test_prompt_includes_masked_conversation_context():
    prompt = generate_prompt(
        "Where is my order?",
        "[source: file://faq.md]\nshipping answer",
        "",
        "en",
        "客户此前消息：[ORDER_1] 的物流三天未更新",
    )
    assert "Recent masked conversation history" in prompt["user"]
    assert "[ORDER_1]" in prompt["user"]
    assert "Current customer message" in prompt["user"]
    assert '"support"' in prompt["system"]
    assert "copied verbatim" in prompt["system"]
