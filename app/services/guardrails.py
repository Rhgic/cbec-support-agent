"""护栏：限流 / 配额 / 熔断 / 结果缓存，全部基于 Redis。

降级方向（刻意与风险闸门相反，规格 6 / 15）：Redis 不可用时**放行并告警**，因为护栏
失效的代价只是多花钱；而风险闸门失效的代价是资损，故风险闸门必须默认拦截。
"""
import hashlib
from datetime import date, datetime

import app.deps
from app.config import get_settings

settings = get_settings()


def _today() -> str:
    return date.today().isoformat()




def _safe(fn, default):
    """Redis 不可用时返回 default（放行）并告警，降级优先于保护。"""
    try:
        return fn()
    except Exception:
        # 这里不打印堆栈，避免日志刷屏；生产应接告警通道
        return default


# ===== 限流：IP 滑动窗口（按分钟）=====
def check_rate_limit(ip: str) -> bool:
    """True=放行。超限返回 False（调用方回 429 + Retry-After）。"""
    return _safe(
        lambda: _rate_ok(ip),
        True,
    )


def _rate_ok(ip: str) -> bool:
    key = f"rl:{ip}:{_today()}-{datetime.now().minute}"
    pipe = app.deps.redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)
    count = pipe.execute()[0]
    return count <= settings.rate_limit_per_minute


# ===== 日配额：按 token =====
def check_quota(token: str) -> bool:
    """True=放行。超限返回 False（调用方回 429）。"""
    return _safe(lambda: _quota_ok(token), True)


def _quota_ok(token: str) -> bool:
    key = f"quota:{token}:{_today()}"
    count = app.deps.redis_client.incr(key)
    app.deps.redis_client.expire(key, 86400)
    return count <= settings.daily_quota_per_token


# ===== 全局熔断：LLM 日累计成本超阈值 → 拒绝新任务 =====
def add_cost(usd: float) -> None:
    """累加当日 LLM 成本（被 llm.py 调用）。Redis 不可用时静默放行。"""
    _safe(lambda: app.deps.redis_client.incrbyfloat(f"breaker:cost:{_today()}", float(usd)), None)


def check_breaker() -> bool:
    """True=放行。成本超阈值返回 False（调用方回 503）。"""
    return _safe(lambda: _breaker_ok(), True)


def _breaker_ok() -> bool:
    cost = float(app.deps.redis_client.get(f"breaker:cost:{_today()}") or 0.0)
    return cost < settings.breaker_cost_threshold_usd


# ===== 结果缓存：相同脱敏文本 + 语种直接返回，不计费 =====
def cache_key(masked_text: str, lang: str) -> str:
    return "cache:" + hashlib.sha256(f"{masked_text}|{lang}".encode()).hexdigest()


def get_cache(masked_text: str, lang: str) -> str | None:
    return _safe(lambda: app.deps.redis_client.get(cache_key(masked_text, lang)), None)


def set_cache(masked_text: str, lang: str, value: str) -> None:
    _safe(lambda: app.deps.redis_client.set(cache_key(masked_text, lang), value), None)
