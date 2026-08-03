"""把人工审核结果导出为可复核的离线样本，不自动修改正式数据集。

输出：
- reviews.jsonl：完整审核上下文，用于失败归因与回复优化；
- tickets_{lang}.jsonl：仅包含人工明确纠正语种/意图的分类样本；
- risk_labeled.jsonl：仅包含人工明确纠正风险等级的样本。

用法：python -m scripts.export_feedback --out-dir runs/feedback_export
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.models import Review, Ticket


def build_feedback_rows(review: Any, ticket: Any) -> dict[str, list[dict]]:
    """把一条 review + ticket 转成三类候选样本；纯函数便于测试。"""
    masked_text = (ticket.masked_text or "").strip()
    corrected_lang = review.corrected_lang
    corrected_intent = review.corrected_intent
    corrected_risk = review.corrected_risk_level

    rows: dict[str, list[dict]] = {
        "reviews": [
            {
                "review_id": review.id,
                "ticket_id": ticket.id,
                "lang": ticket.lang,
                "masked_text": masked_text,
                "predicted_intent": ticket.intent,
                "predicted_risk": ticket.risk_level,
                "reviewer_action": review.reviewer_action,
                "failure_tags": review.failure_tags or [],
                "draft_reply": review.draft_reply,
                "final_reply": review.final_reply,
                "corrected_lang": corrected_lang,
                "corrected_intent": corrected_intent,
                "corrected_risk_level": corrected_risk,
            }
        ],
        "tickets": [],
        "risk": [],
    }

    # 缺少人工纠正字段时不猜 gold；两者至少一个有值时，用原预测补另一个字段。
    if masked_text and (corrected_lang or corrected_intent):
        gold_lang = corrected_lang or ticket.lang
        gold_intent = corrected_intent or ticket.intent
        if gold_lang in {"en", "es", "id"} and gold_intent in {
            "logistics",
            "return",
            "product",
            "other",
        }:
            rows["tickets"].append(
                {
                    "lang": gold_lang,
                    "text": masked_text,
                    "gold_lang": gold_lang,
                    "gold_intent": gold_intent,
                    "source_review_id": review.id,
                }
            )

    risk_lang = corrected_lang or ticket.lang
    risk_intent = corrected_intent or ticket.intent
    if (
        corrected_risk in {"low", "mid", "high"}
        and risk_lang in {"en", "es", "id"}
        and risk_intent in {"logistics", "return", "product", "other"}
    ):
        rows["risk"].append(
            {
                "lang": risk_lang,
                "draft_reply": review.final_reply or review.draft_reply,
                "intent": risk_intent,
                "intent_confidence": ticket.intent_confidence,
                "short_circuited": bool(ticket.short_circuited),
                "tool_errors": [],
                "gold_risk": corrected_risk,
                "source_review_id": review.id,
            }
        )
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def export_feedback(out_dir: Path, since_review_id: int = 0) -> dict[str, int]:
    grouped: dict[str, list[dict]] = {"reviews": [], "risk": []}
    ticket_rows: dict[str, list[dict]] = {"en": [], "es": [], "id": []}

    db = SessionLocal()
    try:
        pairs = (
            db.query(Review, Ticket)
            .join(Ticket, Ticket.id == Review.ticket_id)
            .filter(Review.id > since_review_id)
            .order_by(Review.id)
            .all()
        )
        for review, ticket in pairs:
            rows = build_feedback_rows(review, ticket)
            grouped["reviews"].extend(rows["reviews"])
            grouped["risk"].extend(rows["risk"])
            for row in rows["tickets"]:
                ticket_rows[row["gold_lang"]].append(row)
    finally:
        db.close()

    _write_jsonl(out_dir / "reviews.jsonl", grouped["reviews"])
    _write_jsonl(out_dir / "risk_labeled.jsonl", grouped["risk"])
    for lang, rows in ticket_rows.items():
        _write_jsonl(out_dir / f"tickets_{lang}.jsonl", rows)

    return {
        "reviews": len(grouped["reviews"]),
        "risk": len(grouped["risk"]),
        "tickets": sum(len(rows) for rows in ticket_rows.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("runs/feedback_export"))
    parser.add_argument("--since-review-id", type=int, default=0)
    args = parser.parse_args()
    counts = export_feedback(args.out_dir, args.since_review_id)
    print(f"导出完成：reviews={counts['reviews']} tickets={counts['tickets']} risk={counts['risk']}")
    print(f"输出目录：{args.out_dir}")
    print("这些文件是候选样本；人工复核后再合并到 datasets/。")


if __name__ == "__main__":
    main()
