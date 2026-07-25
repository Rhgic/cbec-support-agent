"""护栏单测（规格 8）+ Redis 宕机降级（规格 12 / 15）。

§12 明确要求：停掉 Redis 后各接口仍返回 200。本文件用 BrokenRedis 模拟宕机，
断言所有护栏函数按下表降级（放行并告警，方向刻意与风险闸门相反）：
    check_rate_limit / check_quota / check_breaker → True（放行）
    get_cache → None（不命中，走正常流程）
    set_cache / add_cost → 静默成功，不抛
"""
from datetime import datetime

from app.services import guardrails


def test_rate_limit_allows_under_threshold(fake_redis):
    assert guardrails.check_rate_limit("1.2.3.4") is True
    # 反复调用仍放行（阈值默认 20/分钟，远未到）
    for _ in range(5):
        assert guardrails.check_rate_limit("1.2.3.4") is True


def test_rate_limit_blocks_over_threshold(fake_redis):
    ip = "9.9.9.9"
    # 把当前分钟计数预置到阈值临界，下一次即超限
    key = f"rl:{ip}:{guardrails._today()}-{datetime.now().minute}"
    fake_redis.data[key] = guardrails.settings.rate_limit_per_minute
    assert guardrails.check_rate_limit(ip) is False


def test_quota_allows(fake_redis):
    assert guardrails.check_quota("tok-a") is True


def test_quota_blocks_when_exhausted(fake_redis):
    key = f"quota:tok-b:{guardrails._today()}"
    fake_redis.data[key] = guardrails.settings.daily_quota_per_token
    assert guardrails.check_quota("tok-b") is False


def test_breaker_allows_and_blocks(fake_redis):
    assert guardrails.check_breaker() is True
    fake_redis.data[f"breaker:cost:{guardrails._today()}"] = (
        guardrails.settings.breaker_cost_threshold_usd + 1.0
    )
    assert guardrails.check_breaker() is False


def test_cache_roundtrip(fake_redis):
    assert guardrails.get_cache("masked", "en") is None
    guardrails.set_cache("masked", "en", "reply text")
    assert guardrails.get_cache("masked", "en") == "reply text"
    # 不同语种 key 隔离
    assert guardrails.get_cache("masked", "es") is None


# ===== Redis 宕机降级（§12）=====
def test_redis_down_rate_limit_passes(broken_redis):
    assert guardrails.check_rate_limit("1.1.1.1") is True


def test_redis_down_quota_passes(broken_redis):
    assert guardrails.check_quota("tok-x") is True


def test_redis_down_breaker_passes(broken_redis):
    # 成本未知，宁可放行，避免误伤正常流量（与风险闸门相反方向）
    assert guardrails.check_breaker() is True


def test_redis_down_cache_miss(broken_redis):
    assert guardrails.get_cache("masked", "en") is None


def test_redis_down_writes_silent(broken_redis):
    # 不应抛异常，降级为静默跳过
    guardrails.set_cache("masked", "en", "x")
    guardrails.add_cost(0.01)


def test_redis_down_cache_key_stable():
    # cache_key 是纯函数，宕机与否一致，便于排查
    assert guardrails.cache_key("m", "en") == "cache:" + __import__("hashlib").sha256(b"m|en").hexdigest()
