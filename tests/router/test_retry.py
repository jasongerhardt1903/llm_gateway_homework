"""重试测试：断言 clock.sleeps 而非真实等待。

需求要求"测试的时候使用 mocktransport 模拟 LLM 的输出"，且重试测试**不能真的
sleep**。这里的关键机制是注入 :class:`FakeClock`：它把每次退避睡眠记进
``sleeps`` 并立即返回，因此重试测试**零真实等待**。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.core.errors import is_retryable_assistant_error
from llm_gw.core.messages import AssistantMessage
from llm_gw.harness.retry import (
    RetryCallbacks,
    RetryPolicy,
    StreamingRetryGuard,
    retry_assistant_call,
    retry_delay_ms,
    retry_provider_request,
)
from llm_gw.util.clock import FakeClock


def _err(text: str) -> AssistantMessage:
    return AssistantMessage(stop_reason="error", error_message=text)


def _ok() -> AssistantMessage:
    return AssistantMessage()


def _policy(**overrides) -> RetryPolicy:
    params = {"enabled": True, "max_retries": 3, "base_delay_ms": 500, "jitter_ratio": 0.0}
    params.update(overrides)
    return RetryPolicy(**params)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --------------------------------------------------------------------------
# 退避序列
# --------------------------------------------------------------------------


def test_retry_delay_doubles_per_attempt():
    policy = _policy(base_delay_ms=500)
    assert retry_delay_ms(policy, 1) == 500
    assert retry_delay_ms(policy, 2) == 1000
    assert retry_delay_ms(policy, 3) == 2000


def test_retry_delay_is_capped_at_max_delay_ms():
    policy = _policy(base_delay_ms=500, max_delay_ms=1500)
    assert retry_delay_ms(policy, 3) == 1500
    assert retry_delay_ms(policy, 4) == 1500


def test_retry_delay_jitter_stays_within_ratio():
    policy = _policy(base_delay_ms=1000, jitter_ratio=0.25)
    for value in (0.0, 0.5, 0.999):
        delay = retry_delay_ms(policy, 1, rng=lambda value=value: value)
        assert 750 <= delay <= 1250


# --------------------------------------------------------------------------
# retry_assistant_call（语义层）
# --------------------------------------------------------------------------


async def test_success_returns_immediately_without_sleep(clock):
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        return _ok()

    result = await retry_assistant_call(produce, _policy(), clock)

    assert calls == 1
    assert result.stop_reason == "pending"
    assert clock.sleeps == []


async def test_retries_transient_error_with_backoff(clock):
    """前两次瞬时错误，第三次成功；退避序列为 500、1000。"""
    attempts: list[int] = []

    async def produce():
        attempts.append(1)
        if len(attempts) < 3:
            return _err("upstream timeout, connection reset")
        return _ok()

    result = await retry_assistant_call(produce, _policy(), clock)

    assert result.stop_reason == "pending"
    assert len(attempts) == 3
    assert clock.sleeps == [500, 1000]


async def test_non_retryable_error_fails_fast(clock):
    """配额/账单类确定性错误不做任何重试。"""
    attempts: list[int] = []

    async def produce():
        attempts.append(1)
        return _err("insufficient_quota")

    result = await retry_assistant_call(produce, _policy(), clock)

    assert result.stop_reason == "error"
    assert len(attempts) == 1
    assert clock.sleeps == []


async def test_aborted_is_never_retried(clock):
    attempts: list[int] = []

    async def produce():
        attempts.append(1)
        return AssistantMessage(stop_reason="aborted")

    await retry_assistant_call(produce, _policy(), clock)

    assert len(attempts) == 1
    assert clock.sleeps == []


async def test_disabled_policy_returns_first_response(clock):
    attempts: list[int] = []

    async def produce():
        attempts.append(1)
        return _err("timeout")

    result = await retry_assistant_call(produce, _policy(enabled=False), clock)

    assert len(attempts) == 1
    assert result.stop_reason == "error"
    assert clock.sleeps == []


async def test_exhausted_retries_returns_last_error(clock):
    attempts: list[int] = []

    async def produce():
        attempts.append(1)
        return _err("connection refused")

    result = await retry_assistant_call(produce, _policy(max_retries=2), clock)

    assert result.stop_reason == "error"
    assert len(attempts) == 3  # 初始 + 2 次重试
    assert clock.sleeps == [500, 1000]


async def test_retry_callbacks_report_schedule_and_finish(clock):
    events: list[tuple] = []

    async def produce():
        if not [e for e in events if e[0] == "call"]:
            events.append(("call",))
            return _err("overloaded")
        events.append(("call",))
        return _ok()

    callbacks = RetryCallbacks(
        on_retry_scheduled=lambda attempt, max_attempts, delay, message: events.append(
            ("scheduled", attempt, max_attempts, delay)
        ),
        on_retry_attempt_start=lambda: events.append(("start",)),
        on_retry_finished=lambda success, attempt, error: events.append(("finished", success, attempt)),
    )

    await retry_assistant_call(produce, _policy(), clock, callbacks=callbacks)

    assert ("scheduled", 1, 3, 500.0) in events
    assert ("start",) in events
    assert ("finished", True, 1) in events


async def test_cancel_during_backoff_normalizes_to_aborted(clock):
    """退避期间被取消 → 归一为 aborted 消息，调用方无需区分取消时机。"""
    state = {"cancelled": False}

    async def produce():
        return _err("timeout")

    def after_schedule(*_args):
        state["cancelled"] = True

    callbacks = RetryCallbacks(on_retry_scheduled=after_schedule)

    result = await retry_assistant_call(
        produce,
        _policy(),
        clock,
        callbacks=callbacks,
        is_cancelled=lambda: state["cancelled"],
    )

    assert result.stop_reason == "aborted"
    assert clock.sleeps == []  # 取消发生在睡眠之前，不产生等待


# --------------------------------------------------------------------------
# is_retryable_assistant_error（分类）
# --------------------------------------------------------------------------


def test_classifies_transient_as_retryable():
    assert is_retryable_assistant_error(_err("503 service unavailable"))
    assert is_retryable_assistant_error(_err("connection reset by peer"))
    assert is_retryable_assistant_error(_err("upstream timeout"))


def test_classifies_quota_and_auth_as_non_retryable():
    assert not is_retryable_assistant_error(_err("insufficient_quota"))
    assert not is_retryable_assistant_error(_err("billing limit reached"))
    assert not is_retryable_assistant_error(_err("401 invalid api key"))


def test_non_error_terminal_is_not_retryable():
    assert not is_retryable_assistant_error(AssistantMessage())
    assert not is_retryable_assistant_error(AssistantMessage(stop_reason="tool_use"))


# --------------------------------------------------------------------------
# retry_provider_request（连接层）
# --------------------------------------------------------------------------


def _status_error(status: int, *, headers: dict | None = None) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        f"{status} error",
        request=httpx.Request("POST", "https://mock.local/x"),
        response=httpx.Response(status, headers=headers or {}, text="{}"),
    )


async def test_provider_request_retries_on_retryable_status(clock):
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(503)
        return "ok"

    result = await retry_provider_request(fn, _policy(), clock)

    assert result == "ok"
    assert calls["n"] == 3
    assert clock.sleeps == [500, 1000]


async def test_provider_request_respects_retry_after_header(clock):
    async def fn():
        raise _status_error(429, headers={"retry-after-ms": "300"})

    with pytest.raises(httpx.HTTPStatusError):
        await retry_provider_request(fn, _policy(max_retries=1), clock)

    assert clock.sleeps == [300]  # 优先采用供应商给出的延迟


async def test_provider_request_does_not_retry_non_retryable(clock):
    async def fn():
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_provider_request(fn, _policy(), clock)

    assert clock.sleeps == []


# --------------------------------------------------------------------------
# StreamingRetryGuard：已流式输出后不盲目重生成
# --------------------------------------------------------------------------


def test_guard_allows_retry_before_first_delta():
    assert StreamingRetryGuard().can_retry is True


def test_guard_blocks_retry_after_first_delta():
    guard = StreamingRetryGuard()
    guard.record_delta("Hello")
    assert guard.can_retry is False


def test_guard_tracks_bytes_and_last_chunk():
    guard = StreamingRetryGuard(request_id="req-1")
    guard.record_delta("你")
    guard.record_delta("好")

    assert guard.first_delta_sent is True
    assert guard.bytes_sent == 6  # 两个中文各 3 字节
    assert guard.last_chunk == "好"

    snap = guard.snapshot()
    assert snap["request_id"] == "req-1"
    assert snap["bytes_sent"] == 6


def test_guard_records_error():
    guard = StreamingRetryGuard()
    guard.record_error("upstream closed")
    assert guard.error == "upstream closed"