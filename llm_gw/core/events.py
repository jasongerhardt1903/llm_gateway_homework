"""通用异步事件流。

移植 pi 的 ``EventStream``（push/end/result + 异步迭代），并额外强制需求中的
**单一终态不变式**：一个流只能以 done、error、cancelled 三者之一结束，
不允许在流中间切换终态类型。

事件流是"真流式"的：生产者可以晚于消费者启动，事件逐个交付而不是攒齐再发。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Generic, TypeVar

from .messages import AssistantMessage, StopReason, TerminalState, Usage

T = TypeVar("T")
R = TypeVar("R")

__all__ = [
    "TerminalStateViolation",
    "StartEvent",
    "TextStartEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "ThinkingStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ToolCallStartEvent",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    "UsageEvent",
    "DoneEvent",
    "ErrorEvent",
    "CancelledEvent",
    "AssistantEvent",
    "EventStream",
    "AssistantEventStream",
    "TERMINAL_EVENT_TYPES",
]

#: 三种终态事件类型。除此之外的事件都是流内中间事件。
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"done", "error", "cancelled"})


class TerminalStateViolation(RuntimeError):
    """试图在流已经结束后再次推送终态事件。

    需求要求流式响应只有一个终态，因此第二个终态事件必须显式失败，
    而不是被静默忽略——静默会掩盖上游状态机 bug。
    """


# --------------------------------------------------------------------------
# 事件定义
# --------------------------------------------------------------------------


@dataclass
class StartEvent:
    type: str = "start"


@dataclass
class TextStartEvent:
    index: int = 0
    type: str = "text_start"


@dataclass
class TextDeltaEvent:
    index: int
    delta: str
    type: str = "text_delta"


@dataclass
class TextEndEvent:
    index: int
    text: str
    type: str = "text_end"


@dataclass
class ThinkingStartEvent:
    index: int = 0
    type: str = "thinking_start"


@dataclass
class ThinkingDeltaEvent:
    index: int
    delta: str
    type: str = "thinking_delta"


@dataclass
class ThinkingEndEvent:
    index: int
    thinking: str
    type: str = "thinking_end"


@dataclass
class ToolCallStartEvent:
    index: int
    id: str = ""
    name: str = ""
    type: str = "toolcall_start"


@dataclass
class ToolCallDeltaEvent:
    index: int
    delta: str
    type: str = "toolcall_delta"


@dataclass
class ToolCallEndEvent:
    index: int
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)
    type: str = "toolcall_end"


@dataclass
class UsageEvent:
    usage: Usage
    type: str = "usage"


@dataclass
class DoneEvent:
    """正常完成终态。"""

    reason: StopReason
    message: AssistantMessage
    type: str = "done"


@dataclass
class ErrorEvent:
    """错误终态。"""

    reason: StopReason
    error: AssistantMessage
    type: str = "error"


@dataclass
class CancelledEvent:
    """取消终态（客户端断开或上游取消）。"""

    reason: StopReason
    message: AssistantMessage
    type: str = "cancelled"


AssistantEvent = (
    StartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | UsageEvent
    | DoneEvent
    | ErrorEvent
    | CancelledEvent
)


# --------------------------------------------------------------------------
# 事件流
# --------------------------------------------------------------------------


class EventStream(Generic[T, R]):
    """异步事件队列。

    消费者通过 ``async for`` 读取；``await stream.result()`` 取得终态结果。
    """

    def __init__(self, is_terminal, extract_result) -> None:
        self._is_terminal = is_terminal
        self._extract_result = extract_result
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._terminal_type: str | None = None
        self._result: R | None = None
        self._result_ready = asyncio.Event()
        self._sentinel = object()

    # -- 生产者接口 --------------------------------------------------------

    @property
    def terminal_type(self) -> str | None:
        """已发生的终态类型；流尚未结束时为 None。"""
        return self._terminal_type

    @property
    def finished(self) -> bool:
        return self._closed

    def push(self, event: T) -> None:
        """推入一个事件。

        - 流已结束后推入的**中间事件**被丢弃（迟到数据不得污染已结束的流）。
        - 流已结束后推入的**终态事件**抛 :class:`TerminalStateViolation`。
        """
        event_type = getattr(event, "type", None)

        if self._closed:
            if event_type in TERMINAL_EVENT_TYPES:
                raise TerminalStateViolation(
                    f"stream already terminated as {self._terminal_type!r}, cannot terminate again as {event_type!r}"
                )
            return

        if event_type in TERMINAL_EVENT_TYPES:
            self._terminal_type = event_type
            self._closed = True
            self._result = self._extract_result(event)
            self._result_ready.set()
            self._queue.put_nowait(event)
            # 终态之后紧跟哨兵，``async for`` 才能自然结束；
            # 否则消费者会在终态事件后永久阻塞在队列上。
            self._queue.put_nowait(self._sentinel)
            return

        self._queue.put_nowait(event)

    def end(self, result: R | None = None) -> None:
        """收尾。未推送终态事件时也能正常关闭流，避免消费者永久阻塞。"""
        if result is not None and not self._result_ready.is_set():
            self._result = result
            self._result_ready.set()
        self._closed = True
        self._queue.put_nowait(self._sentinel)

    # -- 消费者接口 --------------------------------------------------------

    async def __aiter__(self) -> AsyncIterator[T]:
        while True:
            item = await self._queue.get()
            if item is self._sentinel:
                return
            yield item

    async def result(self) -> R:
        """等待并返回终态结果。"""
        await self._result_ready.wait()
        return self._result  # type: ignore[return-value]

    def result_nowait(self) -> R | None:
        """已结束时立即取结果，否则返回 None。"""
        return self._result if self._result_ready.is_set() else None

    # -- 生产者任务 --------------------------------------------------------

    def attach_task(self, task: "asyncio.Task[Any]") -> None:
        """绑定产出本流事件的后台任务，供 :meth:`cancel` 取消。

        客户端断开时若不取消生产者，上游 httpx 请求会继续跑到结束——
        既浪费配额，也让并发槽迟迟不释放。
        """
        self._task = task

    async def cancel(self) -> None:
        """取消已绑定的生产者任务并等待其收尾。

        任务已在 ``CancelledError`` 分支里推入取消终态，因此这里吞掉
        ``CancelledError`` 是安全的，不会掩盖真实错误。
        """
        task = getattr(self, "_task", None)
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _assistant_event_is_terminal(event: Any) -> bool:
    return getattr(event, "type", None) in TERMINAL_EVENT_TYPES


def _assistant_event_result(event: Any) -> AssistantMessage:
    if isinstance(event, DoneEvent):
        return event.message
    if isinstance(event, ErrorEvent):
        return event.error
    if isinstance(event, CancelledEvent):
        return event.message
    raise TerminalStateViolation(f"unexpected event type for final result: {getattr(event, 'type', event)!r}")


class AssistantEventStream(EventStream[AssistantEvent, AssistantMessage]):
    """LLM 响应事件流，终态为 done / error / cancelled。"""

    def __init__(self) -> None:
        super().__init__(_assistant_event_is_terminal, _assistant_event_result)

    def terminal_state(self) -> TerminalState | None:
        """已发生终态的归一化表示。"""
        mapping: dict[str, TerminalState] = {"done": "done", "error": "error", "cancelled": "cancelled"}
        if self._terminal_type is None:
            return None
        return mapping[self._terminal_type]
