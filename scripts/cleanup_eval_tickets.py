"""清理端到端评测产生的工单及其关联轨迹。

默认只预览；传 ``--apply`` 才执行删除。

新评测数据通过 tickets.eval_run_id 精确识别。迁移前的历史残留没有批次标记，只清理同时满足
以下条件的行，避免误删真实待处理工单：
1. status='processing' 且 action IS NULL；
2. raw_text 与当前 tickets_{en,es,id}.jsonl 的标注文本完全一致。
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import AgentRun, PiiVault, Review, Ticket

DATASETS = (
    Path("datasets/tickets_en.jsonl"),
    Path("datasets/tickets_es.jsonl"),
    Path("datasets/tickets_id.jsonl"),
)


def _dataset_texts() -> set[str]:
    texts: set[str] = set()
    for path in DATASETS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.add(json.loads(line)["text"])
    return texts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="确认执行删除；默认仅预览")
    parser.add_argument("--run-id", help="只清理指定 eval_run_id")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="清理迁移前、无 eval_run_id 的历史评测残留",
    )
    args = parser.parse_args()

    if not args.run_id and not args.legacy:
        parser.error("至少指定 --run-id 或 --legacy")

    db = SessionLocal()
    try:
        query = select(Ticket.id)
        if args.run_id:
            query = query.where(Ticket.eval_run_id == args.run_id)
        else:
            query = query.where(
                Ticket.eval_run_id.is_(None),
                Ticket.status == "processing",
                Ticket.action.is_(None),
                Ticket.raw_text.in_(_dataset_texts()),
            )
        ticket_ids = list(db.scalars(query))
        print(f"匹配评测工单：{len(ticket_ids)} 条")
        if not args.apply or not ticket_ids:
            print("未执行删除。" if ticket_ids else "无需清理。")
            return

        # 先删子表，避免外键约束失败；只删除已精确识别出的评测工单关联数据。
        db.execute(delete(AgentRun).where(AgentRun.ticket_id.in_(ticket_ids)))
        db.execute(delete(PiiVault).where(PiiVault.ticket_id.in_(ticket_ids)))
        db.execute(delete(Review).where(Review.ticket_id.in_(ticket_ids)))
        db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
        db.commit()
        print(f"已删除评测工单：{len(ticket_ids)} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
