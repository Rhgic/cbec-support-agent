"""依赖注入：数据库会话与 Redis 客户端。

为什么把 Redis 客户端也放这里：guardrails（第 8 节）与 arq 都依赖 Redis，
统一一个单例客户端，避免各处重复连接。
"""
from collections.abc import Generator

from redis import Redis
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal

settings = get_settings()

# decode_responses=True 让命令返回 str 而非 bytes，省去各处解码。
# ★ 必须设超时：这是「降级优先于保护」能成立的前提——Redis 挂掉时若不设超时，
#   连接会一直阻塞，把整个 HTTP 请求拖死（表现为 /health 与 /tickets 全部超时无响应），
#   护栏反而成了故障放大器。设 1s 超时后，Redis 不可用会快速抛错，
#   由 guardrails 的 try/except 放行并告警（真停 Redis 验证过）。
redis_client: Redis = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
    retry_on_timeout=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：注入数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis() -> Redis:
    """FastAPI 依赖：注入 Redis 客户端。"""
    return redis_client
