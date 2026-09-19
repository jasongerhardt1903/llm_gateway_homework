"""事件流测试：核心不变式是"流式响应只有一个终态"。

对应需求：
- 流式结束只有一个原因：正常完成、错误、或者取消。
- 不能在流中间切换终态类型。
"""

from __future__ import annotations

import pytest

from llm_gw.core.events import (
    AssistantEventStream,
    CancelledEvent,
    DoneEvent,
    ErrorEvent,
    EventStream,
    TerminalStateViolation,
    TextDeltaEvent,
    UsageEvent,
)
from llm_gw.core.messages import AssistantMessage, StopReason, Usage


def _msg(reason: StopReason = "stop") -> AssistantMessage:
    return AssistantMessage(content=[], usage=Usage(), stop_reason=reason)


async def _collect(stream: EventStream) -> list:
    return [event async for event in stream]


async def test_events_are_yielded_in_push_order():
    """事件必须按 push 顺序被消费，不得乱序。"""
    stream: EventStream = AssistantEventStream()
    stream.push(TextDeltaEvent(index=0, delta="a"))
    stream.push(TextDeltaEvent(index=0, delta="b"))
    stream.push(UsageEvent(usage=Usage(input=1, output=2, total_tokens=3)))
    stream.push(DoneEvent(reason="stop", message=_msg()))
    stream.end()

    events = await _collect(stream)
    assert [e.type for e in events] == ["text_delta", "text_delta", "usage", "done"]
    assert [e.delta for e in events[:2]] == ["a", "b"]


async def test_consumer_awaits_events_pushed_later():
    """消费者先于生产者启动时，仍能拿到后续事件（真流式，不是先攒后发）。"""
    stream: EventStream = AssistantEventStream()

    async def producer():
        for chunk in ("he", "llo"):
            stream.push(TextDeltaEvent(index=0, delta=chunk))
        stream.push(DoneEvent(reason="stop", message=_msg()))
        stream.end()

    import asyncio

    task = asyncio.create_task(producer())
    events = await _collect(stream)
    await task

    assert "".join(e.delta for e in events if e.type == "text_delta") == "hello"
    assert events[-1].type == "done"


async def test_terminal_state_is_reached_once_and_result_resolves():
    """终态唯一：done 之后 result() 解析为最终 AssistantMessage。"""
    stream: EventStream = AssistantEventStream()
    final = _msg("stop")
    stream.push(DoneEvent(reason="stop", message=final))
    stream.end()

    await _collect(stream)
    assert await stream.result() is final


async def test_second_terminal_event_is_rejected():
    """不能在流中间切换终态：第二个终态事件必须抛 TerminalStateViolation。"""
    stream: EventStream = AssistantEventStream()
    stream.push(DoneEvent(reason="stop", message=_msg()))

    with pytest.raises(TerminalStateViolation):
        stream.push(ErrorEvent(reason="error", error=_msg("error")))


async def test_error_after_cancelled_is_rejected():
    """取消与错误互斥，同样属于终态切换。"""
    stream: EventStream = AssistantEventStream()
    stream.push(CancelledEvent(reason="aborted", message=_msg("aborted")))

    with pytest.raises(TerminalStateViolation):
        stream.push(ErrorEvent(reason="error", error=_msg("error")))


async def test_non_terminal_events_after_terminal_are_dropped():
    """终态之后的迟到 delta 应被丢弃，不得污染已结束的流。"""
    stream: EventStream = AssistantEventStream()
    stream.push(DoneEvent(reason="stop", message=_msg()))
    stream.push(TextDeltaEvent(index=0, delta="late"))
    stream.end()

    events = await _collect(stream)
    assert [e.type for e in events] == ["done"]


async def test_end_closes_stream_without_terminal_event():
    """end() 用于收尾；未推送终态时流也应正常结束，不阻塞消费者。"""
    stream: EventStream = AssistantEventStream()
    stream.push(TextDeltaEvent(index=0, delta="x"))
    stream.end()

    events = await _collect(stream)
    assert [e.type for e in events] == ["text_delta"]


async def test_error_terminal_extracts_error_message_as_result():
    """错误终态的 result() 解析为错误消息，便于调用方统一处理。"""
    stream: EventStream = AssistantEventStream()
    err = _msg("error")
    stream.push(ErrorEvent(reason="error", error=err))
    stream.end()

    await _collect(stream)
    assert await stream.result() is err
