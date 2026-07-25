"""make knowledge_chunks.embedding nullable

Revision ID: 0004_embedding_nullable
Revises: 0003_ticket_citations
Create Date: 2026-07-24

两阶段构建：build_knowledge 先插入 chunk（embedding 留空），embed_knowledge 再填充向量。
0001 把该列建成 NOT NULL，与该流程矛盾（插入即 IntegrityError）。改为可空。
"""

from alembic import op

revision: str = "0004_embedding_nullable"
down_revision: str | None = "0003_ticket_citations"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("knowledge_chunks", "embedding", nullable=True)


def downgrade() -> None:
    # 回退前需保证无 NULL 行，否则 NOT NULL 约束添加失败
    op.alter_column("knowledge_chunks", "embedding", nullable=False)
