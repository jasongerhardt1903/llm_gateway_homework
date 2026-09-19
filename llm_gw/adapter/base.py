"""Adapter 基类与流式装配器。

需求："将统一的 task schema 翻译成不同大模型使用的 prompt schema 并发送给 LLM；
将 LLM 的响应翻译成统一的 task schema"。

``Adapter`` 定义翻译契约，两个协议 adapter 只需要回答三个问题：

1. :meth:`Adapter.build_request` —— 统一 task 如何变成 HTTP 请求；
2. :meth:`Adapter.parse_response` —— 非流式响应如何变成统一消息；
3. :meth:`Adapter.feed` —— 一条 SSE 事件如何驱动 :class:`StreamAssembler`。

``StreamAssembler`` 承担"供应商 delta → 统一事件序列"的公共部分：块的开闭、
索引映射、usage 合并、终态收尾。协议 adapter 因此不必各自实现一套容易写错的
状态机，终态唯一这条不变式也就只有一处实现。
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from ..core.advanced import AdvancedConfig
from ..core.errors import (
    ErrorCode,
    GatewayError,
    classify_error_code,
    format_provider_error,
    normalize_provider_error,
)
from ..core.events import (
    AssistantEventStream,
    CancelledEvent,
    DoneEvent,
    ErrorEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UsageEvent,
)
from ..core.json_utils import parse_streaming_json
from ..core.messages import (
    AssistantMessage,
    Model,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    calculate_cost,
)
from ..core.schema import Task
from ..harness.sse import SSEEvent, parse_sse_stream

__all__ = [
    "AdapterOptions",
    "StreamAssembler",
    "Adapter",
    "provider_error_from_response",
    "map_finish_reason",
]


@dataclass
class AdapterOptions:
    """调用级选项。密钥不落在 ``Model`` 上，避免随模型配置四处传播。"""

    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int | None = None
    #: 供应商私有字段（如 ``top_p``），原样并入请求体。
    extra_body: dict[str, Any] = field(default_factory=dict)
    #: 本次调用生效的高级配置（模型自身配置或 profile 模版，由路由层解析后传入）。
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)


# --------------------------------------------------------------------------
# 流式装配
# --------------------------------------------------------------------------


class StreamAssembler:
    """把供应商的 delta 序列装配成统一事件序列。

    ``index`` 是**供应商侧的内容块编号**（OpenAI 的 ``delta.tool_calls[].index``、
    Anthropic 的 ``content_block.index``）。装配器在块打开时把它映射到统一
    ``message.content`` 的位置，因此供应商索引不连续、乱序也不会错位。

    TTFT 口径：只有真正产生了文本/思考/工具参数的 delta 才算"有业务意义的
    输出"。``start`` 事件不计入，避免把连接建立当成首 token。
    """

    def __init__(self, out: AssistantEventStream, model: Model) -> None:
        self.out = out
        self.model = model
        self.message = AssistantMessage(timestamp=time.time())
        self.finished = False
        #: 是否已产生过有业务意义的 delta（TTFT 判定与"已流式输出"判定共用）。
        self.emitted_business_delta = False
        self.chunk_count = 0
        #: 上游给出的完成信号（finish_reason / stop_reason）。需求允许
        #: "finish_reason / [DONE] / message_stop" 任一作为完成依据。
        self.recorded_stop_reason: StopReason | None = None
        self.raw_stop_reason: str | None = None

        self._positions: dict[int, int] = {}
        self._text_open: set[int] = set()
        self._thinking_open: set[int] = set()
        self._tool_open: set[int] = set()
        self._tool_args: dict[int, list[str]] = {}
        self._usage_reported = False
        self._started = False

    # -- 内部工具 ----------------------------------------------------------

    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        from ..core.events import StartEvent

        self.out.push(StartEvent())

    def _position(self, index: int) -> int:
        if index not in self._positions:
            self._positions[index] = len(self.message.content)
        return self._positions[index]

    def _block(self, index: int):
        return self.message.content[self._position(index)]

    # -- 文本 --------------------------------------------------------------

    def open_text(self, index: int = 0) -> None:
        if self.finished:
            return
        self._start()
        if index in self._text_open:
            return
        self._text_open.add(index)
        position = self._position(index)
        if position >= len(self.message.content):
            self.message.content.append(TextContent())
        self.out.push(TextStartEvent(index=position))

    def append_text(self, delta: str, index: int = 0) -> None:
        if not delta or self.finished:
            return
        self.open_text(index)
        self._block(index).text += delta
        self.emitted_business_delta = True
        self.out.push(TextDeltaEvent(index=self._position(index), delta=delta))

    def close_text(self, index: int = 0) -> None:
        if index not in self._text_open:
            return
        self._text_open.discard(index)
        block = self._block(index)
        self.out.push(TextEndEvent(index=self._position(index), text=block.text))

    # -- 思考 --------------------------------------------------------------

    def open_thinking(self, index: int = 0) -> None:
        if self.finished:
            return
        self._start()
        if index in self._thinking_open:
            return
        self._thinking_open.add(index)
        position = self._position(index)
        if position >= len(self.message.content):
            self.message.content.append(ThinkingContent())
        self.out.push(ThinkingStartEvent(index=position))

    def append_thinking(self, delta: str, index: int = 0) -> None:
        if not delta or self.finished:
            return
        self.open_thinking(index)
        self._block(index).thinking += delta
        self.emitted_business_delta = True
        self.out.push(ThinkingDeltaEvent(index=self._position(index), delta=delta))

    def close_thinking(self, index: int = 0) -> None:
        if index not in self._thinking_open:
            return
        self._thinking_open.discard(index)
        block = self._block(index)
        self.out.push(ThinkingEndEvent(index=self._position(index), thinking=block.thinking))

    # -- 工具调用 ----------------------------------------------------------

    def open_tool_call(self, index: int, *, call_id: str = "", name: str = "") -> None:
        if self.finished:
            return
        self._start()
        if index in self._tool_open:
            block = self._block(index)
            # 分片补全：id / name 可能晚于首个 delta 到达。
            if call_id and not block.id:
                block.id = call_id
            if name and not block.name:
                block.name = name
            return
        self._tool_open.add(index)
        self._tool_args.setdefault(index, [])
        position = self._position(index)
        if position >= len(self.message.content):
            self.message.content.append(ToolCall())
        block = self._block(index)
        block.id = call_id
        block.name = name
        self.out.push(ToolCallStartEvent(index=position, id=call_id, name=name))

    def append_tool_arguments(self, fragment: str, index: int = 0) -> None:
        if not fragment or self.finished:
            return
        self.open_tool_call(index)
        self._tool_args.setdefault(index, []).append(fragment)
        self.emitted_business_delta = True
        self.out.push(ToolCallDeltaEvent(index=self._position(index), delta=fragment))

    def close_tool_call(self, index: int = 0) -> None:
        if index not in self._tool_open:
            return
        self._tool_open.discard(index)
        block = self._block(index)
        raw = "".join(self._tool_args.get(index, []))
        # 增量解析：参数流可能被截断，尽力还原已到达的部分。
        block.arguments = parse_streaming_json(raw)
        self.out.push(
            ToolCallEndEvent(index=self._position(index), id=block.id, name=block.name, arguments=block.arguments)
        )

    # -- 元信息与用量 ------------------------------------------------------

    def set_usage(
        self,
        *,
        input: int | None = None,
        output: int | None = None,
        cache_read: int | None = None,
        cache_write: int | None = None,
        reasoning: int | None = None,
        total: int | None = None,
    ) -> None:
        """按绝对量覆盖用量字段。

        供应商把用量拆在多个事件里（Anthropic 的 input 在 ``message_start``、
        output 在 ``message_delta``），所以只覆盖显式传入的字段。
        """
        usage = self.message.usage
        if input is not None:
            usage.input = input
        if output is not None:
            usage.output = output
        if cache_read is not None:
            usage.cache_read = cache_read
        if cache_write is not None:
            usage.cache_write = cache_write
        if reasoning is not None:
            usage.reasoning = reasoning
        if total is not None:
            usage.total_tokens = total

    def set_response_meta(self, *, response_id: str | None = None, model: str | None = None) -> None:
        if response_id:
            self.message.response_id = response_id
        if model:
            self.message.response_model = model

    def record_chunk(self) -> None:
        self.chunk_count += 1

    def record_stop_reason(self, stop_reason: StopReason, *, raw: str | None = None) -> None:
        """记录上游的完成信号。首个信号生效，后续重复信号忽略。"""
        if self.recorded_stop_reason is None:
            self.recorded_stop_reason = stop_reason
            self.raw_stop_reason = raw

    # -- 终态 --------------------------------------------------------------

    def finish(self, stop_reason: StopReason = "stop", *, raw: str | None = None) -> None:
        """正常完成。先闭合所有打开的块，再发 usage，最后发唯一终态。"""
        if self.finished:
            return
        self._close_all()
        self.message.stop_reason = stop_reason
        self.message.raw_stop_reason = raw
        calculate_cost(self.model, self.message.usage)
        self._emit_usage()
        self.finished = True
        self.out.push(DoneEvent(reason=stop_reason, message=self.message))

    def fail(self, error: str, *, stop_reason: StopReason = "error", code: ErrorCode | None = None) -> None:
        """错误终态。已发出的文本保留在 message 里，便于调用方落库排查。"""
        if self.finished:
            return
        self._close_all()
        self.message.stop_reason = stop_reason
        self.message.error_message = error
        calculate_cost(self.model, self.message.usage)
        self._emit_usage()
        self.finished = True
        self.out.push(ErrorEvent(reason=stop_reason, error=self.message))
        _ = code  # 错误码由调用方按 message.error_message 再分类，避免重复来源

    def fail_interrupted(self) -> None:
        """上游未给出任何终态信号就断流。

        需求决策表："已流式输出 → 不盲目重新生成"。因此这里必须区分两种情况：
        已经吐过内容的流不能当作"没发生过"重来，只能如实报错。
        """
        if self.emitted_business_delta:
            code = ErrorCode.STREAM_INTERRUPTED_AFTER_DATA
            detail = "上游流在已输出内容后中断，未收到 finish_reason / [DONE] / message_stop"
        else:
            code = ErrorCode.CONN_FAILED
            detail = "上游流在输出任何内容前中断，未收到 finish_reason / [DONE] / message_stop"
        self.fail(f"{code.value}: {detail}")

    def cancelled(self) -> None:
        """取消终态。取消只是信号，不代表上游已经停止生成。"""
        if self.finished:
            return
        self._close_all()
        self.message.stop_reason = "aborted"
        calculate_cost(self.model, self.message.usage)
        self._emit_usage()
        self.finished = True
        self.out.push(CancelledEvent(reason="aborted", message=self.message))

    def _close_all(self) -> None:
        """按内容块在消息中的位置顺序闭合。

        不能按类型分组闭合：思考块在文本块之前，分组会产出
        ``thinking_end`` 晚于 ``text_end`` 的乱序事件序列。
        """
        order = {index: self._positions.get(index, 0) for index in self._open_indices()}
        for index in sorted(order, key=lambda item: order[item]):
            self.close_text(index)
            self.close_thinking(index)
            self.close_tool_call(index)

    def _open_indices(self) -> set[int]:
        return set(self._text_open) | set(self._thinking_open) | set(self._tool_open)

    def _emit_usage(self) -> None:
        if self._usage_reported:
            return
        self._usage_reported = True
        usage = self.message.usage
        if usage.effective_total_tokens() or usage.cost.total:
            self.out.push(UsageEvent(usage=usage))


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------


class Adapter(ABC):
    """协议 adapter 抽象基类。"""

    #: 协议标识，与 ``Model.api`` 对应。
    api: ClassVar[str] = ""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._tasks: set[asyncio.Task] = set()

    # -- 翻译契约 ----------------------------------------------------------

    @abstractmethod
    def build_request(
        self,
        model: Model,
        task: Task,
        options: AdapterOptions | None = None,
        *,
        stream: bool = True,
    ) -> httpx.Request:
        """把统一 task 翻译成供应商 HTTP 请求。"""

    @abstractmethod
    def parse_response(self, body: dict[str, Any], model: Model) -> AssistantMessage:
        """把非流式的完整响应翻译成统一消息。"""

    @abstractmethod
    def feed(self, event: SSEEvent, assembler: StreamAssembler) -> None:
        """用一条 SSE 事件驱动装配器。"""

    # -- 高级配置 ----------------------------------------------------------

    #: 思考模式 → 本协议的请求体字段。协议各自声明；未列出的模式不发任何字段
    #: （``default`` 交给供应商默认，``off`` 在无显式关闭字段的协议下同样只能不发送）。
    thinking_body_map: ClassVar[dict[str, dict[str, Any]]] = {}

    def advanced_body(self, advanced: AdvancedConfig) -> dict[str, Any]:
        """把高级配置翻译成本协议认识的请求字段。

        ``None`` 表示"不发送"而非"发送 0"：``temperature=0`` 是确定性采样，
        与不传该字段的语义完全不同，因此这里必须逐项判空。
        """
        body: dict[str, Any] = {}
        if advanced.temperature is not None:
            body["temperature"] = advanced.temperature
        if advanced.top_p is not None:
            body["top_p"] = advanced.top_p
        if advanced.top_k is not None:
            body["top_k"] = advanced.top_k
        if advanced.max_tokens is not None:
            body["max_tokens"] = advanced.max_tokens
        body.update(self.thinking_body(advanced.thinking_mode))
        return body

    def thinking_body(self, mode: str) -> dict[str, Any]:
        """思考模式的协议映射。"""
        return self.thinking_body_map.get(mode, {})

    # -- 执行 --------------------------------------------------------------

    def new_assembler(self, out: AssistantEventStream, model: Model) -> StreamAssembler:
        return StreamAssembler(out, model)

    def stream(
        self,
        model: Model,
        task: Task,
        options: AdapterOptions | None = None,
    ) -> AssistantEventStream:
        """发起流式请求，立即返回事件流；生产在后台任务中进行。"""
        out = AssistantEventStream()
        producer = asyncio.ensure_future(self._produce(model, task, options or AdapterOptions(), out))
        # 持有引用，避免后台任务被 GC 掉。
        self._tasks.add(producer)
        producer.add_done_callback(self._tasks.discard)
        # 把任务交给事件流，客户端断开时服务层可以据此取消上游请求。
        out.attach_task(producer)
        return out

    async def complete(
        self,
        model: Model,
        task: Task,
        options: AdapterOptions | None = None,
    ) -> AssistantMessage:
        """非流式调用：上游返回完整响应后一次性翻译。"""
        request = self.build_request(model, task, options, stream=False)
        response = await self._client.send(request)
        if response.status_code >= 400:
            raise provider_error_from_response(response)
        message = self.parse_response(response.json(), model)
        calculate_cost(model, message.usage)
        return message

    async def _produce(
        self,
        model: Model,
        task: Task,
        options: AdapterOptions,
        out: AssistantEventStream,
    ) -> None:
        """后台生产：请求、解析、收尾。任何异常都必须转成终态事件。"""
        assembler: StreamAssembler | None = None
        try:
            request = self.build_request(model, task, options, stream=True)
            response = await self._client.send(request, stream=True)
            try:
                if response.status_code >= 400:
                    await response.aread()
                    raise provider_error_from_response(response)

                assembler = self.new_assembler(out, model)
                async for sse in parse_sse_stream(response.aiter_bytes()):
                    if assembler.finished:
                        break
                    assembler.record_chunk()
                    self.feed(sse, assembler)

                if not assembler.finished:
                    # 上游可能在给出 finish_reason 后不再发 [DONE]，此时仍算正常完成。
                    if assembler.recorded_stop_reason is not None:
                        assembler.finish(assembler.recorded_stop_reason, raw=assembler.raw_stop_reason)
                    else:
                        assembler.fail_interrupted()
            finally:
                await response.aclose()

        except asyncio.CancelledError:
            # 客户端断开：发取消终态，但不重抛——重抛会掩盖"上游可能仍在生成"的事实，
            # 且服务层已经知道取消发生了。
            if assembler is not None and not assembler.finished:
                assembler.cancelled()
            raise
        except GatewayError as exc:
            _fail(out, assembler, f"{exc.code.value}: {exc.message}")
        except httpx.HTTPError as exc:
            norm = normalize_provider_error(exc)
            code = classify_error_code(exc)
            _fail(out, assembler, f"{code.value}: {format_provider_error(norm)}")
        except Exception as exc:  # noqa: BLE001 - 兜底：绝不让流悬空
            norm = normalize_provider_error(exc)
            _fail(out, assembler, f"{ErrorCode.UNKNOWN.value}: {format_provider_error(norm)}")


def _fail(out: AssistantEventStream, assembler: StreamAssembler | None, message: str) -> None:
    """统一的失败收尾，保证流一定以终态结束。"""
    if assembler is not None and not assembler.finished:
        assembler.fail(message)
        return
    if not out.finished:
        error = AssistantMessage(stop_reason="error", error_message=message, timestamp=time.time())
        out.push(ErrorEvent(reason="error", error=error))


def provider_error_from_response(response: httpx.Response) -> GatewayError:
    """把非 2xx 响应变成 :class:`GatewayError`。

    直接构造 ``HTTPStatusError`` 再走归一化，这样请求 ID、响应体截断、可重试
    判定这些逻辑只有一份实现。
    """
    try:
        request = response.request
    except RuntimeError:  # pragma: no cover - 无请求上下文时退化为合成请求
        request = httpx.Request("POST", str(response.url))
    # 调用方已经读完 body（aread 会把内容留在 response 上），归一化因此能拿到正文。
    exc = httpx.HTTPStatusError(f"HTTP {response.status_code}", request=request, response=response)
    norm = normalize_provider_error(exc)
    code = classify_error_code(exc)
    return GatewayError(
        code,
        format_provider_error(norm, prefix="provider"),
        http_status=norm.status,
        provider_request_id=norm.provider_request_id,
        details={"body": norm.body} if norm.body else {},
    )


def map_finish_reason(mapping: dict[str, StopReason], raw: str | None, default: StopReason = "stop") -> StopReason:
    """把供应商的 finish reason 映射成统一 stop reason。"""
    if not raw:
        return default
    return mapping.get(raw, default)


def parse_json_or_none(text: str) -> Any:
    """解析 JSON，失败返回 None（用于响应体里可选的 JSON 字段）。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
