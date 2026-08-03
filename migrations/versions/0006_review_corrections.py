"""add structured human correction labels to reviews

Revision ID: 0006_review_corrections
Revises: 0005_ticket_eval_run_id
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006_review_corrections"
down_revision: str | None = "0005_ticket_eval_run_id"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("corrected_lang", sa.String(length=8), nullable=True))
    op.add_column("reviews", sa.Column("corrected_intent", sa.String(length=16), nullable=True))
    op.add_column(
        "reviews", sa.Column("corrected_risk_level", sa.String(length=8), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("reviews", "corrected_risk_level")
    op.drop_column("reviews", "corrected_intent")
    op.drop_column("reviews", "corrected_lang")
