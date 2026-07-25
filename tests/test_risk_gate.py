"""风险闸门单测（规格 6）。

已实现部分（确定性、必测）：
  - ACTION_MAP 映射
  - 上游已强制 high / 脱敏失败 → 直接 high（代价不对称，降级方向与护栏相反）
  - 规则层命中 high 的透传
  - LLM 层 low/mid 映射 + LLM 异常强制 high

作者自写部分（规格 14：evaluate_risk_rules）已实现，以下为完整单测。
"""
from app.graph.nodes import risk_gate
from app.services.llm import LlmResult


def test_action_map():
    assert risk_gate.ACTION_MAP == {
        "low": "auto_send",
        "mid": "quick_review",
        "high": "human_required",
    }


def test_forced_high_shape():
    r = risk_gate._forced_high(["reason1"])
    assert r == {"risk_level": "high", "risk_reasons": ["reason1"], "action": "human_required"}


def test_upstream_already_high_blocks(monkeypatch):
    # 生成失败已置 high，风险闸门不得二次放行
    state = {"risk_level": "high", "risk_reasons": ["生成失败"]}
    out = risk_gate.risk_gate(state)
    assert out["risk_level"] == "high"
    assert out["action"] == "human_required"
    assert "上游已强制 high" in out["risk_reasons"]


def test_fatal_error_blocks(monkeypatch):
    state = {"fatal_error": "pii_vault 写入失败"}
    out = risk_gate.risk_gate(state)
    assert out["risk_level"] == "high"
    assert out["action"] == "human_required"


def test_rule_layer_high_passthrough(monkeypatch):
    # 规则层返回 high → 透传，不再调 LLM
    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda s: {"level": "high", "reasons": ["金额命中"]})
    monkeypatch.setattr(risk_gate, "risk_prompt", lambda reply: {"system": "", "user": ""})

    calls = {"n": 0}

    def fake_chat(*_a, **_k):
        calls["n"] += 1
        return LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0)

    monkeypatch.setattr(risk_gate, "chat_json", fake_chat)
    out = risk_gate.risk_gate({"draft_reply": "x"})
    assert out["risk_level"] == "high"
    assert out["action"] == "human_required"
    assert calls["n"] == 0  # 规则层命中后不应再问 LLM


def test_llm_low_maps_auto_send(monkeypatch):
    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda s: None)
    monkeypatch.setattr(risk_gate, "risk_prompt", lambda reply: {"system": "", "user": ""})
    monkeypatch.setattr(
        risk_gate,
        "chat_json",
        lambda *a, **k: LlmResult(data={"level": "low", "reasons": []}, token_in=1, token_out=1, cost_usd=0.0),
    )
    out = risk_gate.risk_gate({"draft_reply": "x"})
    assert out["risk_level"] == "low"
    assert out["action"] == "auto_send"


def test_llm_mid_maps_quick_review(monkeypatch):
    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda s: None)
    monkeypatch.setattr(risk_gate, "risk_prompt", lambda reply: {"system": "", "user": ""})
    monkeypatch.setattr(
        risk_gate,
        "chat_json",
        lambda *a, **k: LlmResult(
            data={"level": "mid", "reasons": ["措辞偏软"]},
            token_in=1,
            token_out=1,
            cost_usd=0.0,
        ),
    )
    out = risk_gate.risk_gate({"draft_reply": "x"})
    assert out["risk_level"] == "mid"
    assert out["action"] == "quick_review"


def test_llm_error_forced_high(monkeypatch):
    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda s: None)
    monkeypatch.setattr(risk_gate, "risk_prompt", lambda reply: {"system": "", "user": ""})
    monkeypatch.setattr(
        risk_gate,
        "chat_json",
        lambda *a, **k: LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0, error="timeout"),
    )
    out = risk_gate.risk_gate({"draft_reply": "x"})
    assert out["risk_level"] == "high"
    assert out["action"] == "human_required"


def test_unknown_level_clamped_to_mid(monkeypatch):
    monkeypatch.setattr(risk_gate, "evaluate_risk_rules", lambda s: None)
    monkeypatch.setattr(risk_gate, "risk_prompt", lambda reply: {"system": "", "user": ""})
    monkeypatch.setattr(
        risk_gate,
        "chat_json",
        lambda *a, **k: LlmResult(data={"level": "bogus", "reasons": []}, token_in=1, token_out=1, cost_usd=0.0),
    )
    out = risk_gate.risk_gate({"draft_reply": "x"})
    assert out["risk_level"] == "mid"


def test_rule_layer_contract():
    """规则层契约：六条高风险规则命中 high，无风险交 LLM 层。"""
    from app.graph.nodes.risk_gate import evaluate_risk_rules

    assert evaluate_risk_rules({"intent": "other"}) is not None
    assert evaluate_risk_rules({"intent_confidence": 0.5}) is not None
    assert evaluate_risk_rules({"short_circuited": True}) is not None
    assert evaluate_risk_rules({"tool_errors": ["boom"]}) is not None
    # 规则未命中应放行给 LLM 层
    assert evaluate_risk_rules({"intent": "logistics", "intent_confidence": 0.95}) is None
