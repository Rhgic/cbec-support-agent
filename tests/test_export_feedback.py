from types import SimpleNamespace

from scripts.export_feedback import build_feedback_rows


def _ticket(**overrides):
    values = {
        "id": 8,
        "masked_text": "where is [ORDER_1]",
        "lang": "en",
        "intent": "logistics",
        "intent_confidence": 0.82,
        "risk_level": "mid",
        "short_circuited": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _review(**overrides):
    values = {
        "id": 12,
        "reviewer_action": "edited",
        "failure_tags": ["wrong_intent"],
        "draft_reply": "draft",
        "final_reply": "final",
        "corrected_lang": None,
        "corrected_intent": None,
        "corrected_risk_level": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unlabeled_review_does_not_create_gold_samples():
    rows = build_feedback_rows(_review(), _ticket())
    assert len(rows["reviews"]) == 1
    assert rows["tickets"] == []
    assert rows["risk"] == []


def test_explicit_intent_correction_creates_classifier_sample():
    rows = build_feedback_rows(_review(corrected_intent="return"), _ticket())
    assert rows["tickets"] == [
        {
            "lang": "en",
            "text": "where is [ORDER_1]",
            "gold_lang": "en",
            "gold_intent": "return",
            "source_review_id": 12,
        }
    ]


def test_explicit_risk_correction_creates_risk_sample():
    rows = build_feedback_rows(_review(corrected_risk_level="high"), _ticket())
    assert rows["risk"][0]["gold_risk"] == "high"
    assert rows["risk"][0]["draft_reply"] == "final"


def test_raw_text_is_never_exported():
    rows = build_feedback_rows(_review(corrected_intent="return"), _ticket())
    serialized = str(rows)
    assert "raw_text" not in serialized
    assert "[ORDER_1]" in serialized


def test_invalid_predicted_labels_do_not_create_risk_sample():
    rows = build_feedback_rows(
        _review(corrected_risk_level="high"),
        _ticket(lang="unknown", intent=None),
    )
    assert rows["risk"] == []
