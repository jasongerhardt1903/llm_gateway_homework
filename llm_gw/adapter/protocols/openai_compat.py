"""OpenAI ``chat.completions`` 兼容协议 adapter。

覆盖 OpenAI 官方与所有 OpenAI 兼容供应商（DeepSeek、vLLM、Ollama、多数国产
模型）。这也是为什么 DeepSeek 不需要独立协议 adapter：它只换 baseUrl 与少数
字段（见 ``adapter/presets/deepseek.py``）。
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from ...core.errors import ErrorCode, GatewayError
from ...core.messages import (
    AssistantMessage,
    Model,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)
from ...core.schema import ImageBlock, Task, TaskMessage, TextBlock
from ...harness.sse import SSEEvent
from ..base import Adapter, AdapterOptions, StreamAssembler, map_finish_reason

__all__ = ["OpenAICompatAdapter", "OPENAI_STOP_REASONS"]

OPENAI_STOP_REASONS: dict[str, StopReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    # 内容被过滤：模型拒答，不能靠换模型绕过。
    "content_filter": "error",
}

#: 内容块索引。OpenAI 的 delta 没有块编号，这里用固定编号让装配器正常工作。
_THINKING_INDEX = 0
_TEXT_INDEX = 1
_TOOL_INDEX_BASE = 2


class OpenAICompatAdapter(Adapter):
    """把统一 task 翻译成 OpenAI chat.completions 请求，并把响应翻译回来。"""

    api: ClassVar[str] = "openai-completions"

    #: 开启思考用 ``thinking`` 对象（DeepSeek 系形状）。``off`` 无标准字段可发，
    #: 只能不发送——语义上等价于"跟随供应商默认"，已在 docs/interface.md 说明。
    thinking_body_map: ClassVar[dict[str, dict[str, Any]]] = {
        "on": {"thinking": {"type": "enabled"}},
    }

    # -- 请求翻译 ----------------------------------------------------------

    def build_request(
        self,
        model: Model,
        task: Task,
        options: AdapterOptions | None = None,
        *,
        stream: bool = True,
    ) -> httpx.Request:
        options = options or AdapterOptions()
        body: dict[str, Any] = {
            "model": model.id,
            "messages": self._messages(task),
            "stream": stream,
        }
        if stream:
            # 让上游在最后一个 chunk 里带上 usage；否则流式调用无法计费。
            body["stream_options"] = {"include_usage": True}

        # 优先级：高级配置（模型/模版默认）< task 显式指定 < extra_body。
        # 逐层覆盖，让调用方的单次意图能盖过模型默认值。
        body.update(self.advanced_body(options.advanced))

        # max_tokens 是唯一需要三方比较的字段：模型自身也有默认输出上限，
        # 高级配置要能盖过它、又要被 task 的显式指定盖过。
        max_tokens = task.input.max_tokens or options.advanced.max_tokens or model.max_tokens
        if max_tokens:
            body["max_tokens"] = max_tokens
        if task.input.temperature is not None:
            body["temperature"] = task.input.temperature
        if task.input.tools:
            body["tools"] = [self._tool(tool) for tool in task.input.tools]
            body["tool_choice"] = "auto"
        if task.input.response_schema is not None:
            body["response_format"] = self._response_format(model, task.input.response_schema)
        elif task.input.response_mode() == "json_object":
            # 只要求"返回合法 JSON"、无结构约束：直接透传 OpenAI 的 json_object 模式。
            body["response_format"] = {"type": "json_object"}

        body.update(options.extra_body)

        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream" if stream else "application/json",
        }
        if options.api_key:
            headers["authorization"] = f"Bearer {options.api_key}"
        headers.update(model.headers)
        headers.update(options.headers)

        return httpx.Request(
            "POST",
            f"{model.base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=body,
        )

    def _messages(self, task: Task) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if task.input.system:
            out.append({"role": "system", "content": task.input.system})
        for message in task.input.messages:
            out.extend(self._message(message))
        return out

    def _message(self, message: TaskMessage) -> list[dict[str, Any]]:
        if message.role == "tool":
            return [self._tool_result_message(message)]
        if message.role == "assistant":
            return [self._assistant_message(message)]
        return [{"role": message.role, "content": self._content(message)}]

    def _tool_result_message(self, message: TaskMessage) -> dict[str, Any]:
        results = message.tool_results()
        if len(results) == 1 and message.tool_call_id is None:
            return {"role": "tool", "tool_call_id": results[0].tool_call_id, "content": results[0].content}
        # 一条消息里含多个结果时，OpenAI 要求拆成多条 tool 消息。
        # 这里只取第一个，多个结果由 transform 层预先拆分。
        call_id = message.tool_call_id or (results[0].tool_call_id if results else "")
        return {"role": "tool", "tool_call_id": call_id, "content": message.text()}

    def _assistant_message(self, message: TaskMessage) -> dict[str, Any]:
        text = message.text()
        calls = message.tool_calls()
        payload: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
        return payload

    def _content(self, message: TaskMessage) -> Any:
        """纯文本消息拍平成字符串；含图片时才用内容块数组。"""
        if isinstance(message.content, str):
            return message.content
        if not message.has_image():
            return message.text()
        parts: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append({"type": "text", "text": block.text})
            elif isinstance(block, ImageBlock):
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                    }
                )
        return parts

    def _tool(self, tool) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _response_format(self, model: Model, schema: dict[str, Any]) -> dict[str, Any]:
        """结构化输出第一层保证：请求时带上 JSON schema。

        能力注册表决定形态：支持 ``json_schema`` 的供应商用严格模式；
        只支持 ``json_object`` 的（如 DeepSeek）退回 JSON 模式，靠 prompt
        与第二层本地校验兜住。
        """
        if model.capabilities.json_schema:
            return {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        return {"type": "json_object"}

    # -- 非流式响应翻译 ----------------------------------------------------

    def parse_response(self, body: dict[str, Any], model: Model) -> AssistantMessage:
        message = AssistantMessage(response_id=body.get("id"), response_model=body.get("model"))
        choices = body.get("choices") or []
        if not choices:
            message.stop_reason = "error"
            message.error_message = f"{ErrorCode.REQUEST_INVALID.value}: 响应中没有 choices"
            return message

        choice = choices[0]
        payload = choice.get("message") or {}
        reasoning = payload.get("reasoning_content") or payload.get("reasoning")
        if reasoning:
            message.content.append(ThinkingContent(thinking=reasoning))
        text = payload.get("content")
        if text:
            message.content.append(TextContent(text=text))
        for call in payload.get("tool_calls") or []:
            message.content.append(_tool_call(call))

        raw = choice.get("finish_reason")
        message.raw_stop_reason = raw
        message.stop_reason = map_finish_reason(OPENAI_STOP_REASONS, raw)
        message.usage = _usage_from(body.get("usage"))
        return message

    # -- 流式翻译 ----------------------------------------------------------

    def feed(self, event: SSEEvent, assembler: StreamAssembler) -> None:
        data = event.data.strip()
        if not data:
            return
        if data == "[DONE]":
            assembler.finish(assembler.recorded_stop_reason or "stop", raw=assembler.raw_stop_reason)
            return

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise GatewayError(
                ErrorCode.CONN_FAILED,
                f"无法解析上游 SSE 数据帧: {exc}",
                details={"data": data[:500]},
            ) from exc

        if isinstance(payload, dict) and payload.get("error"):
            raise GatewayError(
                ErrorCode.UPSTREAM_OVERLOADED,
                f"上游在流中返回错误: {payload['error']}",
                details={"error": payload["error"]},
            )

        assembler.set_response_meta(response_id=payload.get("id"), model=payload.get("model"))
        if payload.get("usage"):
            assembler.set_usage(**_usage_fields(payload["usage"]))

        for choice in payload.get("choices") or []:
            self._feed_choice(choice, assembler)

    def _feed_choice(self, choice: dict[str, Any], assembler: StreamAssembler) -> None:
        delta = choice.get("delta") or {}

        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            assembler.append_thinking(reasoning, _THINKING_INDEX)

        content = delta.get("content")
        if content:
            # 推理内容总是先于正文流出：正文一到，推理阶段即结束。
            # 若等到流末尾统一闭合，事件序列会变成 text_delta 之后才 thinking_end。
            assembler.close_thinking(_THINKING_INDEX)
            assembler.append_text(content, _TEXT_INDEX)

        for call in delta.get("tool_calls") or []:
            index = _TOOL_INDEX_BASE + int(call.get("index") or 0)
            function = call.get("function") or {}
            if call.get("id") or function.get("name"):
                assembler.open_tool_call(index, call_id=call.get("id") or "", name=function.get("name") or "")
            arguments = function.get("arguments")
            if arguments:
                assembler.append_tool_arguments(arguments, index)

        raw = choice.get("finish_reason")
        if raw:
            assembler.record_stop_reason(map_finish_reason(OPENAI_STOP_REASONS, raw), raw=raw)


def _tool_call(call: dict[str, Any]) -> ToolCall:
    function = call.get("function") or {}
    raw_args = function.get("arguments")
    if isinstance(raw_args, dict):
        arguments = raw_args
    else:
        try:
            arguments = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError):
            arguments = {}
    return ToolCall(id=call.get("id") or "", name=function.get("name") or "", arguments=arguments)


def _usage_fields(payload: dict[str, Any]) -> dict[str, int | None]:
    """OpenAI 用量字段 → 统一用量字段。

    ``cached_tokens`` 计入 cache_read、``reasoning_tokens`` 计入 reasoning。
    reasoning 是 output 的子集，只做展示，不重复计费。
    """
    prompt_details = payload.get("prompt_tokens_details") or {}
    completion_details = payload.get("completion_tokens_details") or {}
    return {
        "input": payload.get("prompt_tokens"),
        "output": payload.get("completion_tokens"),
        "cache_read": prompt_details.get("cached_tokens"),
        "reasoning": completion_details.get("reasoning_tokens"),
        "total": payload.get("total_tokens"),
    }


def _usage_from(payload: dict[str, Any] | None) -> Usage:
    usage = Usage()
    if not payload:
        return usage
    fields = _usage_fields(payload)
    usage.input = fields["input"] or 0
    usage.output = fields["output"] or 0
    usage.cache_read = fields["cache_read"] or 0
    usage.reasoning = fields["reasoning"]
    usage.total_tokens = fields["total"] or 0
    return usage
