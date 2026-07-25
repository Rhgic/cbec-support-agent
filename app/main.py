"""FastAPI 入口：装配中间件与路由。

S1 仅挂载 /health 与 /metrics。后续阶段的 tickets / review / knowledge 路由
会在此追加 include_router。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.observability import (
    configure_logging,
    metrics_endpoint,
    request_id_middleware,
)
from app.routers import health, knowledge, metrics, review, tickets


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="CBEC Support Agent", version="2.0")

    # 演示前端（web/，静态服务在另一端口）需跨域调本 API。
    # 仅放行本地开发来源——不用 "*"，避免把演示配置带到生产。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # request_id 必须最先装配，保证后续日志都有链路标识
    app.middleware("http")(request_id_middleware)
    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"])
    app.include_router(health.router)
    app.include_router(tickets.router)
    app.include_router(review.router)
    app.include_router(knowledge.router)
    app.include_router(metrics.router)
    return app


app = create_app()
