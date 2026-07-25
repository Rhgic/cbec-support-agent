"""工具节点单测，重点是「问题需不需要订单事实」的判定（方案 B）。

这条规则是自动解决率与资损风险之间的定价点，必须双向测：
  - 通用政策问题被豁免（否则自动解决率被锁死，规则白加）
  - 指向具体订单的问题**仍然**被拦（否则拿通用政策答具体订单，正是方案 A 的代价）
用例全部取自 datasets/tickets_{en,es,id}.jsonl 的真实工单文本，不自造顺手的例子——
自造例子只能证明正则匹配了自己，证明不了它在真实口语/缩写/混合语种上站得住。
"""
import pytest

from app.graph.nodes import risk_gate, tools

# 通用政策问题：知识库可完整回答，不需要任何订单数据 → 应豁免
GENERAL = [
    "how long does shipping to canada usually take",
    "how long till the refund actually shows up",
    "do i have to pay for return shipping or",
    "how do i get my money back for a broken item",
    "can i swap this for a bigger size",
    "cuanto tarda el envio a españa",
    "cuanto tarda en aparecer el reembolso",
    "tengo que pagar yo el envio de devolucion",
    "como pido el reembolso vino roto",
    "puedo cambiarlo por una talla mas grande",
    "pengiriman ke surabaya berapa lama",
    "refund berapa lama baru masuk",
    "ongkir retur ditanggung siapa nih",
    "gimana cara refund barangnya datang rusak",
    "bisa tukar ukuran yang lebih besar ga",
]

# 指向「我那一单」：没有订单号就答不了，必须转人工索要单号 → 应保持拦截
SPECIFIC = [
    "where is my order?? its been like 2 weeks now",
    "still havent gotten anything and i ordered on the 3rd",
    "tracking hasnt moved in 6 days whats going on",
    "it says delivered but i got nothing lol",
    "is my order even shipped yet",
    "package stuck in customs for a week already",
    "when will it get here im leaving town friday",
    "my stuff still isnt here",
    "you guys sent the wrong thing, gotta send it back",
    "changed my mind can i cancel and send it back",
    "hola donde esta mi pedido ya pague hace rato",
    "no me llega el paquete y ya pasaron 2 semanas",
    "el seguimiento no se actualiza hace dias",
    "dice entregado pero no recibi nada",
    "ya salio mi pedido o todavia no",
    "mi paquete lleva una semana en aduana",
    "mi compra sigue sin llegar",
    "me mandaron el articulo equivocado hay que devolverlo",
    "min pesanan saya blm sampe udh lama bgt",
    "kok resi ga update update ya",
    "udh 2 minggu barang blm nyampe",
    "statusnya terkirim tapi barangnya ga ada",
    "pesanan saya udh dikirim blm sih",
    "paket nyangkut di bea cukai udh seminggu",
    "barang saya belum datang juga nih",
    "dikirimnya salah barang harus dibalikin",
    "berubah pikiran bisa batal sama retur ga",
]


@pytest.mark.parametrize("text", GENERAL)
def test_general_policy_does_not_need_order_facts(text):
    assert tools._needs_order_facts(text) is False, f"通用政策问题被误判为需要订单事实：{text}"


@pytest.mark.parametrize("text", SPECIFIC)
def test_specific_order_question_needs_order_facts(text):
    assert tools._needs_order_facts(text) is True, f"具体订单问题被误豁免（会拿政策答具体单）：{text}"


def test_unknown_text_defaults_to_needing_order_facts():
    """判不准时必须落在保守侧：多转一次人工，而不是拿通用政策去答具体订单。"""
    assert tools._needs_order_facts("asdfgh qwerty 随便一句看不懂的话") is True


def test_specific_signal_wins_over_policy_phrasing():
    """既像政策提问又指向具体订单时，具体订单优先。

    「me arrepenti puedo cancelar y devolver」含 puedo devolver（像问政策），
    但 cancelar（取消）必须知道订单状态——顺序调换就会误豁免这一类。
    """
    assert tools._needs_order_facts("me arrepenti puedo cancelar y devolver") is True


@pytest.mark.parametrize("text", GENERAL + SPECIFIC)
def test_plain_words_are_not_mistaken_for_order_numbers(text):
    """回归：普通单词不得被当成订单号。

    原正则 `[A-Za-z][A-Za-z0-9]{5,21}` 会匹配任何 6 字母以上的词——57 条真实
    logistics/return 工单里 52 条命中假订单号（shipping / delivered / refund / ongkir …），
    再拿它去查订单库必然查不到 → 记 tool_errors → 强制转人工。
    这是「通用政策问题被过度拦截」的真实主路径，比「没提取到订单号」影响大一个量级。
    """
    assert tools._extract_order_no(text) is None, f"普通词被误判为订单号：{text}"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我的订单号是 CBEC202400001 一直没动", "CBEC202400001"),
        ("tracking SF1234567890 hasnt updated", "SF1234567890"),
        ("order CBEC202400042 please check", "CBEC202400042"),
    ],
)
def test_real_order_numbers_still_extracted(text, expected):
    """收窄不能矫枉过正：真实形态的订单/运单号仍须提取得到。"""
    assert tools._extract_order_no(text) == expected


def _run(monkeypatch, text: str, intent: str) -> dict:
    monkeypatch.setattr(tools, "_restore", lambda _state: text)
    return tools.tools({"ticket_id": 1, "intent": intent, "masked_text": text})


def test_product_intent_calls_no_tools(monkeypatch):
    out = _run(monkeypatch, "the earbuds only last like 3 hrs is that normal", "product")
    assert out["tool_errors"] == []
    assert out["tool_results"] == {}


def test_general_policy_without_order_no_records_no_error(monkeypatch):
    """方案 B 的正向半边：不记 tool_errors，故不触发风险规则 4。"""
    out = _run(monkeypatch, "how long does shipping to canada usually take", "logistics")
    assert out["tool_errors"] == []
    assert "tools_skipped" in out["tool_results"]


def test_specific_question_without_order_no_still_errors(monkeypatch):
    """方案 B 的安全半边：仍然记错误 → 规则 4 强制人工。"""
    out = _run(monkeypatch, "where is my order?? its been like 2 weeks now", "logistics")
    assert out["tool_errors"], "指向具体订单却无单号，必须记错误转人工"


def test_skipped_tools_do_not_trigger_risk_rule_4(monkeypatch):
    """端到端契约：豁免路径产出的 state 不得被风险闸门规则 4 拦下。

    直接断言到 risk_gate，因为「不记 tool_errors」只有在规则 4 不再命中时才有意义。
    """
    out = _run(monkeypatch, "ongkir retur ditanggung siapa nih", "return")
    state = {
        "intent": "return",
        "intent_confidence": 0.9,
        "short_circuited": False,
        "tool_errors": out["tool_errors"],
        "draft_reply": "Biaya pengiriman retur ditanggung pembeli.",
        "lang": "id",
    }
    assert risk_gate.evaluate_risk_rules(state) is None
