"""按模型独立限流：令牌桶算法 + 429 出口。

需求「韧性基础」要求"按模型独立限流（超限返回 429）"。这里分两层验证：

* **算法层**（:class:`ModelRateLimiter`）——桶容量、按时间补充、按模型分桶；
  时间由 ``FakeClock`` 精确驱动，不产生真实等待。
* **出口层**（``/v1/tasks`` 与 ``/v1/tasks:stream``）——超限就是 **429 + Retry-After**，
  不是 200 里塞一个 error 事件，也不是被当成可重试错误吞掉。
"""

from __future__ import annotations

import json

import httpx

from llm_gw.adapter.factory import create_adapter
from llm_gw.harness.ratelimit import (
    RATE_LIMIT_BURST_ENV,
    RATE_LIMIT_RPM_ENV,
    ModelRateLimiter,
    RateLimit,
    policy_from_env,
)
from llm_gw.harness.service import GatewayService, create_app
from llm_gw.harness.storage import Storage
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock

from tests.support.mock_transport import client_for, openai_sse, sse_transport


# --------------------------------------------------------------------------
# 算法层
# --------------------------------------------------------------------------


def _limiter(clock: FakeClock, policy: RateLimit) -> ModelRateLimiter:
    return ModelRateLimiter(lambda _label: policy, clock=clock)


def test_bucket_allows_burst_then_denies():
    """桶容量 = 突发上限：头两个放行，第三个被拒且给出等待秒数。"""
    clock = FakeClock()
    limiter = _limiter(clock, RateLimit(rpm=60, burst=2))

    assert [limiter.check("openai/gpt-4o-mini").allowed for _ in range(2)] == [True, True]
    denied = limiter.check("openai/gpt-4o-mini")

    assert denied.allowed is False
    # 60 RPM = 1 个/秒，桶空后攒够一个令牌需要 1 秒。
    assert denied.retry_after == 1.0
    assert denied.retry_after_seconds() == 1
    assert denied.rpm == 60


def test_tokens_refill_over_time():
    """令牌按 RPM/60 连续补充——固定窗口的边界尖峰在这里不存在。"""
    clock = FakeClock()
    limiter = _limiter(clock, RateLimit(rpm=60, burst=1))

    assert limiter.check("m").allowed is True
    assert limiter.check("m").allowed is False
    # 只过 0.5 秒：还不够一个令牌。
    clock._now += 0.5
    assert limiter.check("m").allowed is False
    clock._now += 0.5
    assert limiter.check("m").allowed is True


def test_models_have_independent_buckets():
    """限流粒度是模型标签：A 被限住，B 照样能用。"""
    clock = FakeClock()
    limiter = _limiter(clock, RateLimit(rpm=60, burst=1))

    assert limiter.check("openai/gpt-4o-mini").allowed is True
    assert limiter.check("openai/gpt-4o-mini").allowed is False

    # 另一个模型有自己的桶，不受影响 —— "独立"二字的字面含义。
    assert limiter.check("deepseek/deepseek-flash").allowed is True
    assert limiter.snapshot()["deepseek/deepseek-flash"] == 0


def test_disabled_limit_never_denies():
    """``rpm<=0`` 表示不限额：本地开发不该被限流绊住。"""
    limiter = _limiter(FakeClock(), RateLimit())

    assert [limiter.check("m").allowed for _ in range(50)] == [True] * 50


def test_retry_after_seconds_never_zero():
    """``Retry-After`` 给 0 等于让调用方立刻重发，等于没有退避。"""
    from llm_gw.harness.ratelimit import RateLimitDecision

    assert RateLimitDecision(allowed=False, retry_after=0.01).retry_after_seconds() == 1
    assert RateLimitDecision(allowed=False, retry_after=2.3).retry_after_seconds() == 3


def test_policy_from_env_reads_rpm_and_burst(monkeypatch):
    monkeypatch.setenv(RATE_LIMIT_RPM_ENV, "120")
    monkeypatch.setenv(RATE_LIMIT_BURST_ENV, "5")

    policy = policy_from_env()

    assert policy.rpm == 120
    assert policy.burst == 5
    assert policy.per_second() == 2.0


def test_policy_from_env_defaults_to_unlimited_and_derives_burst(monkeypatch):
    monkeypatch.delenv(RATE_LIMIT_RPM_ENV, raising=False)
    monkeypatch.delenv(RATE_LIMIT_BURST_ENV, raising=False)
    assert policy_from_env().enabled is False

    # 只给 RPM 时，突发容量默认为"一分钟的量"。
    monkeypatch.setenv(RATE_LIMIT_RPM_ENV, "3")
    assert policy_from_env().burst == 3

    # 脏值不能把进程带崩，按不限额处理。
    monkeypatch.setenv(RATE_LIMIT_RPM_ENV, "not-a-number")
    assert policy_from_env().enabled is False


# --------------------------------------------------------------------------
# 出口层
# --------------------------------------------------------------------------


def _service(openai_model, transport, *, limiter, storage=None) -> GatewayService:
    client = client_for(transport, base_url=openai_model.base_url)
    adapter = create_adapter(openai_model.api, client)
    registry = CapabilityRegistry()
    registry.register(openai_model)
    router = Router(registry, adapter_for=lambda _m: adapter, clock=FakeClock())
    return GatewayService(router, storage, clock=FakeClock(), limiter=limiter)


def _payload(task_id: str = "t-rl") -> dict:
    return {"task_id": task_id, "input": {"messages": [{"role": "user", "content": "hi"}]}}


def _json_transport(text: str = "你好") -> httpx.MockTransport:
    body = {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_non_stream_returns_429_with_retry_after(openai_model):
    """超限就是 429，且带上调用方真正需要的信息：还要等多久。"""
    limiter = ModelRateLimiter(lambda _label: RateLimit(rpm=60, burst=1))
    service = _service(openai_model, _json_transport(), limiter=limiter)
    async with await _client(create_app(service)) as client:
        first = await client.post("/v1/tasks", json=_payload())
        second = await client.post("/v1/tasks", json=_payload())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
    detail = second.json()["detail"]
    assert detail["code"] == "RATE_LIMITED"
    assert "openai/gpt-4o-mini" in detail["message"]


async def test_stream_endpoint_is_limited_before_the_stream_starts(openai_model):
    """流式同样在**建流之前**判：状态码必须是 429，而不是 200 里塞 error 事件。"""
    limiter = ModelRateLimiter(lambda _label: RateLimit(rpm=60, burst=1))
    service = _service(openai_model, sse_transport(openai_sse()), limiter=limiter)
    async with await _client(create_app(service)) as client:
        first = await client.post("/v1/tasks:stream", json=_payload())
        second = await client.post("/v1/tasks:stream", json=_payload())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
    assert second.json()["detail"]["code"] == "RATE_LIMITED"


async def test_rate_limited_call_is_recorded_in_exchanges(openai_model):
    """被限流挡下的通讯也要留痕，否则调用方会误以为是上游挂了。"""
    storage = await Storage(":memory:").init()
    limiter = ModelRateLimiter(lambda _label: RateLimit(rpm=60, burst=1))
    service = _service(openai_model, _json_transport(), limiter=limiter, storage=storage)
    try:
        async with await _client(create_app(service)) as client:
            await client.post("/v1/tasks", json=_payload("t-limited"))
            await client.post("/v1/tasks", json=_payload("t-limited"))

        rows = await storage.exchanges_for_task("t-limited")
    finally:
        await storage.close()

    assert [row["status"] for row in rows] == ["done", "error"]
    rejected = rows[-1]
    assert rejected["error_code"] == "RATE_LIMITED"
    assert json.loads(rejected["response_raw"])["detail"]["code"] == "RATE_LIMITED"


async def test_unlimited_by_default(openai_model):
    """默认不限额：连打十次全部 200，限流不会误伤本地开发。"""
    service = _service(openai_model, _json_transport(), limiter=ModelRateLimiter())
    async with await _client(create_app(service)) as client:
        codes = [
            (await client.post("/v1/tasks", json=_payload(f"t-{index}"))).status_code
            for index in range(10)
        ]

    assert codes == [200] * 10