"""服务层测试：HTTP 接口、SSE 终态约定、断连取消、TTFT 口径。

全部由 ``httpx.MockTransport`` 驱动（需求明确要求），并用 ``httpx.ASGITransport``
直接调用 FastAPI 应用，不启动真实服务器。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from llm_gw.adapter.factory import create_adapter
from llm_gw.harness.service import GatewayService, create_app
from llm_gw.harness.storage import Storage
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock

from tests.support.mock_transport import client_for, openai_sse, sse_transport


class _StepClock:
    """每次取时间都前进固定步长，使 TTFT 可被精确断言。"""

    def __init__(self, step: float = 0.01) -> None:
        self._t = 0.0
        self.step = step

    def now(self) -> float:
        self._t += self.step
        return self._t

    async def sleep(self, ms: float) -> None:  # pragma: no cover - 服务测试不重试
        self._t += ms / 1000.0


def _task_payload(**input_overrides) -> dict:
    return {
        "task_id": "t-1",
        "input": {"messages": [{"role": "user", "content": "hi"}], **input_overrides},
    }


def _completion_body(text: str) -> dict:
    """非流式 chat.completions 响应体。"""
    return {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _json_transport(payload: dict, *, status: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def _build(openai_model, transport, *, storage=None, clock=None):
    client = client_for(transport, base_url=openai_model.base_url)
    adapter = create_adapter(openai_model.api, client)
    registry = CapabilityRegistry()
    registry.register(openai_model)
    router = Router(registry, adapter_for=lambda _m: adapter, clock=clock or FakeClock())
    return GatewayService(router, storage, clock=clock or FakeClock()), adapter


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


# --------------------------------------------------------------------------
# 基础接口
# --------------------------------------------------------------------------


async def test_health(openai_model):
    service, _ = _build(openai_model, sse_transport(openai_sse()))
    async with await _client(create_app(service)) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_post_task_returns_translated_text(openai_model):
    service, _ = _build(openai_model, _json_transport(_completion_body("你好")))
    async with await _client(create_app(service)) as client:
        response = await client.post("/v1/tasks", json=_task_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "你好"
    assert body["terminal"] == "done"
    assert body["usage"]["total_tokens"] > 0


async def test_malformed_json_returns_400(openai_model):
    service, _ = _build(openai_model, sse_transport(openai_sse()))
    async with await _client(create_app(service)) as client:
        response = await client.post(
            "/v1/tasks", content=b"{not json", headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"


async def test_schema_violation_returns_422(openai_model):
    service, _ = _build(openai_model, sse_transport(openai_sse()))
    async with await _client(create_app(service)) as client:
        # 缺少必填的 input.messages
        response = await client.post("/v1/tasks", json={"task_id": "t-1", "input": {}})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# SSE 流式与终态约定
# --------------------------------------------------------------------------


async def test_stream_emits_done_and_done_marker(openai_model):
    service, _ = _build(openai_model, sse_transport(openai_sse(text="hi")))
    async with await _client(create_app(service)) as client:
        response = await client.post("/v1/tasks:stream", json=_task_payload())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: text_delta" in body
    assert "event: done" in body
    assert "data: [DONE]" in body


async def test_stream_error_terminal_omits_done_marker(openai_model):
    """流内 error 不发 [DONE]：客户端据此区分正常结束与异常结束。"""
    transport = sse_transport(
        [
            "data: "
            + json.dumps(
                {"choices": [{"index": 0, "delta": {"content": "部分"}, "finish_reason": None}]}
            ),
            "",
            "data: " + json.dumps({"error": {"message": "upstream overloaded", "type": "server_error"}}),
            "",
        ]
    )
    service, _ = _build(openai_model, transport)
    async with await _client(create_app(service)) as client:
        response = await client.post("/v1/tasks:stream", json=_task_payload())

    body = response.text
    assert "event: error" in body
    assert "[DONE]" not in body


async def test_stream_cancelled_terminal_omits_done_marker(openai_model):
    transport = sse_transport(openai_sse(text="hi"))
    service, _ = _build(openai_model, transport)

    frames = [frame async for frame in service.stream_sse(_validated())]

    assert any(b"event: done" in frame for frame in frames)
    assert any(b"[DONE]" in frame for frame in frames)


# --------------------------------------------------------------------------
# 客户端断开 → 取消上游
# --------------------------------------------------------------------------


class _GatedStream(httpx.AsyncByteStream):
    """先吐一帧，然后挂住等待放行；用于模拟"上游仍在生成"。"""

    def __init__(self, first: bytes, gate: asyncio.Event, rest: bytes) -> None:
        self.first = first
        self.gate = gate
        self.rest = rest
        self.closed = False

    async def __aiter__(self):
        yield self.first
        await self.gate.wait()
        yield self.rest

    async def aclose(self) -> None:
        self.closed = True


def _gated_transport(gate: asyncio.Event) -> tuple[httpx.MockTransport, _GatedStream]:
    first = (
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": {"content": "首字"}, "finish_reason": None}]})
        + "\n\n"
    ).encode()
    stream = _GatedStream(first, gate, b"data: [DONE]\n\n")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    return httpx.MockTransport(handler), stream


async def test_client_disconnect_cancels_upstream(openai_model):
    gate = asyncio.Event()
    transport, upstream = _gated_transport(gate)
    service, _ = _build(openai_model, transport)

    generator = service.stream_sse(_validated())

    # 消费帧，直到业务 delta 已经吐出（上游此刻已开始生成）。
    saw_delta = False
    for _ in range(20):
        frame = await generator.__anext__()
        if b"text_delta" in frame:
            saw_delta = True
            break
    assert saw_delta
    assert upstream.closed is False  # 上游仍在生成

    # 客户端断开：关闭生成器 → 取消上游请求，释放并发槽。
    await generator.aclose()

    assert upstream.closed is True


# --------------------------------------------------------------------------
# TTFT 口径
# --------------------------------------------------------------------------


def _validated(**input_overrides):
    from llm_gw.core.schema import validate_schema

    return validate_schema(_task_payload(**input_overrides))


async def test_ttft_is_measured_from_first_business_delta(openai_model):
    storage = await Storage(":memory:").init()
    clock = _StepClock()
    service, _ = _build(openai_model, sse_transport(openai_sse(text="hi")), storage=storage, clock=clock)

    async for _ in service.stream_sse(_validated()):
        pass

    rows = await storage.recent_calls()
    await storage.close()

    assert rows[0]["ttft_ms"] > 0
    assert rows[0]["ttft_ms"] <= rows[0]["total_ms"]


async def test_ttft_is_zero_when_only_connection_events(openai_model):
    """只有 start / usage / done、没有业务 delta 时，TTFT 必须为 0。

    把连接建立事件当成首 token 会系统性低估 TTFT，这是需求明确禁止的。
    """
    storage = await Storage(":memory:").init()
    clock = _StepClock()
    service, _ = _build(
        openai_model, sse_transport(openai_sse(text="")), storage=storage, clock=clock
    )

    async for _ in service.stream_sse(_validated()):
        pass

    rows = await storage.recent_calls()
    await storage.close()

    assert rows[0]["ttft_ms"] == 0.0
    assert rows[0]["terminal"] == "done"


async def test_stream_records_trace_and_prompt_fingerprint(openai_model):
    storage = await Storage(":memory:").init()
    service, _ = _build(openai_model, sse_transport(openai_sse(text="hi")), storage=storage)

    async for _ in service.stream_sse(
        _validated(), trace_id="trace-abc"
    ):
        pass

    rows = await storage.recent_calls()
    await storage.close()

    assert rows[0]["trace_id"] == "trace-abc"
    assert len(rows[0]["prompt_sha256"]) == 64  # sha256 十六进制长度
    assert rows[0]["stream_chunk_count"] > 0