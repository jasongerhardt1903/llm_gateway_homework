"""Harness 服务：对外 HTTP 接口 + SSE 流式。

需求："adapter 层默认采用 SSE 方式和 LLM 进行通讯"、"gateway 内部维护事件序列，
对外统一为 SSE 协议"、"流式结束只有一个原因"。

这一层负责把统一事件序列编码成对外的 SSE，并保证三件事：

1. **两层校验**：先校验语法（400），再校验 schema（422），错误码稳定。
2. **单一终态**：正常完成发 ``event: done`` + ``data: [DONE]``；错误/取消只发
   终态事件，**不发 [DONE]** —— 客户端据此区分"正常结束"与"异常结束"。
3. **可观测**：每次调用（含流式）都落一条 :class:`CallRecord`，TTFT 以
   **第一个有业务意义的 delta** 为准，不把 ``start`` 事件算作首 token。

非流式调用还会把执行层的处置事实（重试次数、是否降级、最终处置、告警）写进
``CallRecord.resilience`` 并在响应里回传 ``warnings``——需求要求"妥善记录"
重试 / 降级 / 报错三选一的结果。

版本：0.3.0
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.errors import ErrorCode
from ..core.events import AssistantEvent
from ..core.messages import AssistantMessage
from ..core.schema import SchemaViolation, SyntaxViolation, Task, validate_schema, validate_syntax
from ..core.telemetry import CallRecord, LatencyBreakdown, PromptInfo, ResilienceInfo
from ..harness.sse import encode_sse
from ..router.router import ExecutionTrace, Router
from ..util.clock import Clock, RealClock
from .query import Query
from .storage import Storage

__all__ = ["GatewayService", "agent_router", "create_app", "BUSINESS_DELTA_TYPES"]

#: 计入 TTFT 的"有业务意义"的事件类型。
#: ``start`` 只是连接建立信号，把它当首 token 会系统性低估 TTFT。
BUSINESS_DELTA_TYPES: frozenset[str] = frozenset({"text_delta", "thinking_delta", "toolcall_delta"})


class GatewayService:
    """把 Router + Storage 组装成可被 HTTP 层调用的服务。"""

    def __init__(
        self,
        router: Router,
        storage: Storage | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.router = router
        self.storage = storage
        self.clock: Clock = clock or RealClock()
        self.query = Query(storage) if storage is not None else None
        #: 最近一次非流式调用产生的告警（如"认证失败已换模型，未阻塞"）。
        #: 挂在服务上而不是扩展 ``AssistantMessage``——后者是 adapter 共享的传输
        #: 模型，加字段会牵动所有供应商的序列化。
        self.last_warnings: list[str] = []

    # -- 非流式 ------------------------------------------------------------

    async def complete(self, task: Task, *, trace_id: str | None = None) -> AssistantMessage:
        started = self.clock.now()
        decision = self.router.route(task)
        # 执行层的处置事实（重试次数 / 是否降级 / 最终处置 / 告警）经由 trace 回传，
        # 否则落库的 attempt/retry/fallback 恒为默认值，"妥善记录"就是空话。
        trace = ExecutionTrace()
        message = await self.router.execute(task, trace=trace)
        self.last_warnings = list(trace.warnings)
        await self._record(
            task=task,
            decision=decision,
            message=message,
            started=started,
            first_delta_at=None,
            chunk_count=0,
            trace_id=trace_id,
            trace=trace,
        )
        return message

    # -- 流式 --------------------------------------------------------------

    async def stream_sse(self, task: Task, *, trace_id: str | None = None) -> AsyncIterator[bytes]:
        """把统一事件流编码为对外 SSE 字节流。"""
        started = self.clock.now()
        decision, stream = self.router.stream(task)
        first_delta_at: float | None = None
        chunk_count = 0

        try:
            async for event in stream:
                if event.type in BUSINESS_DELTA_TYPES and first_delta_at is None:
                    first_delta_at = self.clock.now()
                chunk_count += 1
                for frame in encode_event(event):
                    yield frame
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开：取消上游请求，释放并发槽。此处不能再 yield。
            with contextlib.suppress(Exception):
                await stream.cancel()
            raise
        else:
            message = stream.result_nowait()
            if message is None:  # pragma: no cover - 正常迭代结束必有终态
                message = await stream.result()
            await self._record(
                task=task,
                decision=decision,
                message=message,
                started=started,
                first_delta_at=first_delta_at,
                chunk_count=chunk_count,
                trace_id=trace_id,
            )

    # -- 记录 --------------------------------------------------------------

    async def _record(
        self,
        *,
        task: Task,
        decision,
        message: AssistantMessage,
        started: float,
        first_delta_at: float | None,
        chunk_count: int,
        trace_id: str | None,
        trace: ExecutionTrace | None = None,
    ) -> None:
        if self.storage is None:
            return

        ended = self.clock.now()
        model = decision.primary
        record = CallRecord(
            trace_id=trace_id or "",
            run_id=str(task.metadata.get("run_id", "")),
            step_id=str(task.metadata.get("step_id", "")),
            call_id=f"call-{uuid.uuid4().hex[:12]}",
            prompt=PromptInfo(
                name=str(task.metadata.get("prompt_name", "")),
                version=str(task.metadata.get("prompt_version", "")),
                sha256=task.prompt_fingerprint(),
                schema_version=str(task.metadata.get("prompt_schema_version", "")),
            ),
            profile=decision.profile.name if decision.profile else "",
            provider=model.provider if model else "",
            model=model.id if model else "",
            api=model.api if model else "",
            usage=message.usage,
            latency=LatencyBreakdown(
                ttft_ms=(first_delta_at - started) * 1000.0 if first_delta_at is not None else 0.0,
                generation_ms=(ended - first_delta_at) * 1000.0 if first_delta_at is not None else 0.0,
                total_ms=(ended - started) * 1000.0,
            ),
            # 弹性维度真实落库：重试次数、是否降级、最终处置。流式不跨模型降级，
            # 因此 stream_sse 调用 _record 时 trace 为 None，保持默认 1/0/0。
            resilience=ResilienceInfo(
                attempt=trace.attempts if trace else 1,
                retry=trace.retries if trace else 0,
                fallback=trace.fallback if trace else False,
                disposition=trace.disposition if trace else "",
            ),
            finish_reason=message.raw_stop_reason or message.stop_reason,
            terminal=message.terminal_state(),
            error_code=_error_code_from(message),
            error_message=message.error_message,
            stream_chunk_count=chunk_count,
        )
        await self.storage.save_call(record)


def _error_code_from(message: AssistantMessage) -> ErrorCode | None:
    """从错误消息前缀还原稳定错误码。

    装配器统一以 ``"<CODE>: <detail>"`` 写错误消息，因此这里可以精确还原，
    不必再对文案做模糊匹配。
    """
    if message.stop_reason != "error" or not message.error_message:
        return None
    prefix = message.error_message.split(":", 1)[0].strip()
    try:
        return ErrorCode(prefix)
    except ValueError:
        return ErrorCode.UNKNOWN


# --------------------------------------------------------------------------
# SSE 编码
# --------------------------------------------------------------------------


def encode_event(event: AssistantEvent) -> list[bytes]:
    """把一个统一事件编码为 SSE 帧。

    终态约定：``done`` 额外发 ``data: [DONE]``；``error`` / ``cancelled``
    **不发** —— 客户端据此区分正常结束与异常结束。
    """
    payload = _event_payload(event)
    frames = [encode_sse(json.dumps(payload, ensure_ascii=False), event=event.type)]
    if event.type == "done":
        frames.append(encode_sse("[DONE]"))
    return frames


def _event_payload(event: AssistantEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": event.type}
    for key, value in asdict(event).items():
        if key == "type":
            continue
        payload[key] = _serialize(value)
    return payload


def _serialize(value: Any) -> Any:
    if isinstance(value, AssistantMessage):
        return {
            "text": value.text(),
            "stop_reason": value.stop_reason,
            "terminal": value.terminal_state(),
            "error_message": value.error_message,
            "response_model": value.response_model,
            "usage": {
                "input": value.usage.input,
                "output": value.usage.output,
                "cache_read": value.usage.cache_read,
                "cache_write": value.usage.cache_write,
                "reasoning": value.usage.reasoning,
                "total_tokens": value.usage.effective_total_tokens(),
                "cost": value.usage.cost.total,
            },
            "tool_calls": [asdict(call) for call in value.tool_calls()],
        }
    return value


# --------------------------------------------------------------------------
# FastAPI 应用
# --------------------------------------------------------------------------


def _parse_task(raw: bytes) -> Task:
    """两层校验 → 统一 Task；失败转成对应 HTTP 状态码。"""
    try:
        data = validate_syntax(raw)
    except SyntaxViolation as exc:
        raise HTTPException(status_code=400, detail={"code": ErrorCode.REQUEST_INVALID.value, "message": str(exc)})
    try:
        return validate_schema(data)
    except SchemaViolation as exc:
        raise HTTPException(status_code=422, detail={"code": ErrorCode.REQUEST_INVALID.value, "message": str(exc)})


def agent_router(service: GatewayService) -> APIRouter:
    """agent 对外 HTTP 契约的路由表：``/health`` 与 ``/v1/tasks``。

    抽成 router 而不是直接建应用，是为了让组合根把**同一份**路由挂到两种应用上：
    只含 agent API 的应用（测试用的 ``create_app``）与"控制台 + agent API"的
    运行时应用（``runtime.create_runtime_app``），避免两处各写一份定义。
    """
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/v1/tasks")
    async def create_task(request: Request) -> JSONResponse:
        task = _parse_task(await request.body())
        message = await service.complete(task, trace_id=request.headers.get("x-trace-id"))
        return JSONResponse(
            {
                "task_id": task.task_id,
                "stop_reason": message.stop_reason,
                "terminal": message.terminal_state(),
                "text": message.text(),
                "error_message": message.error_message,
                # 不阻塞但需知会的告警，例如"认证失败已换模型继续"。
                "warnings": service.last_warnings,
                "usage": {
                    "input": message.usage.input,
                    "output": message.usage.output,
                    "total_tokens": message.usage.effective_total_tokens(),
                    "cost": message.usage.cost.total,
                },
            }
        )

    @router.post("/v1/tasks:stream")
    async def stream_task(request: Request) -> StreamingResponse:
        task = _parse_task(await request.body())
        return StreamingResponse(
            service.stream_sse(task, trace_id=request.headers.get("x-trace-id")),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    return router


def create_app(service: GatewayService) -> FastAPI:
    """构造**只含 agent API** 的 FastAPI 应用。

    服务实例由调用方注入，便于测试直接塞入带 mock transport 的 Router。
    真实进程走的是 ``runtime.create_runtime_app``——它在同一份路由表之外
    还挂载了控制台。
    """
    app = FastAPI(title="LLM Gateway", version="0.6.0")
    app.include_router(agent_router(service))
    return app