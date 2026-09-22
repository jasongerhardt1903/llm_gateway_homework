"""Harness 服务：对外 HTTP 接口 + SSE 流式。

需求："adapter 层默认采用 SSE 方式和 LLM 进行通讯"、"gateway 内部维护事件序列，
对外统一为 SSE 协议"、"流式结束只有一个原因"。

这一层负责把统一事件序列编码成对外的 SSE，并保证三件事：

1. **两层校验**：先校验语法（400），再校验 schema（422），错误码稳定。
2. **单一终态**：正常完成发 ``event: done`` + ``data: [DONE]``；错误/取消只发
   终态事件，**不发 [DONE]** —— 客户端据此区分"正常结束"与"异常结束"。
3. **可观测**：每次调用（含流式）都落一条 :class:`CallRecord`，TTFT 以
   **第一个有业务意义的 delta** 为准，不把 ``start`` 事件算作首 token。

**一次尝试一条记录**：候选链上的每次尝试各落一条 :class:`CallRecord`（共享同一个
``trace_id``，用 ``attempt_index`` 标位次），沿途带上该次尝试的 ``route`` 决策快照与
``degraded_from``。只落最终那一条会让"先试了 A 失败、又试了 B 成功"缩成一行，失败、
重试与降级在 Trace 里全部不可见——而每一次尝试都是真实消耗过配额的上游调用。

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

版本：0.8.8
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
from ..core.schema import (
    PromptRef,
    SchemaViolation,
    SyntaxViolation,
    Task,
    output_validity,
    validate_schema,
    validate_syntax,
)
from ..core.telemetry import CallRecord, LatencyBreakdown, PromptInfo, ResilienceInfo, RouteInfo
from ..harness.decisions import disposition_for
from ..harness.sse import encode_sse
from ..router.router import AttemptResult, ExecutionTrace, Router
from ..router.rules import Decision
from ..util.clock import Clock, RealClock
from .prompts import PromptTemplateError
from .query import Query
from .ratelimit import ModelRateLimiter, RateLimitDecision
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
        limiter: ModelRateLimiter | None = None,
    ) -> None:
        self.router = router
        self.storage = storage
        self.clock: Clock = clock or RealClock()
        self.query = Query(storage) if storage is not None else None
        #: 按模型独立的令牌桶。默认不限额（``RateLimit()` 未启用），真实进程在组合根
        #: 里按环境变量注入——本地开发不该被限流绊住。
        self.limiter = limiter or ModelRateLimiter()
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

    # -- 提示词模板（需求「提示词版本管理」：存储 / 变量替换 / 版本引用） -------

    async def resolve_prompt(self, task: Task) -> Task:
        """把 ``task.prompt`` 引用渲染成 system 提示，其余字段原样保留。

        ``version`` 缺省时取最新版本；模板里用 ``{{变量}}`` 占位，缺变量/多给变量都
        由 :meth:`PromptTemplate.render` 报错（宁可比对失败，也不要静默产出残缺提示）。
        渲染结果**追加在原 system 之前**，让模板当背景、调用方当即时指令。
        """
        ref = task.prompt
        if ref is None or self.storage is None:
            return task
        template = await self.storage.prompt(ref.name, ref.version)
        if template is None:
            target = f"{ref.name}@{ref.version}" if ref.version else ref.name
            raise PromptTemplateError(f"模板 {target} 不存在")
        rendered = template.render(ref.variables)
        system = rendered if not task.input.system else f"{rendered}\n\n{task.input.system}"
        return task.model_copy(
            update={
                "input": task.input.model_copy(update={"system": system}),
                # 记录**实际使用**的版本：缺省引用在解析后才确定落到了哪一版。
                "prompt": PromptRef(
                    name=template.name, version=template.version, variables=ref.variables
                ),
            }
        )

    # -- 限流（需求「韧性基础」：按模型独立限流，超限 429） -----------------

    def check_rate_limit(self, task: Task) -> RateLimitDecision | None:
        """按本 task 即将落到的模型取一个令牌。

        要先路由才知道"是哪个模型"，因此这里算一次路由决策。路由是纯内存查表（无
        I/O），HTTP 层随后真正执行时会再算一次；两次之间注册表理论上可能被并发调用
        改动（``record_usage`` 参与动态打分），但桶是按模型各自持有的，即便落到另一个
        模型，限流也只作用在它自己身上，不会误伤。

        返回 ``None`` 表示没有可用候选——这种情况该由路由如实报
        ``ROUTE_NO_CANDIDATE``，限流不该抢在它前面给出一个更容易误导的 429。
        """
        decision = self.router.route(task)
        if decision.primary is None:
            return None
        return self.limiter.check(decision.primary.label())

    # -- 非流式 ------------------------------------------------------------

    async def complete(self, task: Task, *, trace_id: str | None = None) -> AssistantMessage:
        decision = self.router.route(task)
        # 执行层的处置事实（重试次数 / 是否降级 / 最终处置 / 告警）经由 trace 回传，
        # 否则落库的 attempt/retry/fallback 恒为默认值，"妥善记录"就是空话。
        trace = ExecutionTrace()

        async def on_attempt(attempt: AttemptResult) -> None:
            """候选链上的每次尝试各落一条记录。

            只落最终那一条（旧做法）会让"先试了 A 失败、又试了 B 成功"在 Trace 里缩成
            一行，失败与重试完全不可见；而 A 那次是真实消耗过配额的上游调用。
            """
            await self._record(
                task=task,
                decision=decision,
                message=attempt.message,
                model=attempt.model,
                trace_id=trace_id,
                started=attempt.started,
                ended=attempt.ended,
                attempt_index=attempt.index,
                degraded_from=attempt.degraded_from,
                attempt=attempt.attempt,
                retry=attempt.retry,
                disposition=attempt.disposition,
            )

        message = await self.router.execute(task, trace=trace, on_attempt=on_attempt)
        self.last_warnings = list(trace.warnings)
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
        chunk_count = 0
        # attempts 从 1 起算：第一条流无论成功与否都算一次真实调用。
        trace = ExecutionTrace(attempts=1)
        # 实际发给客户端的字节，供落 exchanges 用。
        sent: list[bytes] = []

        # 整条候选链：profile 里配了几个模型就有几次机会（主备只是前两名）。
        plan: list[Model | None] = list(decision.candidates) or [decision.primary]

        stream = None
        served_model: Model | None = None
        served_index = 1
        # 上一个失败的模型标签，作为本次尝试的 ``degraded_from``。
        previous = ""
        attempt_started = self.clock.now()
        attempt_first_delta_at: float | None = None

        try:
            for index, model in enumerate(plan, start=1):
                nxt = plan[index] if index < len(plan) else None
                attempt_started = self.clock.now()
                attempt_first_delta_at = None
                _, current = self.router.stream(task, model=model)
                stream = current
                served_model = model
                served_index = index
                emitted = False
                degrade_code: ErrorCode | None = None

                async for event in current:
                    if event.type in BUSINESS_DELTA_TYPES:
                        emitted = True
                        if attempt_first_delta_at is None:
                            attempt_first_delta_at = self.clock.now()
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

                # 命中"首 delta 前降级"：取消本次上游，**先把这次失败的尝试落一条记录**，
                # 再换下一个模型重开。不落的话"试过但失败"在 Trace 里没有痕迹，只剩最终
                # 成功那一行，"降级到底有没有生效"就无从判断。
                with contextlib.suppress(Exception):
                    await current.cancel()
                ended = self.clock.now()
                abandoned = current.result_nowait()
                if abandoned is None:  # pragma: no cover - error 终态已入流，结果理应就绪
                    abandoned = _error_message(
                        f"{degrade_code.value if degrade_code else ErrorCode.UNKNOWN.value}: 首 token 前失败"
                    )
                disposition = disposition_for(degrade_code).value if degrade_code else ""
                served_from = model.label() if model is not None else ""
                await self._record(
                    task=task,
                    decision=decision,
                    message=abandoned,
                    model=model,
                    trace_id=trace_id,
                    started=attempt_started,
                    ended=ended,
                    attempt_index=index,
                    degraded_from=previous,
                    disposition=disposition,
                )
                trace.fallback = True
                trace.attempts += 1
                trace.degraded_from = served_from
                trace.degraded_to = nxt.label()  # type: ignore[union-attr] - nxt 非空由上面的条件保证
                trace.disposition = disposition
                trace.warnings.append(
                    f"{degrade_code.value if degrade_code else ErrorCode.UNKNOWN.value}: "
                    f"首 token 前失败，已降级 {trace.degraded_from} → {trace.degraded_to}"
                )
                previous = served_from
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
            model=served_model,
            trace_id=trace_id,
            started=attempt_started,
            ended=self.clock.now(),
            first_delta_at=attempt_first_delta_at,
            chunk_count=chunk_count,
            attempt_index=served_index,
            degraded_from=previous,
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
        decision: Decision,
        message: AssistantMessage,
        model: Model | None,
        trace_id: str | None,
        started: float,
        ended: float | None = None,
        first_delta_at: float | None = None,
        chunk_count: int = 0,
        attempt_index: int = 1,
        degraded_from: str = "",
        attempt: int = 1,
        retry: int = 0,
        disposition: str = "",
    ) -> None:
        """落**一次尝试**的调用记录（候选链上有几次尝试就落几行，共享 trace_id）。

        ``model`` 是本次尝试真正打到的模型，**不能**照抄 ``decision.primary``：降级之后
        两者不同，照抄会把"降到了哪个模型"记成没降级。
        """
        if self.storage is None:
            return

        ended = self.clock.now() if ended is None else ended
        record = CallRecord(
            trace_id=trace_id or "",
            run_id=str(task.metadata.get("run_id", "")),
            step_id=str(task.metadata.get("step_id", "")),
            call_id=f"call-{uuid.uuid4().hex[:12]}",
            prompt=PromptInfo(
                # 结构化引用优先：``resolve_prompt`` 会把缺省版本补成实际版本，
                # 因此这里记的 name/version 是真正渲染过的那一版。仅当调用方仍走
                # 非结构化的 metadata 约定时，才回落到 metadata 里的自由文本。
                name=str(task.prompt.name)
                if task.prompt
                else str(task.metadata.get("prompt_name", "")),
                version=(str(task.prompt.version or "") if task.prompt else str(task.metadata.get("prompt_version", ""))),
                sha256=task.prompt_fingerprint(),
                schema_version=str(task.metadata.get("prompt_schema_version", "")),
            ),
            profile=decision.profile.name if decision.profile else "",
            provider=model.provider if model else "",
            model=model.id if model else "",
            api=model.api if model else "",
            # 路由决策快照随每条记录落库："为什么选它 / 为什么不选它"要能在线下复盘，
            # 否则线上只能看到一个模型名，决策过程无从还原。
            route=RouteInfo(
                reason=decision.reason,
                candidates=[candidate.label() for candidate in decision.candidates],
                rejected=[
                    {"model": rejected.label(), "reason": reason}
                    for rejected, reason in decision.rejected
                ],
            ),
            usage=message.usage,
            latency=LatencyBreakdown(
                ttft_ms=(first_delta_at - started) * 1000.0 if first_delta_at is not None else 0.0,
                generation_ms=(ended - first_delta_at) * 1000.0 if first_delta_at is not None else 0.0,
                total_ms=(ended - started) * 1000.0,
            ),
            # 弹性维度记的是**这一次尝试**：这一跳对上游调了几次、是否重试、判定为什么处置、
            # 排在候选链第几位、由谁降级而来。整条请求的汇总见执行层的 ``ExecutionTrace``。
            resilience=ResilienceInfo(
                attempt=attempt,
                retry=retry,
                fallback=bool(degraded_from) or disposition in {"degrade", "retry"},
                disposition=disposition,
                attempt_index=attempt_index,
                degraded_from=degraded_from,
            ),
            finish_reason=message.raw_stop_reason or message.stop_reason,
            terminal=message.terminal_state(),
            output_valid=_output_validity(task, message),
            error_code=_error_code_from(message),
            error_message=message.error_message,
            stream_chunk_count=chunk_count,
        )
        await self.storage.save_call(record)


def _output_validity(task: Task, message: AssistantMessage) -> bool | None:
    """本次输出是否兑现了请求里的结构化约束。

    失败/取消的调用压根没有可判的输出，记 ``None``——把"没跑成"记成"输出不合格"会让
    ``output_valid`` 这个维度同时混进两类完全不同的故障。
    """
    if message.terminal_state() != "done":
        return None
    return output_validity(task.input, message.text())


def _event_code(event: AssistantEvent) -> ErrorCode:
    """从错误终态事件里还原稳定错误码，供降级判定使用。

    只有 ``ErrorEvent`` 才有错误码；其余事件（正常 ``done`` / 客户端 ``cancelled``）
    按 ``UNKNOWN`` 处理——``UNKNOWN`` 的处置是 ``FAIL``，因此不会触发降级。
    """
    if isinstance(event, ErrorEvent):
        return _error_code_from(event.error)
    return ErrorCode.UNKNOWN


def _error_message(error: str) -> AssistantMessage:
    """造一条 ``"<CODE>: <detail>"`` 形态的错误终态，供落库兜底用。"""
    return AssistantMessage(stop_reason="error", error_message=error)


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
    """把被网关入口挡下的通讯也记进 exchanges（需求 Harness 层功能第 3 条）。

    被挡下的有两类，都值得留痕：

    * **两层校验失败**（400 / 422）——这一类通讯**一次模型调用都没有**，``requests``
      表里没有对应行；但 agent 最常踩的恰恰是 schema 错误，"这次我到底发了什么"只有
      在通讯日志里才看得到，所以不能因为"没进路由"就不记。
    * **入口限流**（429）——调用方需要从日志里看出"是被网关限流挡下的，不是上游挂了"，
      否则会一直以为是供应商的问题。

    归组用的 ``task_id`` 由 :func:`_task_id_hint` 从原文里抠。

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


async def _guard_rate_limit(
    service: GatewayService,
    *,
    request: Request,
    raw: bytes,
    task: Task,
    endpoint: str,
) -> None:
    """入口限流：超限就地返回 **429 + ``Retry-After``**。

    刻意**不**把它做成可重试的上游错误：重试只会让桶更空，而且会把"还要等多久"这个
    信息从调用方手里拿走。因此这次请求根本不会进入路由执行，调用方拿到的就是一个
    明确的 429 与他该等的秒数。
    """
    decision = service.check_rate_limit(task)
    if decision is None or decision.allowed:
        return
    seconds = decision.retry_after_seconds()
    exc = HTTPException(
        status_code=429,
        detail={
            "code": ErrorCode.RATE_LIMITED.value,
            "message": (
                f"模型 {decision.model} 已触达本地限流（{decision.rpm:g} RPM），"
                f"请在 {seconds} 秒后重试"
            ),
        },
        headers={"Retry-After": str(seconds)},
    )
    await _record_rejected(service, request=request, raw=raw, endpoint=endpoint, exc=exc)
    raise exc


async def _resolve_prompt(
    service: GatewayService,
    *,
    request: Request,
    raw: bytes,
    task: Task,
    endpoint: str,
) -> Task:
    """把模板引用解析成实际请求；模板不存在或变量对不上 → **422**。

    引用一个不存在的模板、或漏给变量，都属于"请求本身不合法"（422）而不是上游故障，
    所以要在**限流之前**判：先给一个本就该 422 的请求回 429，调用方会误以为"等一会
    再发同样的请求就能成功"。解析产物（补齐 version 的 task）交给后续执行路径。
    """
    try:
        return await service.resolve_prompt(task)
    except PromptTemplateError as exc:
        http_exc = HTTPException(
            status_code=422,
            detail={"code": ErrorCode.PROMPT_INVALID.value, "message": str(exc)},
        )
        await _record_rejected(service, request=request, raw=raw, endpoint=endpoint, exc=http_exc)
        raise http_exc


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
        task = await _resolve_prompt(
            service, request=request, raw=raw, task=task, endpoint="/v1/tasks"
        )
        await _guard_rate_limit(
            service, request=request, raw=raw, task=task, endpoint="/v1/tasks"
        )
        started = service.clock.now()
        message = await service.complete(task, trace_id=request.headers.get("x-trace-id"))
        body = {
            "task_id": task.task_id,
            "stop_reason": message.stop_reason,
            "terminal": message.terminal_state(),
            "text": message.text(),
            "error_message": message.error_message,
            # 结构化输出的兑现结果（与落库的同源）：调用方要的 JSON 到底合不合格，
            # 不必再自己解析一遍就能看到。
            "output_valid": _output_validity(task, message),
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
        # 限流必须在**建流之前**判：StreamingResponse 一旦返回，HTTP 状态码就固定成
        # 200 了，再想表达 429 只能靠流里的 error 事件，那不是"返回 429"。
        task = await _resolve_prompt(
            service, request=request, raw=raw, task=task, endpoint="/v1/tasks:stream"
        )
        await _guard_rate_limit(
            service, request=request, raw=raw, task=task, endpoint="/v1/tasks:stream"
        )
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