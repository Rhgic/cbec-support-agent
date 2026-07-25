"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-23

为什么手写迁移而非自动生成：首版要把 pgvector 扩展、vector(1024) 列、HNSW 索引、
category btree 索引一次性建对，自动生成容易漏掉扩展与索引类型。
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # pgvector 扩展必须在创建 vector 列之前启用（单库：业务与向量同库）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_docs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # BGE-m3 输出维度为 1024，与模型严格一致
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["doc_id"], ["knowledge_docs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 先按 category 过滤再做向量排序，这是选用 pgvector 而非 Chroma/faiss 的核心理由
    op.create_index("ix_knowledge_chunks_category", "knowledge_chunks", ["category"])
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=64), nullable=False),
        sa.Column("buyer_ref", sa.Text(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        # 真实运单号（规格 4.3 红线），不建唯一约束因为同一单可能多次出现
        sa.Column("tracking_no", sa.String(length=64), nullable=True),
        sa.Column("carrier", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no"),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # raw_text 仅入库、限制访问；后续处理只用 masked_text
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("masked_text", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(length=8), nullable=True),
        sa.Column("intent", sa.String(length=16), nullable=True),
        sa.Column("intent_confidence", sa.REAL(), nullable=True),
        sa.Column("intent_method", sa.String(length=8), nullable=True),
        sa.Column("retrieval_score", sa.REAL(), nullable=True),
        sa.Column("short_circuited", sa.Boolean(), nullable=True),
        sa.Column("draft_reply", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=8), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=True),
        # 默认 processing，后续节点推进状态机
        sa.Column("status", sa.String(length=16), server_default=sa.text("'processing'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "pii_vault",
        # 单条工单可能含多类 PII，复合主键 (ticket_id, placeholder)
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("placeholder", sa.String(length=32), nullable=False),
        # 加密后的原文，出站前一步才还原
        sa.Column("original", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("ticket_id", "placeholder"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("node", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("token_in", sa.Integer(), nullable=True),
        sa.Column("token_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_level", sa.String(length=8), nullable=False),
        sa.Column("draft_reply", sa.Text(), nullable=False),
        sa.Column("final_reply", sa.Text(), nullable=True),
        sa.Column("reviewer_action", sa.String(length=16), nullable=False),
        # 人工标注的失败原因，用于离线归因（不做自动回流）
        sa.Column("failure_tags", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_table("agent_runs")
    op.drop_table("pii_vault")
    op.drop_table("tickets")
    op.drop_table("orders")
    op.drop_index("ix_knowledge_chunks_embedding", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_category", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_docs")
