"""Anthropic Messages 协议 adapter。

与 OpenAI 兼容协议的结构性差异（这正是"多供应商"真正压测翻译层的地方）：

* system 不在 messages 里，而是顶层 ``system`` 字段；
* ``max_tokens`` 必填；
* 内容块有类型（text / thinking / tool_use / tool_result），且有显式
  ``content_block_start|delta|stop`` 生命周期；
* 用量拆在 ``message_start``（input）与 ``message_delta``（output）两处；
* 没有 ``response_format``，结构化输出只能靠 prompt 约束 + 本地校验。
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

__all__ = [
    "AnthropicMessagesAdapter",
    "ANTHROPIC_STOP_REASONS",
    "ANTHROPIC_VERSION",
    "DEFAULT_THINKING_BUDGET",
]

ANTHROPIC_VERSION = "2023-06-01"

#: Anthropic 开启思考必须带 ``budget_tokens``，缺了会 400。这个值不暴露成配置项，
#: 需要调整时用 ``AdapterOptions.extra_body`` 覆盖。
DEFAULT_THINKING_BUDGET = 1024

ANTHROPIC_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
    # 安全拒答：不得靠换模型绕过。
    "refusal": "error",
}

#: Anthropic 缺少 max_tokens 会直接 400，因此给一个保守兜底值。
_DEFAULT_MAX_TOKENS = 4096

#: 结构化输出的 prompt 约束（Anthropic 无原生 response_format）。
_SCHEMA_INSTRUCTION = (
    "You must reply with a single JSON object and nothing else. "
    "It must validate against this JSON Schema:\n"
)


class AnthropicMessagesAdapter(Adapter):
    """把统一 task 翻译成 Anthropic Messages 请求，并把响应翻译回来。"""

    api: ClassVar[str] = "anthropic-messages"

    #: Anthropic 没有"显式关闭思考"字段：不发送 ``thinking`` 即为关闭，
    #: 因此 ``off`` 与 ``default`` 一样不产出任何字段。
    thinking_body_map: ClassVar[dict[str, dict[str, Any]]] = {
        "on": {"thinking": {"type": "enabled", "budget_tokens": DEFAULT_THINKING_BUDGET}},
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
        system = self._system(task)
        body: dict[str, Any] = {
            "model": model.id,
            "messages": self._messages(task),
            "stream": stream,
        }

        # 优先级：高级配置（模型/模版默认）< task 显式指定 < extra_body。
        body.update(self.advanced_body(options.advanced))

        # max_tokens 是唯一需要三方比较的字段，且 Anthropic 缺少它会直接 400，
        # 因此最后还要落到一个保守兜底值。
        body["max_tokens"] = (
            task.input.max_tokens
            or options.advanced.max_tokens
            or model.max_tokens
            or _DEFAULT_MAX_TOKENS
        )
        if system:
            body["system"] = system
        if task.input.temperature is not None:
            body["temperature"] = task.input.temperature
        if task.input.tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in task.input.tools
            ]
            body["tool_choice"] = {"type": "auto"}

        body.update(options.extra_body)

        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream" if stream else "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if options.api_key:
            headers["x-api-key"] = options.api_key
        headers.update(model.headers)
        headers.update(options.headers)

        return httpx.Request(
            "POST",
            f"{model.base_url.rstrip('/')}/messages",
            headers=headers,
            json=body,
        )

    def _system(self, task: Task) -> str:
        """system 消息与 ``input.system`` 合并到顶层字段。"""
        parts: list[str] = []
        if task.input.system:
            parts.append(task.input.system)
        for message in task.input.messages:
            if message.role == "system":
                text = message.text()
                if text:
                    parts.append(text)
        if task.input.response_schema is not None:
            parts.append(
                _SCHEMA_INSTRUCTION
                + json.dumps(task.input.response_schema, ensure_ascii=False, sort_keys=True)
            )
        return "\n\n".join(parts)

    def _messages(self, task: Task) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in task.input.messages:
            if message.role == "system":
                continue  # 已提升到顶层 system
            payload = self._message(message)
            if payload is None:
                continue
            # Anthropic 要求 user/assistant 交替；同角色相邻消息合并成一个。
            if out and out[-1]["role"] == payload["role"]:
                out[-1]["content"].extend(payload["content"])
            else:
                out.append(payload)
        return out

    def _message(self, message: TaskMessage) -> dict[str, Any] | None:
        blocks = self._blocks(message)
        if not blocks:
            return None
        if message.role == "tool":
            return {"role": "user", "content": blocks}
        if message.role == "assistant":
            return {"role": "assistant", "content": blocks}
        return {"role": "user", "content": blocks}

    def _blocks(self, message: TaskMessage) -> list[dict[str, Any]]:
        if isinstance(message.content, str):
            return [{"type": "text", "text": message.content}] if message.content else []

        blocks: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text:
                    blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ImageBlock):
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.mime_type,
                            "data": block.data,
                        },
                    }
                )
            elif block.type == "tool_call":
                blocks.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}
                )
            elif block.type == "tool_result":
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_call_id,
                        "content": block.content,
                    }
                )
        return blocks

    # -- 非流式响应翻译 ----------------------------------------------------

    def parse_response(self, body: dict[str, Any], model: Model) -> AssistantMessage:
        message = AssistantMessage(response_id=body.get("id"), response_model=body.get("model"))
        for block in body.get("content") or []:
            kind = block.get("type")
            if kind == "text":
                message.content.append(TextContent(text=block.get("text") or ""))
            elif kind == "thinking":
                message.content.append(
                    ThinkingContent(thinking=block.get("thinking") or "", signature=block.get("signature"))
                )
            elif kind == "tool_use":
                message.content.append(
                    ToolCall(
                        id=block.get("id") or "",
                        name=block.get("name") or "",
                        arguments=block.get("input") or {},
                    )
                )
        raw = body.get("stop_reason")
        message.raw_stop_reason = raw
        message.stop_reason = map_finish_reason(ANTHROPIC_STOP_REASONS, raw)
        message.usage = _usage_from(body.get("usage"))
        return message

    # -- 流式翻译 ----------------------------------------------------------

    def feed(self, event: SSEEvent, assembler: StreamAssembler) -> None:
        kind = event.event or _kind_from_payload(event.data)
        try:
            payload = json.loads(event.data) if event.data.strip() else {}
        except json.JSONDecodeError as exc:
            raise GatewayError(
                ErrorCode.CONN_FAILED,
                f"无法解析上游 SSE 数据帧: {exc}",
                details={"data": event.data[:500]},
            ) from exc

        if kind == "ping":
            return
        if kind == "error":
            detail = payload.get("error") or payload
            raise GatewayError(
                ErrorCode.UPSTREAM_OVERLOADED,
                f"上游在流中返回错误: {detail}",
                details={"error": detail},
            )
        if kind == "message_start":
            inner = payload.get("message") or {}
            assembler.set_response_meta(response_id=inner.get("id"), model=inner.get("model"))
            assembler.set_usage(**_usage_fields(inner.get("usage")))
            return
        if kind == "content_block_start":
            self._block_start(payload, assembler)
            return
        if kind == "content_block_delta":
            self._block_delta(payload, assembler)
            return
        if kind == "content_block_stop":
            self._block_stop(int(payload.get("index") or 0), assembler)
            return
        if kind == "message_delta":
            delta = payload.get("delta") or {}
            raw = delta.get("stop_reason")
            if raw:
                assembler.record_stop_reason(map_finish_reason(ANTHROPIC_STOP_REASONS, raw), raw=raw)
            assembler.set_usage(**_usage_fields(payload.get("usage")))
            return
        if kind == "message_stop":
            assembler.finish(assembler.recorded_stop_reason or "stop", raw=assembler.raw_stop_reason)

    def _block_start(self, payload: dict[str, Any], assembler: StreamAssembler) -> None:
        index = int(payload.get("index") or 0)
        block = payload.get("content_block") or {}
        kind = block.get("type")
        if kind == "text":
            assembler.open_text(index)
        elif kind == "thinking" or kind == "redacted_thinking":
            assembler.open_thinking(index)
        elif kind == "tool_use":
            assembler.open_tool_call(index, call_id=block.get("id") or "", name=block.get("name") or "")

    def _block_delta(self, payload: dict[str, Any], assembler: StreamAssembler) -> None:
        index = int(payload.get("index") or 0)
        delta = payload.get("delta") or {}
        kind = delta.get("type")
        if kind == "text_delta":
            assembler.append_text(delta.get("text") or "", index)
        elif kind == "thinking_delta":
            assembler.append_thinking(delta.get("thinking") or "", index)
        elif kind == "input_json_delta":
            assembler.append_tool_arguments(delta.get("partial_json") or "", index)
        # signature_delta 只用于校验思考块完整性，统一模型里不消费。

    def _block_stop(self, index: int, assembler: StreamAssembler) -> None:
        assembler.close_text(index)
        assembler.close_thinking(index)
        assembler.close_tool_call(index)


def _kind_from_payload(data: str) -> str:
    """部分网关不设 ``event:`` 行，退回从 payload 的 ``type`` 推断。"""
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(payload, dict):
        return payload.get("type") or ""
    return ""


def _usage_fields(payload: dict[str, Any] | None) -> dict[str, int | None]:
    """Anthropic 用量字段 → 统一用量字段。"""
    if not payload:
        return {}
    return {
        "input": payload.get("input_tokens"),
        "output": payload.get("output_tokens"),
        "cache_read": payload.get("cache_read_input_tokens"),
        "cache_write": payload.get("cache_creation_input_tokens"),
    }


def _usage_from(payload: dict[str, Any] | None) -> Usage:
    usage = Usage()
    if not payload:
        return usage
    fields = _usage_fields(payload)
    usage.input = fields.get("input") or 0
    usage.output = fields.get("output") or 0
    usage.cache_read = fields.get("cache_read") or 0
    usage.cache_write = fields.get("cache_write") or 0
    return usage
