"""Adapter Contract Test：请求翻译与响应翻译是否符合协议。

需求："测试每个 adapter 的请求翻译和响应翻译是否符合协议，用 mocktransport
驱动，不依赖真实供应商。"

因此这里**只断言翻译结果**（HTTP 方法/URL/请求头/请求体字段、统一消息字段），
不关心网络细节。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm_gw.adapter.base import AdapterOptions
from llm_gw.adapter.factory import create_adapter
from llm_gw.adapter.presets.registry import all_models, find_model, provider_choices
from llm_gw.core.schema import Task, validate_schema

from tests.support.mock_transport import sse_transport


def _task(**input_overrides: Any) -> Task:
    payload: dict[str, Any] = {
        "task_id": "t-1",
        "input": {"messages": [{"role": "user", "content": "hi"}], **input_overrides},
    }
    return validate_schema(payload)


def _body(request) -> dict[str, Any]:
    return json.loads(request.content)


# --------------------------------------------------------------------------
# OpenAI 兼容协议
# --------------------------------------------------------------------------


def test_openai_request_translation(openai_model, make_adapter):
    """统一 task → OpenAI chat.completions 请求体。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    task = _task(
        system="be brief",
        max_tokens=256,
        temperature=0.2,
        tools=[{"name": "lookup", "description": "查资料", "parameters": {"type": "object"}}],
        response_schema={"type": "object", "properties": {"a": {"type": "integer"}}},
    )

    request = adapter.build_request(openai_model, task, AdapterOptions(api_key="sk-test"))
    body = _body(request)

    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert request.headers["accept"] == "text/event-stream"

    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is True
    # 流式必须显式索取 usage，否则无法计费。
    assert body["stream_options"] == {"include_usage": True}
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0.2
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "lookup"
    # 结构化输出第一层保证：请求时带 JSON schema。
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True


def test_openai_request_omits_stream_options_when_not_streaming(openai_model, make_adapter):
    adapter = make_adapter(openai_model, sse_transport([]))

    body = _body(adapter.build_request(openai_model, _task(), stream=False))

    assert body["stream"] is False
    assert "stream_options" not in body


def test_deepseek_degrades_response_format_to_json_object(deepseek_model, make_adapter):
    """能力注册表驱动降级：DeepSeek 不支持严格 JSON Schema，只能退回 json_object。"""
    adapter = make_adapter(deepseek_model, sse_transport([]))
    task = _task(response_schema={"type": "object", "properties": {"a": {"type": "integer"}}})

    request = adapter.build_request(deepseek_model, task)

    assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
    assert _body(request)["response_format"] == {"type": "json_object"}


def test_openai_response_format_alias_json_object_is_forwarded(openai_model, make_adapter):
    """OpenAI 形态的 response_format 别名 → json_object 原样透传给上游。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    task = _task(response_format={"type": "json_object"})

    body = _body(adapter.build_request(openai_model, task))

    assert body["response_format"] == {"type": "json_object"}


def test_openai_response_format_alias_json_schema_becomes_strict_schema(openai_model, make_adapter):
    """别名里的 json_schema 归一化后，与直接用 response_schema 完全等价。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    task = _task(
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "weather", "schema": {"type": "object"}},
        }
    )

    body = _body(adapter.build_request(openai_model, task))

    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert body["response_format"]["json_schema"]["strict"] is True


def test_deepseek_json_object_mode_is_not_downgraded(deepseek_model, make_adapter):
    """json_object 模式本来就只要求合法 JSON，DeepSeek 无需"降级"即可满足。"""
    adapter = make_adapter(deepseek_model, sse_transport([]))
    task = _task(response_format={"type": "json_object"})

    assert _body(adapter.build_request(deepseek_model, task))["response_format"] == {
        "type": "json_object"
    }


def test_openai_translates_tool_history(openai_model, make_adapter):
    """assistant 的 tool_call 与 tool 结果必须翻译成协议要求的成对结构。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    task = _task(
        messages=[
            {"role": "user", "content": "上海天气？"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我查一下"},
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "get_weather",
                        "arguments": {"city": "上海"},
                    },
                ],
            },
            {
                "role": "tool",
                "content": [{"type": "tool_result", "tool_call_id": "call_1", "content": "晴"}],
            },
        ]
    )

    messages = _body(adapter.build_request(openai_model, task))["messages"]

    assistant = messages[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "我查一下"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"city": "上海"}
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "晴"}


def test_openai_translates_image_blocks_to_data_url(openai_model, make_adapter):
    adapter = make_adapter(openai_model, sse_transport([]))
    task = _task(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是什么"},
                    {"type": "image", "data": "AAAA", "mime_type": "image/png"},
                ],
            }
        ]
    )

    content = _body(adapter.build_request(openai_model, task))["messages"][0]["content"]

    assert content[0] == {"type": "text", "text": "这是什么"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_openai_parse_response(openai_model, make_adapter):
    """非流式响应 → 统一消息。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    body = {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
    }

    message = adapter.parse_response(body, openai_model)

    assert message.response_id == "chatcmpl-1"
    assert message.text() == "hello"
    assert message.stop_reason == "tool_use"
    assert message.tool_calls()[0].arguments == {"a": 1}
    assert message.usage.input == 10
    assert message.usage.cache_read == 4
    assert message.usage.reasoning == 1


def test_openai_maps_content_filter_to_error_stop_reason(openai_model, make_adapter):
    """内容被过滤是拒答，不能当成正常完成。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    body = {
        "id": "chatcmpl-2",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"}],
    }

    message = adapter.parse_response(body, openai_model)

    assert message.stop_reason == "error"


# --------------------------------------------------------------------------
# Anthropic Messages 协议
# --------------------------------------------------------------------------


def test_anthropic_request_translation(anthropic_model, make_adapter):
    """统一 task → Anthropic Messages 请求体（system 提升到顶层）。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    task = _task(
        system="顶层 system",
        messages=[
            {"role": "system", "content": "消息里的 system"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.3,
        tools=[{"name": "lookup", "description": "查资料", "parameters": {"type": "object"}}],
    )

    request = adapter.build_request(anthropic_model, task, AdapterOptions(api_key="sk-ant"))
    body = _body(request)

    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant"
    assert request.headers["anthropic-version"] == "2023-06-01"
    # Anthropic 的 system 不在 messages 里。
    assert body["system"] == "顶层 system\n\n消息里的 system"
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["temperature"] == 0.3
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    assert body["tool_choice"] == {"type": "auto"}


def test_anthropic_always_sends_max_tokens(anthropic_model, make_adapter):
    """Anthropic 缺 max_tokens 会直接 400，必须有兜底值。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))

    body = _body(adapter.build_request(anthropic_model, _task()))

    assert body["max_tokens"] == anthropic_model.max_tokens


def test_anthropic_translates_tool_history(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    task = _task(
        messages=[
            {"role": "user", "content": "上海天气？"},
            {
                "role": "assistant",
                "content": [{"type": "tool_call", "id": "t1", "name": "get_weather", "arguments": {"city": "上海"}}],
            },
            {"role": "tool", "content": [{"type": "tool_result", "tool_call_id": "t1", "content": "晴"}]},
        ]
    )

    messages = _body(adapter.build_request(anthropic_model, task))["messages"]

    assert messages[1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "get_weather",
        "input": {"city": "上海"},
    }
    # tool_result 必须包在 user 轮次里。
    assert messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "晴"}],
    }


def test_anthropic_merges_consecutive_same_role_messages(anthropic_model, make_adapter):
    """Anthropic 要求 user/assistant 交替，相邻同角色必须合并。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    task = _task(
        messages=[
            {"role": "user", "content": "第一句"},
            {"role": "user", "content": "第二句"},
        ]
    )

    messages = _body(adapter.build_request(anthropic_model, task))["messages"]

    assert len(messages) == 1
    assert [block["text"] for block in messages[0]["content"]] == ["第一句", "第二句"]


def test_anthropic_structured_output_uses_prompt_instruction(anthropic_model, make_adapter):
    """Anthropic 没有 response_format，结构化输出只能靠 prompt 约束。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    task = _task(response_schema={"type": "object", "properties": {"a": {"type": "integer"}}})

    body = _body(adapter.build_request(anthropic_model, task))

    assert "response_format" not in body
    assert "JSON Schema" in body["system"]
    assert '"a"' in body["system"]


def test_anthropic_json_object_mode_uses_json_only_instruction(anthropic_model, make_adapter):
    """json_object 模式无 schema 可注入，只能要求"只回合法 JSON"。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    task = _task(response_format={"type": "json_object"})

    body = _body(adapter.build_request(anthropic_model, task))

    assert "response_format" not in body
    assert "valid JSON" in body["system"]
    assert "JSON Schema" not in body["system"]


def test_anthropic_parse_response(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    body = {
        "id": "msg_1",
        "model": "claude-3-5-haiku-20241022",
        "content": [
            {"type": "thinking", "thinking": "想一想"},
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}},
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 3,
        },
    }

    message = adapter.parse_response(body, anthropic_model)

    assert message.response_id == "msg_1"
    assert message.stop_reason == "tool_use"
    assert message.text() == "hi"
    assert message.tool_calls()[0].arguments == {"a": 1}
    assert message.usage.cache_read == 2
    assert message.usage.cache_write == 3


def test_anthropic_maps_refusal_to_error_stop_reason(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    body = {"id": "msg_2", "content": [], "stop_reason": "refusal"}

    assert adapter.parse_response(body, anthropic_model).stop_reason == "error"


# --------------------------------------------------------------------------
# preset / 工厂
# --------------------------------------------------------------------------


def test_deepseek_is_openai_compatible_preset_not_separate_protocol():
    """DeepSeek 是 preset 而非独立协议——与参考项目 pi 的结论一致。"""
    model = find_model("deepseek", "deepseek-flash")

    assert model is not None
    assert model.api == "openai-completions"
    assert model.base_url.startswith("https://api.deepseek.com")


def test_deepseek_v4_pro_declares_no_vision_support():
    """能力注册表要如实反映供应商限制，否则路由会把带图任务派给它。

    官方文档里 ``deepseek-v4-pro`` 明确不支持视觉，``deepseek-flash`` 支持；
    这是两个型号唯一的能力差异，用它同时固化"能力必须按型号分别声明"。
    """
    pro = find_model("deepseek", "deepseek-v4-pro")
    flash = find_model("deepseek", "deepseek-flash")

    assert pro.capabilities.vision is False
    assert flash.capabilities.vision is True
    assert pro.capabilities.tools is True


def test_all_preset_models_resolve_to_a_known_protocol():
    for model in all_models():
        adapter = create_adapter(model.api)
        assert adapter.api == model.api


def test_unknown_api_is_rejected():
    with pytest.raises(ValueError):
        create_adapter("no-such-protocol")


def test_provider_choices_expose_dropdown_options():
    """Web 供应商下拉菜单直接消费这个列表。"""
    choices = {choice["provider"] for choice in provider_choices()}

    assert {"openai", "deepseek", "anthropic"} <= choices
