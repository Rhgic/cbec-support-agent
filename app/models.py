"""ORM 模型定义（全部使用 PostgreSQL）。

为什么集中在一个文件：所有表都依赖同一份 Base.metadata，Alembic 自动迁移与
create_all 都从这里取元信息，拆文件反而要维护 import 顺序。

知识文本与元数据存 PostgreSQL；向量存 Milvus。历史 pgvector 列仅保留为回退路径。
"""
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class KnowledgeDoc(Base):
    """知识库原文（只有中文，靠多语种 embedding 跨语种检索）。"""

    __tablename__ = "knowledge_docs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_url = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    category = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeChunk(Base):
    """切分后的向量片段。检索时先按 category 过滤再做向量排序。"""

    __tablename__ = "knowledge_chunks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    doc_id = Column(BigInteger, ForeignKey("knowledge_docs.id"), nullable=False)
    content = Column(Text, nullable=False)
    # 向量现存于 Milvus（见 services/vectorstore.py）；此列保留为 pgvector 回退路径，
    # 当前 embed_knowledge 不再写入，故 nullable=True。
    embedding = Column(Vector(1024), nullable=True)
    category = Column(String(16), nullable=False)
    token_count = Column(Integer, nullable=False, server_default=sa.text("0"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # category 的 btree 索引是「先过滤再排序」的核心理由，必须由迁移保证存在，
    # 因此放在 __table_args__ 而非依赖应用启动
    __table_args__ = (Index("ix_knowledge_chunks_category", "category"),)


class Order(Base):
    """mock 订单。运单号必须真实可查（规格 4.3 红线）。"""

    __tablename__ = "orders"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_no = Column(String(64), unique=True, nullable=False)
    buyer_ref = Column(Text, nullable=False)
    market = Column(String(8), nullable=False)
    sku = Column(Text, nullable=False)
    product_name = Column(Text, nullable=False)
    qty = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), nullable=False)
    ordered_at = Column(DateTime(timezone=True), nullable=False)
    shipped_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    tracking_no = Column(String(64), nullable=True)
    carrier = Column(String(32), nullable=True)
    status = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TrackingCache(Base):
    """17TRACK 原始返回缓存（规格 7.1）。

    所有 API 原始返回落库；开发/测试环境默认读缓存（TRACKING_MODE=cache），
    不扣真实额度。17TRACK 额度模型：注册扣 1 额度，注册后重复查询不扣，故缓存即可
    反复演示时序轨迹，成本为零。
    """

    __tablename__ = "tracking_cache"

    # 以运单号为主键：同一单只缓存一份，注册去重也以此为据
    tracking_no = Column(String(64), primary_key=True)
    raw_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Ticket(Base):
    """工单主表。raw_text 仅入库、限制访问；后续处理只用 masked_text。"""

    __tablename__ = "tickets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    raw_text = Column(Text, nullable=False)
    masked_text = Column(Text, nullable=True)
    lang = Column(String(8), nullable=True)
    intent = Column(String(16), nullable=True)
    intent_confidence = Column(REAL, nullable=True)
    intent_method = Column(String(8), nullable=True)
    retrieval_score = Column(REAL, nullable=True)
    short_circuited = Column(Boolean, nullable=True)
    draft_reply = Column(Text, nullable=True)
    # 引用来源（source_url 列表）；规格 4.4 未列此列，但前端(§11)要求展示「引用来源」，
    # 故补齐此列以落库，否则生成节点的 citations 无处持久化
    citations = Column(ARRAY(Text), nullable=True)
    risk_level = Column(String(8), nullable=True)
    action = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, server_default=sa.text("'processing'"))
    # 端到端评测批次标记；线上工单为空，便于精确筛选/清理评测数据。
    eval_run_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PiiVault(Base):
    """PII 占位符 ↔ 原文映射。原文加密存储，仅在出站前一步还原。"""

    __tablename__ = "pii_vault"

    # 单条工单可能含多个 PII，故用 (ticket_id, placeholder) 复合主键
    ticket_id = Column(BigInteger, ForeignKey("tickets.id"), primary_key=True)
    placeholder = Column(String(32), primary_key=True)
    original = Column(Text, nullable=False)  # 加密后的原文
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    """节点级执行记录，用于 /tickets/{id}/trace 演示与埋点。"""

    __tablename__ = "agent_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id = Column(BigInteger, ForeignKey("tickets.id"), nullable=False)
    node = Column(String(32), nullable=False)
    latency_ms = Column(Integer, nullable=False)
    token_in = Column(Integer, nullable=True)
    token_out = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    ok = Column(Boolean, nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    """人工审核与修正样本收集。不做自动回流，仅供离线归因。"""

    __tablename__ = "reviews"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id = Column(BigInteger, ForeignKey("tickets.id"), nullable=False)
    risk_level = Column(String(8), nullable=False)
    draft_reply = Column(Text, nullable=False)
    final_reply = Column(Text, nullable=True)
    reviewer_action = Column(String(16), nullable=False)
    # 人工标注的失败原因，用于离线归因
    failure_tags = Column(ARRAY(Text), nullable=True)
    # 人工纠正标签只用于离线评测/规则改进，不在线上自动学习。
    corrected_lang = Column(String(8), nullable=True)
    corrected_intent = Column(String(16), nullable=True)
    corrected_risk_level = Column(String(8), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
