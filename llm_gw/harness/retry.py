"""有界退避重试。

移植 pi 的 ``retry.ts`` / ``provider-retry.ts``，并落实需求 Harness 层第 91-104 行
的重试决策表：

- **有界**：``max_retries`` 上限，初始调用不计入重试次数。
- **指数退避**：``base_delay_ms × 2^(attempt-1)``，封顶 ``max_delay_ms``，±25% 抖动
  （抖动用于避免惊群；测试把 ``jitter_ratio`` 设为 0 即可断言精确序列）。
- **可中断**：所有睡眠都走注入的 :class:`Clock`，因此测试**零真实等待**。
- **不盲目重生成**：:class:`StreamingRetryGuard` 记录流式进度，只有尚未吐出
  首个业务 delta 时才允许重试。已经输出过内容还重试会产出重复文本。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, TypeVar

from ..core.errors import is_retryable_assistant_error, is_retryable_provider_error, normalize_provider_error
from ..core.messages import AssistantMessage
from ..util.clock import Clock

__all__ = [
    "DEFAULT_MAX_RETRY_DELAY_MS",
    "RetryPolicy",
    "RetryCallbacks",
    "StreamingRetryGuard",
    "retry_delay_ms",
    "retry_provider_request",
    "retry_assistant_call",
]

#: 单次退避的上限（毫秒）。与 pi 的 ``maxAgentDelayMs`` 一致。
DEFAULT_MAX_RETRY_DELAY_MS = 60_000

T = TypeVar("T")


@dataclass
class RetryPolicy:
    """重试策略。默认 3 次，可由 Web 配置覆盖。"""

    enabled: bool = True
    #: 最大重试次数（0 = 不重试）。初始调用不计入。
    max_retries: int = 3
    #: 退避基数：第 n 次重试的延迟为 ``base_delay_ms × 2^(n-1)``。
    base_delay_ms: float = 500
    #: 单次退避延迟上限。
    max_delay_ms: float = DEFAULT_MAX_RETRY_DELAY_MS
    #: 抖动比例。0 表示不抖动（测试用），生产默认 ±25%。
    jitter_ratio: float = 0.25

    def max_attempts(self) -> int:
        """允许的重试次数；策略关闭时为 0。"""
        return self.max_retries if self.enabled else 0


def retry_delay_ms(
    policy: RetryPolicy,
    attempt: int,
    *,
    rng: Callable[[], float] | None = None,
) -> float:
    """第 ``attempt`` 次重试的退避延迟（毫秒，1-indexed）。

    ``base × 2^(attempt-1)``，先封顶再叠加抖动，避免抖动把延迟顶出上限。
    """
    delay = policy.base_delay_ms * (2 ** max(0, attempt - 1))
    delay = min(delay, policy.max_delay_ms)
    if policy.jitter_ratio > 0:
        random_fn = rng or random.random
        # random() ∈ [0, 1) → 因子 ∈ [1-r, 1+r)
        delay *= 1.0 + policy.jitter_ratio * (2.0 * random_fn() - 1.0)
    return delay


# --------------------------------------------------------------------------
# 供应商请求重试（连接层）
# --------------------------------------------------------------------------


def _retry_after_ms(exc: BaseException) -> float | None:
    """解析供应商主动给出的重试延迟。

    依次尝试 ``retry-after-ms``（毫秒）、``retry-after``（秒或 HTTP-date）。
    解析失败返回 None，由调用方回退到指数退避。
    """
    normalized = normalize_provider_error(exc)

    raw_ms = normalized.header("retry-after-ms")
    if raw_ms:
        try:
            return max(0.0, float(raw_ms))
        except ValueError:
            pass

    raw = normalized.header("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw) * 1000.0)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return max(0.0, (parsed.timestamp() - time.time()) * 1000.0)


async def retry_provider_request(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy | None,
    clock: Clock,
    *,
    max_retry_delay_ms: float | None = None,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """执行 ``fn``，对可重试的连接层错误做有界退避重试。

    不可重试的错误（认证失败、配额耗尽、参数非法）**立即抛出**，不做无谓等待。
    """
    max_attempts = policy.max_attempts() if policy is not None else 0
    attempt = 0

    while True:
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 分类后决定是否重试
            if attempt >= max_attempts or not is_retryable_provider_error(exc):
                raise
            attempt += 1
            delay = _retry_after_ms(exc)
            if delay is None:
                delay = retry_delay_ms(policy, attempt)  # type: ignore[arg-type]
            if max_retry_delay_ms is not None:
                delay = min(delay, max_retry_delay_ms)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            await clock.sleep(delay)


# --------------------------------------------------------------------------
# 助手调用重试（语义层）
# --------------------------------------------------------------------------


@dataclass
class RetryCallbacks:
    """重试过程中的回调，供落库与可观测性使用。"""

    #: 每次退避睡眠**之前**触发（attempt 从 1 开始）。
    on_retry_scheduled: Callable[[int, int, float, str], Awaitable[None] | None] | None = None
    #: 退避睡眠结束、重试调用开始**之前**触发。
    on_retry_attempt_start: Callable[[], Awaitable[None] | None] | None = None
    #: 循环结束时触发一次；success 表示后续某次调用正常完成。
    on_retry_finished: Callable[[bool, int, str | None], Awaitable[None] | None] | None = None


async def _notify(callback, *args) -> None:
    """回调可能是同步或异步的，统一处理。"""
    if callback is None:
        return
    result = callback(*args)
    if asyncio.iscoroutine(result):
        await result


async def retry_assistant_call(
    produce: Callable[[], Awaitable[AssistantMessage]],
    policy: RetryPolicy | None,
    clock: Clock,
    *,
    callbacks: RetryCallbacks | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    is_retryable: Callable[[AssistantMessage], bool] | None = None,
) -> AssistantMessage:
    """执行一次"产出助手消息"的调用，对瞬时错误做有界重试。

    - 成功 / 取消：立即返回，取消（``stop_reason == "aborted"``）**永不重试**。
    - 不可重试的错误（配额、账单、认证）：立即返回，让确定性失败快速失败。
    - 其余错误：最多重试 ``max_retries`` 次，指数退避。
    - 退避期间被取消：归一为 ``aborted`` 消息，调用方无需关心取消发生在哪一步。

    ``is_retryable`` 允许调用方注入判定谓词。默认走 :func:`is_retryable_assistant_error`
    的正则分类；路由层会注入基于处置决策表（``should_auto_retry``）的谓词，让决策表
    成为"能不能重试"的唯一真源，避免正则与决策表各判一次导致漂移。
    """
    retryable_fn = is_retryable or is_retryable_assistant_error
    max_attempts = policy.max_attempts() if policy is not None else 0
    attempt = 0
    last_retry: int | None = None

    while True:
        response = await produce()

        if response.stop_reason == "aborted":
            if last_retry is not None:
                await _notify(callbacks.on_retry_finished if callbacks else None, False, last_retry, None)
            return response

        if response.stop_reason != "error":
            if last_retry is not None:
                await _notify(callbacks.on_retry_finished if callbacks else None, True, last_retry, None)
            return response

        if attempt >= max_attempts or not retryable_fn(response):
            if last_retry is not None:
                await _notify(
                    callbacks.on_retry_finished if callbacks else None,
                    False,
                    last_retry,
                    response.error_message,
                )
            return response

        attempt += 1
        last_retry = attempt
        delay = retry_delay_ms(policy, attempt)  # type: ignore[arg-type]
        await _notify(
            callbacks.on_retry_scheduled if callbacks else None,
            attempt,
            max_attempts,
            delay,
            response.error_message or "Unknown error",
        )

        # 退避前先看是否已被取消；取消发生在睡眠期间等价于取消发生在调用期间，
        # 统一归一为 aborted，避免调用方区分两种取消时机。
        if is_cancelled is not None and is_cancelled():
            await _notify(
                callbacks.on_retry_finished if callbacks else None,
                False,
                attempt,
                response.error_message,
            )
            return replace(response, stop_reason="aborted")

        await clock.sleep(delay)
        await _notify(callbacks.on_retry_attempt_start if callbacks else None)


# --------------------------------------------------------------------------
# 流式重试守卫
# --------------------------------------------------------------------------


@dataclass
class StreamingRetryGuard:
    """记录流式输出进度，回答"还能不能重试"。

    需求决策表："已流式输出 → 不盲目重新生成"。只有尚未发出首个业务 delta
    时才允许重试；一旦吐过内容，重试会产生重复文本，因此只能如实报错。
    """

    request_id: str = ""
    first_delta_sent: bool = False
    bytes_sent: int = 0
    last_chunk: str | None = None
    error: str | None = None

    def record_delta(self, delta: str) -> None:
        """记录一段业务 delta。"""
        self.first_delta_sent = True
        self.bytes_sent += len(delta.encode("utf-8"))
        self.last_chunk = delta

    def record_error(self, error: str) -> None:
        self.error = error

    @property
    def can_retry(self) -> bool:
        """尚未输出任何业务内容时才允许重试。"""
        return not self.first_delta_sent

    def snapshot(self) -> dict[str, object]:
        """落库用的进度快照。"""
        return {
            "request_id": self.request_id,
            "first_delta_sent": self.first_delta_sent,
            "bytes_sent": self.bytes_sent,
            "last_chunk": self.last_chunk,
            "error": self.error,
        }
