"""add tickets.citations

Revision ID: 0003_ticket_citations
Revises: 0002_tracking_cache
Create Date: 2026-07-24

规格 4.4 的 tickets 表未列 citations，但前端(§11)要求展示「引用来源」，生成节点
也产出 citations。补齐该列以持久化引用来源。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0003_ticket_citations"
down_revision: str | None = "0002_tracking_cache"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("citations", ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "citations")
