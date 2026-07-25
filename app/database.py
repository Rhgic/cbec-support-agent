"""SQLAlchemy 引擎与会话。

为什么用同步会话：规格未要求 asyncpg；arq worker 是异步的，但调用同步
DB 代码没有问题。统一用同步可避免 SQLAlchemy/asyncpg 两套心智模型。
后续若需要更高吞吐，再引入 async 不迟。
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# pool_pre_ping 自动剔除失效连接，避免长跑后拿到已断开的 conn
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话，结束自动归还。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
