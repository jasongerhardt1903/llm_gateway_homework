"""高级配置项 → 供应商请求体的翻译。

需求第 30 行列出的高级配置项（temperature / top_p / top_k / 思考模式 / 工具调用
轮数）需要在调用时真正作用到请求上。本文件验证：

1. 各协议如何把 :class:`AdvancedConfig` 翻译成自己认识的请求字段；
2. ``thinking_mode`` 三态在两种协议下的映射；
3. 三方优先级：**高级配置 < task 显式指定 < extra_body**。

协议差异（字段名、思考参数的形状）必须留在 adapter 里，因此断言也落在这里。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm_gw.adapter.base import AdapterOptions
from llm_gw.adapter.protocols.anthropic_messages import DEFAULT_THINKING_BUDGET
from llm_gw.core.advanced import AdvancedConfig
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


def test_openai_maps_sampling_params(openai_model, make_adapter):
    adapter = make_adapter(openai_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(temperature=0.4, top_p=0.8, max_tokens=128))

    body = _body(adapter.build_request(openai_model, _task(), options))

    assert body["temperature"] == 0.4
    assert body["top_p"] == 0.8
    assert body["max_tokens"] == 128


def test_openai_omits_absent_sampling_params(openai_model, make_adapter):
    """未配置的项必须完全不出现——传 0 与不传在采样语义上不同。"""
    adapter = make_adapter(openai_model, sse_transport([]))

    body = _body(adapter.build_request(openai_model, _task(), AdapterOptions()))

    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_openai_thinking_on_sends_enable_flag(openai_model, make_adapter):
    """DeepSeek 系用 ``thinking`` 对象开启思考，这是 openai_compat 协议的既定形状。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(thinking_mode="on"))

    body = _body(adapter.build_request(openai_model, _task(), options))

    assert body["thinking"] == {"type": "enabled"}


@pytest.mark.parametrize("mode", ["default", "off"])
def test_openai_thinking_default_and_off_send_nothing(openai_model, make_adapter, mode: str):
    """``default`` 交给供应商默认；``off`` 在 openai_compat 下无标准字段可发，
    因此同样不发送——这一点在 docs/interface.md 中已写明。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(thinking_mode=mode))

    body = _body(adapter.build_request(openai_model, _task(), options))

    assert "thinking" not in body


def test_openai_task_temperature_beats_advanced(openai_model, make_adapter):
    """调用方的逐次意图覆盖模型/模版默认值。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(temperature=0.4))

    body = _body(adapter.build_request(openai_model, _task(temperature=0.9), options))

    assert body["temperature"] == 0.9


def test_openai_extra_body_beats_everything(openai_model, make_adapter):
    """供应商私有字段优先级最高，是运维的最终逃生通道。"""
    adapter = make_adapter(openai_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(temperature=0.4), extra_body={"temperature": 1.5})

    body = _body(adapter.build_request(openai_model, _task(temperature=0.9), options))

    assert body["temperature"] == 1.5


# --------------------------------------------------------------------------
# Anthropic Messages 协议
# --------------------------------------------------------------------------


def test_anthropic_maps_sampling_params(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(temperature=0.4, top_p=0.8, top_k=32))

    body = _body(adapter.build_request(anthropic_model, _task(), options))

    assert body["temperature"] == 0.4
    assert body["top_p"] == 0.8
    assert body["top_k"] == 32


def test_anthropic_thinking_on_sends_budget(anthropic_model, make_adapter):
    """Anthropic 开启思考必须带 ``budget_tokens``，否则请求会被拒。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(thinking_mode="on"))

    body = _body(adapter.build_request(anthropic_model, _task(), options))

    assert body["thinking"] == {"type": "enabled", "budget_tokens": DEFAULT_THINKING_BUDGET}


@pytest.mark.parametrize("mode", ["default", "off"])
def test_anthropic_thinking_default_and_off_send_nothing(anthropic_model, make_adapter, mode: str):
    """Anthropic 没有显式关闭字段：不发送 ``thinking`` 即为关闭。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(thinking_mode=mode))

    body = _body(adapter.build_request(anthropic_model, _task(), options))

    assert "thinking" not in body


def test_anthropic_advanced_max_tokens_beats_model_default(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(max_tokens=777))

    body = _body(adapter.build_request(anthropic_model, _task(), options))

    assert body["max_tokens"] == 777


def test_anthropic_task_max_tokens_beats_advanced(anthropic_model, make_adapter):
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(advanced=AdvancedConfig(max_tokens=777))

    body = _body(adapter.build_request(anthropic_model, _task(max_tokens=64), options))

    assert body["max_tokens"] == 64


def test_thinking_budget_can_be_overridden_via_extra_body(anthropic_model, make_adapter):
    """预算没做成配置项，但运维仍可通过 extra_body 覆盖。"""
    adapter = make_adapter(anthropic_model, sse_transport([]))
    options = AdapterOptions(
        advanced=AdvancedConfig(thinking_mode="on"),
        extra_body={"thinking": {"type": "enabled", "budget_tokens": 4096}},
    )

    body = _body(adapter.build_request(anthropic_model, _task(), options))

    assert body["thinking"]["budget_tokens"] == 4096
