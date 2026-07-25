"""add tracking_cache

Revision ID: 0002_tracking_cache
Revises: 0001_initial
Create Date: 2026-07-24

规格 7.1 的 tracking_cache 表在首版迁移（0001）时尚未纳入，这里补齐。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002_tracking_cache"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "tracking_cache",
        sa.Column("tracking_no", sa.String(length=64), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tracking_no"),
    )


def downgrade() -> None:
    op.drop_table("tracking_cache")
