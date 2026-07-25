"""应用配置（pydantic-settings）。

为什么集中在这里：所有模块都从 get_settings() 取配置，避免在代码里散落
硬编码的连接串 / 密钥 / 阈值。env.py、guardrails、llm 封装都复用同一份。
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 允许从 .env 加载；CI / 容器里用环境变量注入即可
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== 数据库 / 缓存 =====
    # Postgres 存业务数据与知识库原文/元数据；向量索引在 Milvus（见下方 milvus_* 配置）
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/cbec"
    redis_url: str = "redis://localhost:6379/0"

    # ===== DeepSeek（OpenAI 兼容接口）=====
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"  # deepseek-chat 已下线（真跑 400 报错发现）

    # ===== 17TRACK =====
    tracking_api_key: str = ""
    # live：真实调用；cache：只读本地缓存（开发 / 测试），详见 tools/tracking.py
    tracking_mode: str = "cache"

    # ===== 嵌入模型 BGE-m3 =====
    bge_m3_model: str = "BAAI/bge-m3"
    bge_m3_device: str = "cpu"

    # ===== Milvus 向量库 =====
    # 开发默认 Milvus Lite（pymilvus 内置、本地文件、免 docker）；
    # 指向 http://host:19530 即无缝切换到 standalone 集群。
    # ⚠️ 环境变量名必须带 CBEC_ 前缀：pymilvus 会自动读取全局环境变量 MILVUS_URI 作为
    # Config.MILVUS_URI，并按 "http://host:port" 解析——本地文件路径会被判为
    # "Illegal uri" 导致连接失败（只在真跑 worker 时暴露）。
    milvus_uri: str = Field(default="data/milvus_local.db", alias="CBEC_MILVUS_URI")
    milvus_collection: str = Field(default="cbec_chunks", alias="CBEC_MILVUS_COLLECTION")

    # ===== 重排 rerank（P1：两阶段检索 = 向量粗筛 → 交叉编码精排）=====
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    recall_k: int = 20   # 向量召回候选数；rerank 后取 DEFAULT_K

    # ===== 护栏阈值（第 8 节）=====
    rate_limit_per_minute: int = 20
    daily_quota_per_token: int = 500
    breaker_cost_threshold_usd: float = 5.0

    # ===== 应用 =====
    app_env: str = "dev"
    log_level: str = "INFO"
    # 演示端固定 token，规格 1.2 明确不做注册登录
    demo_token: str = "dev-token"

    # ===== PII 加密（stdlib-only 约束，见 app/services/pii.py 说明）=====
    # 依赖清单不含 cryptography，故用 stdlib 派生的密钥做可逆变换；生产应换 AES-GCM
    pii_secret: str = "change-me-in-prod"


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免每次请求重复构造 Settings。"""
    return Settings()
