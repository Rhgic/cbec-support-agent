"""可观测性：JSON 日志、request_id 贯穿全链路、/metrics 端点。

为什么用 JSON 日志：结构化日志便于后续接入采集系统，request_id 字段让一次
请求在 arq worker、DB、LLM 调用之间都能串起来。
"""
import logging
from contextvars import ContextVar

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)
from pythonjsonlogger import jsonlogger

# 当前请求的 request_id，跨函数传递（同步 / 异步都适用）
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """给每条日志注入当前 request_id。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging(log_level: str = "INFO") -> None:
    """配置根日志为 JSON 格式并附带 request_id。"""
    handler = logging.StreamHandler()
    # 字段顺序：时间、级别、logger 名、request_id、消息
    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s"
    )
    handler.setFormatter(fmt)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)


async def request_id_middleware(request: Request, call_next) -> Response:
    """为每个请求分配 / 透传 request_id，并写回响应头。"""
    rid = request.headers.get("X-Request-ID") or __import__("uuid").uuid4().hex
    token = request_id_ctx.set(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        request_id_ctx.reset(token)


# 独立的 registry，避免与默认全局注册表混淆，便于未来按需扩展
REGISTRY = CollectorRegistry()

# 仅 S1 暴露的依赖健康指标；后续阶段会追加 token、成本、延迟等业务指标
DEPENDENCY_UP = Gauge(
    "dependency_up",
    "依赖可用性（1=可用，0=不可用）",
    ["dependency"],
    registry=REGISTRY,
)
HTTP_REQUEST_TOTAL = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
    registry=REGISTRY,
)


def metrics_endpoint() -> Response:
    """Prometheus 抓取端点。"""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
