"""测试公共夹具。

为什么需要 fake Redis：护栏（第 8 节）强依赖 Redis，但单测不应起一个真 Redis。
这里用内存实现覆盖 app.deps.redis_client——这正是当初把护栏改成引用
app.deps.redis_client（而非直接 import）的原因：可在测试里整体替换。
BrokenRedis 模拟「Redis 宕机」，用于验证 §12 要求的降级（停 Redis 各接口仍 200）。
"""
from __future__ import annotations

import pytest

from app import deps


class FakeRedis:
    """最小可用 Redis 桩：只实现护栏用到的命令语义。"""

    def __init__(self) -> None:
        self.data: dict[str, float | str] = {}

    def incr(self, key: str) -> int:
        self.data[key] = int(self.data.get(key, 0)) + 1
        return int(self.data[key])

    def incrbyfloat(self, key: str, amount: float) -> float:
        self.data[key] = float(self.data.get(key, 0.0)) + float(amount)
        return float(self.data[key])

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True

    def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002 — 桩不实现 TTL
        return True

    def pipeline(self) -> _Pipe:
        return _Pipe(self)

    def today_key(self, prefix: str) -> str:
        """对齐 guardrails 的按日 key 前缀（仅供测试预置数据用）。"""
        from datetime import date

        return f"{prefix}:{date.today().isoformat()}"


class _Pipe:
    def __init__(self, redis: FakeRedis) -> None:
        self._r = redis
        self._ops: list[tuple] = []

    def incr(self, key: str) -> _Pipe:
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int) -> _Pipe:  # noqa: ARG002
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self) -> list:
        out: list = []
        for op in self._ops:
            if op[0] == "incr":
                out.append(self._r.incr(op[1]))
            # expire 不改变执行结果列表
        self._ops = []
        return out


class BrokenRedis:
    """每个命令都抛连接异常，模拟 Redis 不可用。"""

    @staticmethod
    def _boom(*_args, **_kwargs):
        from redis.exceptions import ConnectionError

        raise ConnectionError("simulated redis down")

    def __getattr__(self, _name):
        return self._boom

    def pipeline(self) -> _BrokenPipe:
        return _BrokenPipe()


class _BrokenPipe:
    def __getattr__(self, _name):
        def _boom(*_a, **_k):
            from redis.exceptions import ConnectionError

            raise ConnectionError("simulated redis down")

        return _boom

    def execute(self):
        from redis.exceptions import ConnectionError

        raise ConnectionError("simulated redis down")


@pytest.fixture
def fake_redis():
    """给护栏一个可用的内存 Redis。"""
    r = FakeRedis()
    old = deps.redis_client
    deps.redis_client = r
    yield r
    deps.redis_client = old


@pytest.fixture
def broken_redis():
    """模拟 Redis 宕机；降级应放行。"""
    b = BrokenRedis()
    old = deps.redis_client
    deps.redis_client = b  # type: ignore[assignment]
    yield b
    deps.redis_client = old
