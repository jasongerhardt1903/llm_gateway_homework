"""Phase 0 自测：测试地基本身必须可靠，否则后续断言不可信。

覆盖两件事：
1. ``FakeClock`` 记录睡眠且不真实等待。
2. mocktransport 能回放 SSE、能按脚本返回错误、能捕获请求体。
"""

from __future__ import annotations

import httpx
import pytest

from tests.support.mock_transport import (
    CapturedRequest,
    client_for,
    openai_sse,
    scripted_transport,
    sse_transport,
)


async def test_fake_clock_records_sleeps_without_waiting(clock):
    """FakeClock 必须记录毫秒数并立即返回。"""
    import time

    started = time.monotonic()
    await clock.sleep(5000)
    await clock.sleep(250)
    elapsed = time.monotonic() - started

    assert clock.sleeps == [5000, 250]
    # 5 秒的睡眠必须几乎瞬时完成，否则测试会真实等待。
    assert elapsed < 0.5


async def test_fake_clock_advances_now(clock):
    """now() 随睡眠推进，保证延迟计算与真实行为一致。"""
    before = clock.now()
    await clock.sleep(1000)
    assert clock.now() == before + 1.0


async def test_sse_transport_replays_chunks_and_captures_request():
    """mocktransport 回放 SSE 文本，并记录请求体供契约断言。"""
    captured: list[CapturedRequest] = []
    transport = sse_transport(openai_sse(text="hi"), captured=captured)

    async with client_for(transport) as client:
        response = await client.post("/v1/chat/completions", json={"model": "gpt-4o-mini"})

    assert response.status_code == 200
    assert "[DONE]" in response.text
    assert len(captured) == 1
    assert captured[0].json == {"model": "gpt-4o-mini"}
    assert captured[0].method == "POST"


async def test_scripted_transport_returns_errors_in_order():
    """脚本 transport 依次返回超时/429/成功，用于重试场景。"""
    captured: list[CapturedRequest] = []
    transport = scripted_transport(
        [
            httpx.TimeoutException("boom"),
            httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate limited"}),
            httpx.Response(200, json={"ok": True}),
        ],
        captured=captured,
    )

    async with client_for(transport) as client:
        with pytest.raises(httpx.TimeoutException):
            await client.post("/v1/chat/completions", json={"n": 1})
        second = await client.post("/v1/chat/completions", json={"n": 2})
        third = await client.post("/v1/chat/completions", json={"n": 3})

    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
    assert third.status_code == 200
    assert [c.json for c in captured] == [{"n": 1}, {"n": 2}, {"n": 3}]
