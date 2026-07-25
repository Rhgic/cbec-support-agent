"""健康检查：返回各依赖状态。

依赖：PostgreSQL、Redis、17TRACK 额度。
- PG / Redis：真实探活（能连上 / 能 ping 通才算 ok）
- 17TRACK：S1 仅做配置项存在性检查；真实额度轮询留到工具层（tools/tracking.py）
  因为查额度要走具体 API，且属于业务调用，不应混在纯探活里
"""
from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_db, get_redis
from app.observability import DEPENDENCY_UP

router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    settings = get_settings()
    deps: dict[str, dict] = {}

    # PostgreSQL：能执行简单查询即视为可用
    try:
        db.execute(text("SELECT 1"))
        deps["postgres"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 — 健康检查需捕获一切连接异常
        deps["postgres"] = {"status": "down", "detail": str(exc)}

    # Redis：ping 不通即视为不可用
    try:
        redis.ping()
        deps["redis"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        deps["redis"] = {"status": "down", "detail": str(exc)}

    # 17TRACK：S1 仅检查配置项；无密钥不算「down」，仅标记未配置
    deps["tracking_17track"] = {
        "status": "configured" if settings.tracking_api_key else "no_api_key",
    }

    # 任一关键依赖（PG/Redis）不可用即整体 degraded
    overall = (
        "ok"
        if all(d["status"] in ("ok", "configured") for d in deps.values())
        else "degraded"
    )

    # 同步 Prometheus gauge，便于监控面板按依赖维度看可用性
    for name, info in deps.items():
        DEPENDENCY_UP.labels(dependency=name).set(
            1 if info["status"] in ("ok", "configured") else 0
        )

    return {"status": overall, "dependencies": deps}
