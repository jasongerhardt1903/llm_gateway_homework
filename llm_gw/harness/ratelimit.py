"""按模型独立限流：令牌桶 + 429。

需求「韧性基础」：统一错误码 + 指数退避重试 + **按模型独立限流（超限返回 429）**。

三个设计取舍：

* **令牌桶而非固定窗口**——固定窗口在边界会放过两倍流量（上一窗口末尾与下一窗口
  开头连着打），令牌桶按时间连续补充，没有这个尖峰。补充速率 = ``rpm / 60`` 个/秒，
  桶容量 = 突发上限。
* **按模型各自持桶**——桶的键是模型标签（``provider/id``）。一个模型被限住不影响
  另一个，这正是需求里"独立"二字的含义，也是"两个模型都能正常工作"的前提。
* **在网关入口判定，而不是当作可重试错误**——超限要如实返回 429 + ``Retry-After``，
  而不是丢给重试逻辑：重试只会让桶更空，且把"该等多久"这个信息对调用方藏起来。

时间来自注入的 :class:`Clock`，因此桶的补充过程可被测试精确驱动，不需要真实等待。

配置走环境变量（见 :func:`policy_from_env`）：``LLM_GW_RATE_LIMIT_RPM`` 为 0 时
表示不限额，默认即为不限额——本地开发不该被限流绊住。

版本：0.8.8
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Callable

from ..util.clock import Clock, RealClock

__all__ = [
    "RATE_LIMIT_RPM_ENV",
    "RATE_LIMIT_BURST_ENV",
    "RateLimit",
    "RateLimitDecision",
    "ModelRateLimiter",
    "policy_from_env",
]

#: 全局 RPM 上限（按模型各自计算）。``<=0`` 表示不限额。
RATE_LIMIT_RPM_ENV = "LLM_GW_RATE_LIMIT_RPM"
#: 突发容量：桶最多能攒多少个令牌。缺省取 ``ceil(rpm)``，即"一分钟的量"。
RATE_LIMIT_BURST_ENV = "LLM_GW_RATE_LIMIT_BURST"


@dataclass(frozen=True)
class RateLimit:
    """单个模型的限流参数。"""

    #: 每分钟允许的请求数；``<=0`` 表示不限额。
    rpm: float = 0.0
    #: 桶容量（允许的瞬时突发）。至少为 1，否则任何请求都过不去。
    burst: int = 1

    @property
    def enabled(self) -> bool:
        return self.rpm > 0

    def per_second(self) -> float:
        """令牌补充速率（个/秒）。"""
        return self.rpm / 60.0


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流判定的结果。"""

    allowed: bool
    #: 被限住时建议的等待秒数（已按桶的空缺与补充速率算出，不是拍脑袋的常数）。
    retry_after: float = 0.0
    #: 生效的 RPM，便于写进 429 文案与日志。
    rpm: float = 0.0
    #: 判定后桶内剩余令牌数（向下取整），仅用于可观测。
    remaining: int = 0
    #: 被限住的模型标签。
    model: str = ""

    def retry_after_seconds(self) -> int:
        """``Retry-After`` 响应头的值：向上取整，至少 1 秒。

        HTTP 的 ``Retry-After`` 以整秒表达；给 0 会让调用方立刻重发，等于没有退避。
        """
        return max(1, math.ceil(self.retry_after))


@dataclass
class _Bucket:
    """单个模型的令牌桶。"""

    tokens: float
    updated_at: float = field(default=0.0)


class ModelRateLimiter:
    """按模型标签分桶的令牌桶限流器。

    ``policy_for`` 返回该模型的限流参数；返回 ``None`` 或未启用的参数表示不限额。
    做成回调而不是直接吃一个字典，是为了让"限额从哪来"（环境变量 / 控制台配置 /
    测试注入）与限流算法本身解耦。
    """

    def __init__(
        self,
        policy_for: Callable[[str], RateLimit | None] | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._policy_for = policy_for or (lambda _label: RateLimit())
        self._clock: Clock = clock or RealClock()
        self._buckets: dict[str, _Bucket] = {}

    def policy_for(self, label: str) -> RateLimit:
        return self._policy_for(label) or RateLimit()

    def check(self, label: str) -> RateLimitDecision:
        """尝试取一个令牌。

        首次见到某个模型时桶是满的——否则一个刚配置好的模型会被"从未发生过"的
        历史请求扣成空桶。
        """
        policy = self.policy_for(label)
        if not policy.enabled:
            return RateLimitDecision(allowed=True, model=label)

        now = self._clock.now()
        capacity = float(max(1, policy.burst))
        bucket = self._buckets.get(label)
        if bucket is None:
            bucket = _Bucket(tokens=capacity, updated_at=now)
            self._buckets[label] = bucket
        else:
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * policy.per_second())
            bucket.updated_at = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return RateLimitDecision(
                allowed=True,
                rpm=policy.rpm,
                remaining=int(bucket.tokens),
                model=label,
            )

        # 桶空了：按补充速率算出"再攒够一个令牌要多久"。
        return RateLimitDecision(
            allowed=False,
            retry_after=(1.0 - bucket.tokens) / policy.per_second(),
            rpm=policy.rpm,
            remaining=0,
            model=label,
        )

    def reset(self, label: str | None = None) -> None:
        """清空桶（``label`` 为空时清空全部）。配置变更后调用，避免旧桶残留。"""
        if label is None:
            self._buckets.clear()
        else:
            self._buckets.pop(label, None)

    def snapshot(self) -> dict[str, int]:
        """当前各模型的剩余令牌数（向下取整），供控制台展示。"""
        return {label: int(bucket.tokens) for label, bucket in self._buckets.items()}


def policy_from_env() -> RateLimit:
    """从环境变量读取限流参数。

    对所有模型取**同一组**参数，但各自分桶——因此限流仍然是"按模型独立"的。
    将来若要把额度做成按模型配置，只需换掉传给 :class:`ModelRateLimiter` 的那个
    回调，算法本身不用动。
    """
    rpm = _env_float(RATE_LIMIT_RPM_ENV, 0.0)
    if rpm <= 0:
        return RateLimit()
    burst = _env_float(RATE_LIMIT_BURST_ENV, float(math.ceil(rpm)))
    return RateLimit(rpm=rpm, burst=max(1, int(burst)))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default