"""mocktransport：用脚本化的 SSE 响应模拟 LLM 输出。

需求要求"测试的时候使用 mocktransport 模拟 LLM 的输出"。这里基于
``httpx.MockTransport`` 构建，adapter 只依赖注入的 ``httpx.AsyncClient``，
测试把 client 换成由本模块构造的即可，**完全不依赖真实供应商**。

两种用法：

* :func:`sse_transport` —— 一次成功请求，按行回放给定的 SSE 文本。
* :func:`scripted_transport` —— 多次请求按脚本依次返回（响应或异常），
  用于"第 1 次超时、第 2 次 429、第 3 次成功"这类重试场景。
"""

from __future__ import annotations

import httpx
from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "sse_transport",
    "scripted_transport",
    "client_for",
    "openai_sse",
    "anthropic_sse",
    "CapturedRequest",
]


class CapturedRequest:
    """记录 adapter 实际发出的请求，供契约测试断言请求翻译是否正确。"""

    def __init__(self, request: httpx.Request, body: bytes) -> None:
        self.method = request.method
        self.url = str(request.url)
        self.headers = dict(request.headers)
        self.body_bytes = body
        self.json = _try_json(body)

    def header(self, name: str) -> str | None:
        """大小写不敏感地取请求头。"""
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return None


def _try_json(body: bytes):
    import json

    if not body:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _sse_bytes(lines: Iterable[str]) -> bytes:
    """把若干 SSE 文本行拼成响应体，统一补换行。"""
    out = []
    for line in lines:
        out.append(line if line.endswith("\n") else line + "\n")
    return "".join(out).encode("utf-8")


def sse_transport(
    chunks: Iterable[str],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    captured: list[CapturedRequest] | None = None,
    content_type: str = "text/event-stream",
) -> httpx.MockTransport:
    """构造一次性成功响应的 mock transport。

    :param chunks: SSE 文本行序列，例如 ``["data: {...}", "", "data: [DONE]"]``。
    :param captured: 若提供，则把每个请求追加进去供断言。
    """
    payload = _sse_bytes(chunks)
    response_headers = {"content-type": content_type}
    if headers:
        response_headers.update(headers)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content if isinstance(request.content, bytes) else b""
        if captured is not None:
            captured.append(CapturedRequest(request, body))
        return httpx.Response(status, headers=response_headers, content=payload)

    return httpx.MockTransport(handler)


def scripted_transport(
    script: Sequence[httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]],
    *,
    captured: list[CapturedRequest] | None = None,
) -> httpx.MockTransport:
    """按脚本依次响应：第 N 次请求返回 ``script[N]``。

    脚本元素可以是 ``httpx.Response``（成功/错误响应）、``Exception``
    （模拟连接失败或超时），或接收 request 返回 response 的可调用对象。
    脚本耗尽后重复最后一项，便于"前两次失败、之后一直成功"。
    """
    state = {"index": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content if isinstance(request.content, bytes) else b""
        if captured is not None:
            captured.append(CapturedRequest(request, body))
        index = min(state["index"], len(script) - 1)
        state["index"] += 1
        item = script[index]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        return item

    return httpx.MockTransport(handler)


def client_for(transport: httpx.MockTransport, *, base_url: str = "https://mock.local") -> httpx.AsyncClient:
    """把 mock transport 包成 adapter 可直接使用的异步客户端。"""
    return httpx.AsyncClient(transport=transport, base_url=base_url)


def openai_sse(
    *,
    model: str = "gpt-4o-mini",
    text: str = "Hello",
    finish_reason: str = "stop",
    include_usage: bool = True,
    prompt_tokens: int = 10,
    completion_tokens: int = 2,
) -> list[str]:
    """生成一段最小可用的 OpenAI ``chat.completions`` 流式 SSE 文本。

    只做整体一次性 delta；需要逐字 delta 的测试请手工拼 chunks。
    """
    import json

    lines = [
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
            }
        ),
        "",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
        ),
        "",
    ]
    if include_usage:
        lines += [
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                }
            ),
            "",
        ]
    lines.append("data: [DONE]")
    lines.append("")
    return lines


def anthropic_sse(
    *,
    model: str = "claude-3-5-haiku-20241022",
    text: str = "Hello",
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 2,
) -> list[str]:
    """生成一段最小可用的 Anthropic Messages 流式 SSE 文本。"""
    import json

    def ev(name: str, data: dict) -> list[str]:
        return [f"event: {name}", f"data: {json.dumps(data)}", ""]

    lines: list[str] = []
    lines += ev(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 1},
            },
        },
    )
    lines += ev(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )
    lines += ev(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
    )
    lines += ev("content_block_stop", {"type": "content_block_stop", "index": 0})
    lines += ev(
        "message_delta",
        {"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": output_tokens}},
    )
    lines += ev("message_stop", {"type": "message_stop"})
    return lines
