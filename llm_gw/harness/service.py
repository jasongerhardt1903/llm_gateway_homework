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

携带请求的 agent 接口受简单口令保护：口令**可在控制台网页上配置**（需求 Harness 层
功能第 1 条），以 ``Authorization: Bearer <password>`` 提交；未配置口令时不强制。
环境变量 ``LLM_GW_AGENT_PASSWORD`` 仍然生效且**优先级高于**网页配置——容器化部署
不必把口令写进数据库。``/health`` 与控制台 ``/api/*`` 不在保护范围内。

流式路径同样支持降级（需求 adapter 层第 8 条"降级时按路由中可用模型执行"）：
若**首个业务 delta 之前**就收到可降级的错误终态，本次流被取消、改用备用路由重开
一条干净的流，对外仍只有一个终态。一旦吐过业务 delta 就不再换模型——那会产生
重复内容（需求："已流式输出 → 不盲目重新生成"）。

每次与后端 agent 的通讯都在 ``exchanges`` 里留一条原始往来记录（需求 Harness 层功能
第 3 条），包含**被两层校验挡下的那类**——它们一次模型调用都没有，``requests`` 表里
查不到，而 agent 最常踩的恰恰是 schema 错误。落库的状态是通讯的真实结局，不是 HTTP 码：
非流式调用模型失败时 HTTP 仍是 200（状态码只表达"请求本身合法"）。

版本：0.8.5
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import __version__
from ..core.errors import ErrorCode, ErrorDisposition
from ..core.events import AssistantEvent, ErrorEvent
from ..core.messages import AssistantMessage, Model
from ..core.schema import SchemaViolation, SyntaxViolation, Task, validate_schema, validate_syntax
from ..core.telemetry import CallRecord, LatencyBreakdown, PromptInfo, ResilienceInfo
from ..harness.decisions import disposition_for
from ..harness.sse import encode_sse
from ..router.router import ExecutionTrace, Router
from ..util.clock import Clock, RealClock
from .query import Query
from .storage import Storage

__all__ = [
    "AGENT_PASSWORD_ENV",
    "CONFIG_AGENT_PASSWORD",
    "GatewayService",
    "agent_password_from_env",
    "agent_router",
    "create_app",
    "require_agent_password",
    "BUSINESS_DELTA_TYPES",
]

#: 计入 TTFT 的"有业务意义"的事件类型。
#: ``start`` 只是连接建立信号，把它当首 token 会系统性低估 TTFT。
BUSINESS_DELTA_TYPES: frozenset[str] = frozenset({"text_delta", "thinking_delta", "toolcall_delta"})

#: 流式路径上允许"首 delta 前换模型"的处置。
#:
#: - ``DEGRADE``：决策表要求"更换路由表中下一个模型"，换模型是正解；
#: - ``RETRY``：决策表写的是"**有限重试或 fall back**"，流式不做同模型重试
#:   （重开一条流与换模型的代价相同，而换模型还多一次机会），因此取 fall back 一支。
#:
#: ``FAIL`` 一律不换：换模型也不会变好（请求非法、已流式输出后中断等）。
_STREAM_FALLBACK_DISPOSITIONS: frozenset[ErrorDisposition] = frozenset(
    {ErrorDisposition.DEGRADE, ErrorDisposition.RETRY}
)


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
        #: 控制台上配置的 agent 口令（需求 Harness 层功能第 1 条）；``None`` 表示没配。
        #: 进程启动时由 :meth:`load_agent_password` 从 config 表恢复。
        self.console_password: str | None = None

    # -- agent 口令 --------------------------------------------------------

    async def load_agent_password(self) -> None:
        """从 config 表恢复控制台配置的口令（启动时调用一次）。"""
        if self.storage is None:
            return
        stored = await self.storage.load_config(CONFIG_AGENT_PASSWORD)
        self.console_password = str(stored) if stored else None

    async def set_agent_password(self, password: str | None) -> None:
        """设置控制台上配置的口令；``None`` / 空串表示清除。

        只在配置成"本次会话"还不够——重启后口令不能凭空消失，因此同时写 config 表。
        """
        self.console_password = password or None
        if self.storage is not None:
            await self.storage.save_config(CONFIG_AGENT_PASSWORD, self.console_password)

    def effective_password(self) -> str | None:
        """本次生效的口令。

        **环境变量优先**：容器化部署可以完全不碰数据库就把接口设防，同时网页配置
        仍然服务本地/单机场景。
        """
        return agent_password_from_env() or self.console_password

    def password_source(self) -> str:
        """口令来源：``env`` / ``console`` / ``none``，供页面如实提示配置位置。"""
        if agent_password_from_env():
            return "env"
        return "console" if self.console_password else "none"

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

    async def stream_sse(
        self,
        task: Task,
        *,
        trace_id: str | None = None,
        exchange_request_raw: bytes | str | None = None,
        endpoint: str = "",
    ) -> AsyncIterator[bytes]:
        """把统一事件流编码为对外 SSE 字节流。

        候选链是 ``[主路由] + [备用路由]``。**换模型只发生在首 delta 之前**：那时客户端
        一个业务字节都没收到，重开一条流不会造成重复内容，正是需求 adapter 层第 8 条
        "降级时按路由中可用模型执行"要覆盖的路径。首 delta 之后的错误一律如实上报——
        需求明令"已流式输出 → 不盲目重新生成"。

        对外仍然只有一个终态：被放弃的那条流的 ``error`` 事件**不会**转发给客户端，
        只有最后落地的那条流才产出终态事件与可选的 ``[DONE]``。

        同时把"与后端 agent 的原始往来"落进 ``exchanges``（需求 Harness 层功能第 3 条）：
        ``response_raw`` 是**实际发出的 SSE 帧原文**（含 ``event:`` / ``data:`` 行），
        不是渲染后的结果，这样日志页才能提供"raw data 模式"。被放弃的那条流的帧不进
        记录——客户端从没收到过它们。
        """
        started = self.clock.now()
        decision = self.router.route(task)
        first_delta_at: float | None = None
        chunk_count = 0
        # attempts 从 1 起算：第一条流无论成功与否都算一次真实调用。
        trace = ExecutionTrace(attempts=1)
        # 实际发给客户端的字节，供落 exchanges 用。
        sent: list[bytes] = []

        plan: list[Model | None] = [decision.primary]
        if decision.backup is not None:
            plan.append(decision.backup)

        stream = None
        served_model: Model | None = None

        try:
            for index, model in enumerate(plan):
                nxt = plan[index + 1] if index + 1 < len(plan) else None
                _, current = self.router.stream(task, model=model)
                stream = current
                served_model = model
                emitted = False
                degrade_code: ErrorCode | None = None

                async for event in current:
                    if event.type in BUSINESS_DELTA_TYPES:
                        emitted = True
                        if first_delta_at is None:
                            first_delta_at = self.clock.now()
                    # 首 delta 之前、且还有下一个候选、且该错误允许换模型 → 放弃这条流。
                    if (
                        event.type == "error"
                        and not emitted
                        and nxt is not None
                        and disposition_for(_event_code(event)) in _STREAM_FALLBACK_DISPOSITIONS
                    ):
                        degrade_code = _event_code(event)
                        break
                    chunk_count += 1
                    for frame in encode_event(event):
                        sent.append(frame)
                        yield frame
                else:
                    # 迭代自然结束：终态已转发给客户端，收工。
                    break

                # 命中"首 delta 前降级"：取消本次上游，记下弹性事实，换下一个模型重开。
                with contextlib.suppress(Exception):
                    await current.cancel()
                trace.fallback = True
                trace.attempts += 1
                trace.degraded_from = served_model.label() if served_model else ""
                trace.degraded_to = nxt.label()  # type: ignore[union-attr] - nxt 非空由上面的条件保证
                trace.disposition = disposition_for(degrade_code).value  # type: ignore[arg-type]
                trace.warnings.append(
                    f"{degrade_code.value if degrade_code else ErrorCode.UNKNOWN.value}: "
                    f"首 token 前失败，已降级 {trace.degraded_from} → {trace.degraded_to}"
                )
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开：取消上游请求，释放并发槽。此处不能再 yield，只补一条
            # "本次通讯被中断"的记录——否则最需要排查的那类中断在日志里反而没有痕迹。
            with contextlib.suppress(Exception):
                await current.cancel()
            with contextlib.suppress(Exception):
                await self._record_exchange(
                    task=task,
                    trace_id=trace_id,
                    endpoint=endpoint,
                    request_raw=exchange_request_raw,
                    response_raw=sent,
                    stream=True,
                    status="cancelled",
                    started=started,
                )
            raise

        if stream is None:  # pragma: no cover - plan 至少含一项
            return
        message = stream.result_nowait()
        if message is None:
            message = await stream.result()
        await self._record(
            task=task,
            decision=decision,
            message=message,
            started=started,
            first_delta_at=first_delta_at,
            chunk_count=chunk_count,
            trace_id=trace_id,
            trace=trace,
            model=served_model,
        )
        await self._record_exchange(
            task=task,
            trace_id=trace_id,
            endpoint=endpoint,
            request_raw=exchange_request_raw,
            response_raw=sent,
            stream=True,
            status=message.terminal_state(),
            error_code=_error_code_from(message),
            error_message=message.error_message,
            started=started,
            meta={
                "model": served_model.label() if served_model else "",
                "profile": decision.profile.name if decision.profile else "",
                "degraded_from": trace.degraded_from,
                "degraded_to": trace.degraded_to,
            },
        )

    # -- 通讯原始往来数据（需求 Harness 层功能第 3 条） ---------------------

    async def record_exchange(
        self,
        *,
        task_id: str,
        request_raw: str | bytes = "",
        response_raw: str = "",
        trace_id: str | None = None,
        endpoint: str = "",
        stream: bool = False,
        status: str = "done",
        error_code: ErrorCode | str | None = None,
        error_message: str | None = None,
        duration_ms: float = 0.0,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """对外暴露的通讯落库入口，供非流式的 agent 端点调用。"""
        if self.storage is None:
            return None
        return await self.storage.save_exchange(
            task_id=task_id,
            request_raw=request_raw,
            response_raw=response_raw,
            trace_id=trace_id or "",
            endpoint=endpoint,
            stream=stream,
            status=status,
            error_code=error_code.value if isinstance(error_code, ErrorCode) else error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            meta=meta,
        )

    async def _record_exchange(
        self,
        *,
        task: Task,
        trace_id: str | None,
        endpoint: str,
        request_raw: bytes | str | None,
        response_raw: list[bytes] | str,
        stream: bool,
        status: str,
        error_code: ErrorCode | None = None,
        error_message: str | None = None,
        started: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """流式路径的内部落库：把累积的 SSE 帧拼成原文，并补上耗时。"""
        if self.storage is None:
            return
        raw_response = (
            b"".join(response_raw).decode("utf-8", "replace")
            if isinstance(response_raw, list)
            else response_raw
        )
        await self.record_exchange(
            task_id=task.task_id,
            request_raw=request_raw or "",
            response_raw=raw_response,
            trace_id=trace_id,
            endpoint=endpoint,
            stream=stream,
            status=status,
            error_code=error_code,
            error_message=error_message,
            duration_ms=(self.clock.now() - started) * 1000.0,
            meta=meta,
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
        model: Model | None = None,
    ) -> None:
        if self.storage is None:
            return

        ended = self.clock.now()
        # 实际服务本次请求的模型：流式降级后它不是 ``decision.primary``，
        # 若照抄主路由就会把"降级到了哪个模型"记成没降级。
        model = model if model is not None else decision.primary
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
            # 弹性维度真实落库：重试次数、是否降级、最终处置。流式路径只在
            # "首 delta 前"降级，因此 attempt 记的是实际开过的流数、disposition 记的是
            # 触发降级的那个处置；没降级时 trace 保持默认 1/0/0。
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


def _event_code(event: AssistantEvent) -> ErrorCode:
    """从错误终态事件里还原稳定错误码，供降级判定使用。

    只有 ``ErrorEvent`` 才有错误码；其余事件（正常 ``done`` / 客户端 ``cancelled``）
    按 ``UNKNOWN`` 处理——``UNKNOWN`` 的处置是 ``FAIL``，因此不会触发降级。
    """
    if isinstance(event, ErrorEvent):
        return _error_code_from(event.error)
    return ErrorCode.UNKNOWN


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


def _task_id_hint(raw: bytes) -> str:
    """校验失败时也要能归组：尽力从原始报文里把 ``task_id`` 抠出来。

    需求要求按"task id / 每次通讯流程"两级组织通讯日志，而校验失败的报文恰恰也要
    出现在日志里（否则 agent 最常踩的 schema 错误反而查不到）。语法合法的报文能拿到
    ``task_id``；连 JSON 都不合法的取不到，归到空串一组。
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if isinstance(data, dict) and isinstance(data.get("task_id"), str):
        return data["task_id"]
    return ""


#: agent 接口口令所在的环境变量名。
#:
#: 环境变量这条路径保留且**优先于**网页配置：口令是部署期凭据，容器化部署不必把
#: 它写进 ``llm_gw.sqlite3``（"把库拷走"就等于"拿到口令"）。网页配置是为了让
#: 单机/本地场景不必改名环境变量重启进程——需求 Harness 层功能第 1 条要求
#: "password 通过网页配置"。
AGENT_PASSWORD_ENV = "LLM_GW_AGENT_PASSWORD"

#: config 表中存放"控制台配置的口令"的键名。
CONFIG_AGENT_PASSWORD = "agent_password"


def agent_password_from_env() -> str | None:
    """读取环境变量中的口令；空串视同未配置（``.env`` 里写 ``KEY=`` 很常见）。"""
    return os.environ.get(AGENT_PASSWORD_ENV) or None


def require_agent_password(service: GatewayService):
    """构造 agent 接口的鉴权依赖（需求：Harness 层功能第 1 条）。

    做成工厂而不是无参依赖：口令现在有两个来源（环境变量 + 网页配置），判定需要
    服务实例；闭包比把服务塞进模块级全局变量更安全，也便于测试。

    规则：

    * 未配置口令时**放行**——本地开发与 TDD 迭代不必先造一个口令；
    * 配置后要求 ``Authorization: Bearer <password>``，否则 401 + ``AUTH_REQUIRED``；
    * 只作用于 agent 接口（``/v1/tasks`` 与 ``/v1/tasks:stream``）。控制台
      ``/api/*`` 不挂这个依赖，否则页面自己都打不开；``/health`` 也刻意豁免，
      探活程序通常不带凭证。
    """

    async def dependency(request: Request) -> None:
        expected = service.effective_password()
        if expected is None:
            return
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        # 用 compare_digest 做定时安全比较：== 会在首个不同字符处短路，
        # 反复请求就能按耗时把口令逐字节猜出来。
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=401,
                detail={"code": ErrorCode.AUTH_REQUIRED.value, "message": "agent 接口口令无效"},
                headers={"WWW-Authenticate": "Bearer"},
            )

    return dependency


async def _record_rejected(
    service: GatewayService,
    *,
    request: Request,
    raw: bytes,
    endpoint: str,
    exc: HTTPException,
) -> None:
    """把被两层校验挡下的通讯也记进 exchanges（需求 Harness 层功能第 3 条）。

    这一类通讯**一次模型调用都没有**，``requests`` 表里没有对应行；但 agent 最常踩的
    恰恰是 schema 错误，"这次我到底发了什么"只有在通讯日志里才看得到，所以不能因为
    "没进路由"就不记。归组用的 ``task_id`` 由 :func:`_task_id_hint` 从原文里抠。

    落库失败不能改变对外行为——错误响应必须照常返回，因此这里的异常一律吞掉。
    """
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    code = str(detail.get("code") or ErrorCode.REQUEST_INVALID.value)
    message = str(detail.get("message") or "")
    with contextlib.suppress(Exception):
        await service.record_exchange(
            task_id=_task_id_hint(raw),
            trace_id=request.headers.get("x-trace-id"),
            endpoint=endpoint,
            request_raw=raw,
            # 如实记下对外回的原文（FastAPI 的 ``{"detail": ...}`` 形态）。
            response_raw=json.dumps({"detail": detail}, ensure_ascii=False),
            stream=endpoint.endswith(":stream"),
            status="error",
            error_code=code,
            error_message=f"{code}: {message}" if message else code,
        )


def agent_router(service: GatewayService) -> APIRouter:
    """agent 对外 HTTP 契约的路由表：``/health`` 与 ``/v1/tasks``。

    抽成 router 而不是直接建应用，是为了让组合根把**同一份**路由挂到两种应用上：
    只含 agent API 的应用（测试用的 ``create_app``）与"控制台 + agent API"的
    运行时应用（``runtime.create_runtime_app``），避免两处各写一份定义。
    """
    router = APIRouter()
    auth = Depends(require_agent_password(service))

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/v1/tasks", dependencies=[auth])
    async def create_task(request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            task = _parse_task(raw)
        except HTTPException as exc:
            await _record_rejected(service, request=request, raw=raw, endpoint="/v1/tasks", exc=exc)
            raise
        started = service.clock.now()
        message = await service.complete(task, trace_id=request.headers.get("x-trace-id"))
        body = {
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
        await service.record_exchange(
            task_id=task.task_id,
            trace_id=request.headers.get("x-trace-id"),
            endpoint="/v1/tasks",
            request_raw=raw,
            response_raw=json.dumps(body, ensure_ascii=False),
            stream=False,
            # 模型调用失败时 HTTP 仍是 200（状态码只表达"请求本身合法"），但通讯日志的
            # 状态列要说实话，否则页面上一片绿色而正文里全是错误。
            status=message.terminal_state(),
            error_code=_error_code_from(message),
            error_message=message.error_message,
            duration_ms=(service.clock.now() - started) * 1000.0,
        )
        return JSONResponse(body)

    @router.post("/v1/tasks:stream", dependencies=[auth])
    async def stream_task(request: Request) -> StreamingResponse:
        raw = await request.body()
        try:
            task = _parse_task(raw)
        except HTTPException as exc:
            await _record_rejected(service, request=request, raw=raw, endpoint="/v1/tasks:stream", exc=exc)
            raise
        return StreamingResponse(
            service.stream_sse(
                task,
                trace_id=request.headers.get("x-trace-id"),
                exchange_request_raw=raw,
                endpoint="/v1/tasks:stream",
            ),
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
    app = FastAPI(title="LLM Gateway", version=__version__)
    app.include_router(agent_router(service))
    return app