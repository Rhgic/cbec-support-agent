from unittest.mock import MagicMock

from scripts import eval_e2e


def test_run_one_persists_graph_result_with_eval_marker(monkeypatch):
    create_db = MagicMock()
    persist_db = MagicMock()

    def assign_id(ticket):
        ticket.id = 321

    create_db.add.side_effect = assign_id
    monkeypatch.setattr(
        eval_e2e,
        "SessionLocal",
        MagicMock(side_effect=[create_db, persist_db]),
    )
    persist = MagicMock()
    monkeypatch.setattr(eval_e2e, "apply_ticket_result", persist)
    graph = MagicMock()
    graph.invoke.return_value = {
        "masked_text": "masked",
        "lang": "en",
        "intent": "product",
        "action": "auto_send",
        "risk_level": "low",
        "draft_reply": "reply",
        "citations": ["file://product_faq.md"],
    }

    record = eval_e2e.run_one(
        graph,
        {"text": "question", "gold_lang": "en", "gold_intent": "product"},
        "eval-run-123",
    )

    created_ticket = create_db.add.call_args.args[0]
    assert created_ticket.eval_run_id == "eval-run-123"
    persist.assert_called_once_with(persist_db, 321, graph.invoke.return_value)
    persist_db.commit.assert_called_once()
    assert record["ticket_id"] == 321
    assert record["action"] == "auto_send"
