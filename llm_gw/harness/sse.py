"""SSE 编解码。

需求："adapter 层默认采用 SSE 方式和 LLM 进行通讯"、"gateway 内部维护事件序列，
对外统一为 SSE 协议"。

解析侧必须自己缓冲字节流：网络分片与 SSE 行边界无关，一个 ``data:`` 行很可能
被拆成两个 chunk 送达。若直接按 chunk 解析，就会偶发丢事件——这类 bug 在本地
mock 上不复现，只在真实网络下暴露，所以这里从一开始就按字节缓冲。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

__all__ = ["SSEEvent", "parse_sse_stream", "encode_sse"]


@dataclass
class SSEEvent:
    """一条完整的 SSE 事件。"""

    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None


def _decode(chunk: str | bytes) -> str:
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", errors="replace")
    return chunk


async def parse_sse_stream(chunks: AsyncIterator[str | bytes]) -> AsyncIterator[SSEEvent]:
    """把任意分片的文本/字节流解析成 SSE 事件序列。

    遵循 SSE 规范中与 LLM 供应商相关的部分：

    * ``\\n`` / ``\\r\\n`` / ``\\r`` 都是行分隔符；
    * 空行表示事件结束；
    * ``:`` 开头是注释（供应商常用作 keep-alive），丢弃；
    * 同一事件内多条 ``data:`` 用 ``\\n`` 连接；
    * 无 ``data`` 字段的事件不产出。
    """
    buffer = ""
    data_lines: list[str] = []
    event_name: str | None = None
    event_id: str | None = None
    retry: int | None = None

    def flush() -> SSEEvent | None:
        nonlocal data_lines, event_name, event_id, retry
        if not data_lines:
            # 空行但没有 data：重置字段，不产出事件。
            event_name = None
            event_id = None
            retry = None
            return None
        event = SSEEvent(
            data="\n".join(data_lines),
            event=event_name,
            id=event_id,
            retry=retry,
        )
        data_lines = []
        event_name = None
        event_id = None
        retry = None
        return event

    async for chunk in chunks:
        buffer += _decode(chunk)
        # 统一换行符，后续只按 \n 切分。
        buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)

            if not line:
                event = flush()
                if event is not None:
                    yield event
                continue

            if line.startswith(":"):
                continue

            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]

            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_name = value
            elif field == "id":
                event_id = value
            elif field == "retry":
                try:
                    retry = int(value)
                except ValueError:
                    retry = None

    # 流结束时若缓冲区还有未换行的最后一行，按行处理；再冲刷残留事件。
    if buffer:
        line, buffer = buffer, ""
        if line and not line.startswith(":"):
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)

    event = flush()
    if event is not None:
        yield event


def encode_sse(data: str, *, event: str | None = None, event_id: str | None = None) -> bytes:
    """编码一条 SSE 事件。多行 data 会拆成多条 ``data:``。"""
    lines: list[str] = []
    if event is not None:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")
