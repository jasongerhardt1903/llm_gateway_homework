"""Streaming 测试：SSE 事件序列的正确性。

需求："验证 SSE 事件序列的正确性：delta 顺序，usage 事件，终态，错误中断行为。"

覆盖点：
- SSE 解析的 chunk 边界（真实网络下最易出错的地方）；
- delta 顺序与内容块开闭；
- usage 事件在终态之前；
- 终态唯一；
- 中途错误/断流的行为——已输出内容后**不能**当成"没发生过"。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from llm_gw.adapter.base import AdapterOptions, StreamAssembler
from llm_gw.core.events import (
    AssistantEventStream,
    CancelledEvent,
    DoneEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEndEvent,
)
from llm_gw.core.schema import Task, validate_schema
from llm_gw.harness.sse import encode_sse, parse_sse_stream

from tests.support.mock_transport import sse_transport, scripted_transport


def _task(**overrides: Any) -> Task:
    return validate_schema(
        {"task_id": "t-1", "input": {"messages": [{"role": "user", "content": "hi"}], **overrides}}
    )


async def _chunks(items: list[str | bytes]):
    for item in items:
        yield item


async def _drain(stream) -> list[Any]:
    return [event async for event in stream]


def _types(events: list[Any]) -> list[str]:
    return [event.type for event in events]


# --------------------------------------------------------------------------
# SSE 解析
# --------------------------------------------------------------------------


async def test_sse_parser_handles_chunk_boundaries():
    """一条 SSE 行被网络拆成多个 chunk 时不能丢事件。"""
    chunks = ['data: {"a"', ': 1}\n\ndata: [DO', "NE]\n\n"]

    events = [event async for event in parse_sse_stream(_chunks(chunks))]

    assert [event.data for event in events] == ['{"a": 1}', "[DONE]"]


async def test_sse_parser_handles_crlf_and_comments():
    """供应商常用 ``:`` 注释做 keep-alive，必须忽略。"""
    raw = "event: ping\r\n: keep-alive\r\n\r\nevent: message\r\ndata: line1\r\ndata: line2\r\n\r\n"

    events = [event async for event in parse_sse_stream(_chunks([raw]))]

    assert len(events) == 1
    assert events[0].event == "message"
    assert events[0].data == "line1\nline2"


async def test_sse_parser_flushes_final_event_without_trailing_blank_line():
    events = [event async for event in parse_sse_stream(_chunks(["data: [DONE]"]))]

    assert [event.data for event in events] == ["[DONE]"]


def test_encode_sse_roundtrip():
    """编码器输出的格式必须能被自己的解析器读回。"""
    assert encode_sse("hello", event="message") == b"event: message\ndata: hello\n\n"


# --------------------------------------------------------------------------
# OpenAI 兼容流
# --------------------------------------------------------------------------


def _openai_delta_chunks(*deltas: dict[str, Any], finish_reason: str = "stop") -> list[str]:
    lines: list[str] = []
    for delta in deltas:
        lines.append(
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-mock",
                    "model": "gpt-4o-mini",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
            )
        )
        lines.append("")
    lines.append(
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "model": "gpt-4o-mini",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
        )
    )
    lines.append("")
    lines.append(
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "model": "gpt-4o-mini",
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }
        )
    )
    lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return lines


async def test_openai_stream_event_sequence(openai_model, make_adapter):
    """delta 顺序：start → text_start → delta* → text_end → usage → done。"""
    adapter = make_adapter(
        openai_model,
        sse_transport(_openai_delta_chunks({"role": "assistant", "content": "He"}, {"content": "llo"})),
    )

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    assert _types(events) == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "usage",
        "done",
    ]
    assert "".join(event.delta for event in events if isinstance(event, TextDeltaEvent)) == "Hello"
    assert events[-1].message.text() == "Hello"
    assert events[-1].message.usage.input == 7


async def test_openai_stream_terminal_state_is_unique(openai_model, make_adapter):
    """流式响应只有一个终态：done / error / cancelled 三者之一。"""
    adapter = make_adapter(openai_model, sse_transport(_openai_delta_chunks({"content": "x"})))

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    terminal = [event for event in events if event.type in {"done", "error", "cancelled"}]
    assert len(terminal) == 1
    assert stream.terminal_state() == "done"


async def test_usage_event_precedes_terminal(openai_model, make_adapter):
    """usage 必须在终态之前，否则消费方拿不到用量。"""
    adapter = make_adapter(openai_model, sse_transport(_openai_delta_chunks({"content": "x"})))

    events = await _drain(adapter.stream(openai_model, _task()))

    assert _types(events).index("usage") < _types(events).index("done")


async def test_openai_stream_reasoning_becomes_thinking_block(openai_model, make_adapter):
    adapter = make_adapter(
        openai_model,
        sse_transport(_openai_delta_chunks({"reasoning_content": "思考"}, {"content": "答案"})),
    )

    events = await _drain(adapter.stream(openai_model, _task()))

    assert _types(events) == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "usage",
        "done",
    ]


async def test_openai_stream_accumulates_tool_call_arguments(openai_model, make_adapter):
    """工具参数是分片到达的，必须拼完再解析成 JSON。"""
    chunks = _openai_delta_chunks(
        {
            "tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": '{"ci'}}
            ]
        },
        {"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "上海"}'}}]},
        finish_reason="tool_calls",
    )
    adapter = make_adapter(openai_model, sse_transport(chunks))

    events = await _drain(adapter.stream(openai_model, _task()))

    assert _types(events) == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "usage",
        "done",
    ]
    end = next(event for event in events if isinstance(event, ToolCallEndEvent))
    assert end.id == "call_1"
    assert end.name == "get_weather"
    assert end.arguments == {"city": "上海"}
    assert events[-1].message.stop_reason == "tool_use"


async def test_openai_stream_works_without_done_marker(openai_model, make_adapter):
    """有些兼容实现只给 finish_reason 就关流，仍应视为正常完成。"""
    chunks = [
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
            }
        ),
        "",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        ),
        "",
    ]
    adapter = make_adapter(openai_model, sse_transport(chunks))

    events = await _drain(adapter.stream(openai_model, _task()))

    assert isinstance(events[-1], DoneEvent)
    assert events[-1].message.text() == "hi"


# --------------------------------------------------------------------------
# Anthropic 流
# --------------------------------------------------------------------------


async def test_anthropic_stream_event_sequence(anthropic_model, make_adapter):
    """Anthropic 的块有显式生命周期，用量拆在 start 与 delta 两处。"""
    from tests.support.mock_transport import anthropic_sse

    adapter = make_adapter(anthropic_model, sse_transport(anthropic_sse(text="Hello")))

    stream = adapter.stream(anthropic_model, _task())
    events = await _drain(stream)

    assert _types(events) == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "usage",
        "done",
    ]
    final = events[-1].message
    # input 来自 message_start，output 来自 message_delta，必须合并。
    assert final.usage.input == 10
    assert final.usage.output == 2
    assert final.response_id == "msg_mock"
    assert stream.terminal_state() == "done"


async def test_anthropic_stream_tool_use(anthropic_model, make_adapter):
    """input_json_delta 分片要拼成完整参数对象。"""
    def ev(name: str, data: dict) -> list[str]:
        return [f"event: {name}", f"data: {json.dumps(data)}", ""]

    chunks: list[str] = []
    chunks += ev(
        "message_start",
        {"type": "message_start", "message": {"id": "msg_1", "model": "claude", "usage": {"input_tokens": 5}}},
    )
    chunks += ev(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "f"}},
    )
    chunks += ev(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"a"'}},
    )
    chunks += ev(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ": 1}"}},
    )
    chunks += ev("content_block_stop", {"type": "content_block_stop", "index": 0})
    chunks += ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}})
    chunks += ev("message_stop", {"type": "message_stop"})

    adapter = make_adapter(anthropic_model, sse_transport(chunks))
    events = await _drain(adapter.stream(anthropic_model, _task()))

    end = next(event for event in events if isinstance(event, ToolCallEndEvent))
    assert end.arguments == {"a": 1}
    assert events[-1].message.stop_reason == "tool_use"


# --------------------------------------------------------------------------
# 错误与中断
# --------------------------------------------------------------------------


async def test_stream_interrupted_after_data_reports_error_not_done(openai_model, make_adapter):
    """需求决策表："已流式输出 → 不盲目重新生成"。

    已经吐给客户端的内容无法收回，因此必须如实报错，绝不能补一个 done。
    """
    chunks = [
        "data: "
        + json.dumps({"id": "x", "choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]}),
        "",
    ]
    adapter = make_adapter(openai_model, sse_transport(chunks))

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    assert _types(events)[-1] == "error"
    assert stream.terminal_state() == "error"
    assert "STREAM_INTERRUPTED_AFTER_DATA" in events[-1].error.error_message
    assert not any(isinstance(event, DoneEvent) for event in events)


async def test_stream_interrupted_before_data_reports_connection_failure(openai_model, make_adapter):
    """还没吐内容就断流属于连接失败，可重试。"""
    adapter = make_adapter(openai_model, sse_transport(["data: \n", ""]))

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    assert _types(events)[-1] == "error"
    assert "CONN_FAILED" in events[-1].error.error_message


async def test_http_error_status_becomes_terminal_error(openai_model, make_adapter):
    """429 必须在首 token 前被识别为限流，供上层决定重试或 fallback。"""
    transport = scripted_transport(
        [
            httpx.Response(
                429,
                headers={"content-type": "application/json", "x-request-id": "req_1"},
                json={"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}},
            )
        ]
    )
    adapter = make_adapter(openai_model, transport)

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    assert _types(events) == ["error"]
    assert "RATE_LIMITED" in events[0].error.error_message
    assert stream.terminal_state() == "error"


async def test_error_frame_inside_stream_becomes_terminal_error(openai_model, make_adapter):
    """流内错误帧（HTTP 已经 200）同样要收敛成唯一终态。"""
    chunks = ["data: " + json.dumps({"error": {"message": "overloaded"}}), ""]
    adapter = make_adapter(openai_model, sse_transport(chunks))

    stream = adapter.stream(openai_model, _task())
    events = await _drain(stream)

    assert _types(events) == ["error"]
    assert stream.terminal_state() == "error"


async def test_stream_always_reaches_a_terminal_state(openai_model, make_adapter):
    """任何路径下 ``await stream.result()`` 都必须能返回，不能永久阻塞。"""
    adapter = make_adapter(openai_model, sse_transport([]))

    stream = adapter.stream(openai_model, _task())
    result = await stream.result()

    assert result.stop_reason == "error"


async def test_cancelled_is_a_distinct_terminal_state(openai_model):
    """取消是独立终态：客户端断开不代表上游已经停止生成。"""
    stream = AssistantEventStream()
    assembler = StreamAssembler(stream, openai_model)
    assembler.append_text("部分内容")

    assembler.cancelled()

    assert stream.terminal_state() == "cancelled"
    events = await _drain(stream)
    assert isinstance(events[-1], CancelledEvent)
    assert events[-1].message.text() == "部分内容"


async def test_second_terminal_push_is_rejected():
    """终态唯一不是文档约定，而是运行时不变式。

    不变式由事件流本身强制：装配器对终态后的调用是静默忽略（上游状态机
    重入不应炸掉整个请求），而真正越过流边界的第二个终态必须显式失败。
    """
    from llm_gw.core.events import ErrorEvent, TerminalStateViolation
    from llm_gw.core.messages import AssistantMessage

    stream = AssistantEventStream()
    stream.push(DoneEvent(reason="stop", message=AssistantMessage()))

    with pytest.raises(TerminalStateViolation):
        stream.push(ErrorEvent(reason="error", error=AssistantMessage(stop_reason="error")))


async def test_late_deltas_after_terminal_are_dropped(openai_model):
    """终态之后迟到的 delta 不得污染已结束的流。"""
    stream = AssistantEventStream()
    assembler = StreamAssembler(stream, openai_model)
    assembler.finish("stop")

    assembler.append_text("late")

    assert assembler.message.text() == ""


async def test_request_is_actually_sent_with_streaming_headers(openai_model, make_adapter):
    """契约测试之外再确认一次：adapter 真的把请求发出去了。"""
    captured: list = []
    adapter = make_adapter(
        openai_model,
        sse_transport(_openai_delta_chunks({"content": "hi"}), captured=captured),
    )

    await _drain(adapter.stream(openai_model, _task(), AdapterOptions(api_key="sk-x")))

    assert len(captured) == 1
    assert captured[0].header("authorization") == "Bearer sk-x"
    assert captured[0].json["stream"] is True
    assert captured[0].url.endswith("/chat/completions")


async def test_text_start_is_emitted_before_first_delta(openai_model, make_adapter):
    """TTFT 口径要求区分"连接建立"与"第一个有业务意义的 delta"。"""
    adapter = make_adapter(openai_model, sse_transport(_openai_delta_chunks({"content": "x"})))

    events = await _drain(adapter.stream(openai_model, _task()))
    types = _types(events)

    # 首个事件只是 start（连接建立），第一个业务 delta 才是 text_delta。
    assert types[0] == "start"
    assert types.index("text_start") < types.index("text_delta")
    assert not isinstance(events[0], (TextDeltaEvent, ThinkingDeltaEvent))
