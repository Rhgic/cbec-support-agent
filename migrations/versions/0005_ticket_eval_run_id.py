"""add evaluation run marker to tickets

Revision ID: 0005_ticket_eval_run_id
Revises: 0004_embedding_nullable
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ticket_eval_run_id"
down_revision: str | None = "0004_embedding_nullable"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("eval_run_id", sa.String(length=36), nullable=True))
    op.create_index("ix_tickets_eval_run_id", "tickets", ["eval_run_id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_eval_run_id", table_name="tickets")
    op.drop_column("tickets", "eval_run_id")
