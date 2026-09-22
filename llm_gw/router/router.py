"""路由器：把 task 落到具体模型并执行，按处置决策在重试/降级/报错中三选一。

路由决策（选谁）与执行（调用 + 重试 + 降级）放在一起，因为它们共享同一个
``Decision``：只有先知道主备是谁，才谈得上降级。

需求 Harness 层第 91 行要求网关在**重试、降级、报错**三者中选一个。判定数据在
``core/errors.py`` 的 ``ERROR_DECISIONS``，执行落在这里：

- ``RETRY``：先由 :func:`retry_assistant_call` 对**同一**模型有限重试；
- ``DEGRADE``：不重试当前模型，直接换候选链上的下一个模型；
- ``FAIL``：立即返回错误，不做任何后续动作。

两者的差别只在"要不要先同模型重试"；重试耗尽后同样按路由换模型，与需求
「处理策略」列一致。

候选链走的是 :attr:`Decision.candidates` **整条**（而非只取主备两名）：profile 里
配了 4 个模型就该有 4 次机会，否则"降级"在第三个模型起就名存实亡。

版本：0.3.0
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..adapter.base import Adapter, AdapterOptions
from ..core.advanced import AdvancedConfig
from ..core.errors import ErrorCode, ErrorDisposition, classify_error_code
from ..core.events import AssistantEventStream, ErrorEvent
from ..core.messages import AssistantMessage, Model
from ..core.schema import Task
from ..harness.decisions import decision_for, should_auto_retry
from ..harness.retry import RetryCallbacks, RetryPolicy, retry_assistant_call
from ..util.clock import Clock, RealClock
from .profile import GwProfile, resolve_advanced
from .registry import CapabilityRegistry
from .rules import Decision, route as route_task

__all__ = ["AttemptResult", "ExecutionTrace", "Router"]


@dataclass
class ExecutionTrace:
    """本次执行的弹性事实，供 Harness 落库与对外告警。

    需求要求"妥善记录"处置结果，而 :class:`AssistantMessage` 是 adapter 共享的传输
    模型、没有承载元数据的位置，因此执行层把事实写进这个可选的出参，由调用方决定
    怎么落库（Harness 写进 ``CallRecord.resilience``）与怎么对外告警。

    这里是**整条请求的汇总**；逐次尝试的明细见 :class:`AttemptResult`。
    """

    #: 实际调用上游的总次数（含同模型重试与降级后的调用）。
    attempts: int = 0
    #: 同一模型内的重试次数。
    retries: int = 0
    #: 是否发生过降级。
    fallback: bool = False
    #: 最终处置：``retry`` / ``degrade`` / ``fail``；成功完成时为空串。
    disposition: str = ""
    degraded_from: str = ""
    degraded_to: str = ""
    #: 候选链上最后一次尝试的位次（1 起）；未执行任何模型时为 0。
    attempt_index: int = 0
    #: 实际产出最终结果的那个模型。**不是** ``Decision.primary``——降级后两者不同，
    #: 照抄主路由会把"降到了哪个模型"记成没降级。
    served_model: Model | None = None
    #: 不阻塞但需知会的告警（如"认证失败已换模型"）。
    warnings: list[str] = field(default_factory=list)


@dataclass
class AttemptResult:
    """候选链上**一次尝试**的完整结果，供调用方逐条落库。

    需求的可观测性要求"每次 LLM 调用都记录"：一次请求在候选链上试了 3 个模型就是
    3 次真实的上游调用，只落最终那一条会让失败与重试在 Trace 里彻底消失。执行层因此
    用回调把每次尝试交出去，由 Harness 决定怎么记（它才知道 task / trace_id）。
    """

    #: 在候选链里的位次（1 起）。
    index: int
    #: 本次尝试的模型。
    model: Model
    #: 本次尝试的结果（可能是错误终态）。
    message: AssistantMessage
    #: 由哪个模型降级而来（``provider/id``）；首跳为空串。
    degraded_from: str
    #: 本条记录这一跳的处置：``retry`` / ``degrade`` / ``fail``；成功时为空串。
    disposition: str
    #: 是否为本请求最终产出的那条记录（后面的候选不会再试）。
    final: bool
    #: 本次尝试对上游发起的调用次数（含同模型重试）；被工具轮数护栏挡下时为 0。
    attempt: int
    #: 本次尝试内的同模型重试次数。
    retry: int
    #: 本条记录对应的时间窗（含同模型重试在内的整跳耗时）。
    started: float
    ended: float


class Router:
    """能力注册表 + profile 路由规则 + 执行降级。"""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        adapter_for: Callable[[Model], Adapter] | None = None,
        options_for: Callable[[Model, AdvancedConfig], AdapterOptions] | None = None,
        retry_policy: RetryPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.registry = registry
        self._adapter_for = adapter_for
        self._options_for = options_for or (lambda model, advanced: AdapterOptions(advanced=advanced))
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock: Clock = clock or RealClock()

    # -- 决策 --------------------------------------------------------------

    def route(self, task: Task) -> Decision:
        """只做路由决策，不发起调用。"""
        return route_task(task, self.registry)

    def policy_for(self, profile: GwProfile | None) -> RetryPolicy:
        """取本次请求生效的重试策略。

        需求："最大重试次数可以在 web 界面配置。默认为 3。"重试次数挂在 profile 上，
        因此按 profile 覆盖路由器的默认策略；没有 profile 时用默认。
        """
        if profile is None:
            return self.retry_policy
        return RetryPolicy(enabled=profile.retry_enabled, max_retries=profile.max_retries)

    def stream(
        self, task: Task, *, model: Model | None = None
    ) -> tuple[Decision, AssistantEventStream]:
        """按决策发起流式调用。

        ``model`` 省略时用主路由。**流式不在流中间换模型**：一旦开始吐字，换模型重来
        就会产出重复内容（需求：「已流式输出 → 不盲目重新生成」，处置为 ``FAIL``）。
        但需求 adapter 层第 8 条要求"降级时按路由中可用模型执行"，因此 Harness 在
        **首 delta 之前**发现可降级错误时，会用候选链上的下一个模型（``model=...``）
        再调一次本方法，重开一条干净的流——这一次调用复用同一份 ``Decision``，避免重新
        路由选出同一个已失败的模型。
        """
        if self._adapter_for is None:
            raise RuntimeError("Router 未配置 adapter_for，无法发起流式调用")

        decision = self.route(task)
        target = decision.primary if model is None else model
        if target is None:
            out = AssistantEventStream()
            out.push(ErrorEvent(reason="error", error=_error_message("ROUTE_NO_CANDIDATE: " + decision.reason)))
            return decision, out

        adapter = self._adapter_for(target)
        advanced = resolve_advanced(target, decision.profile)
        options = self._options_for(target, advanced)
        return decision, adapter.stream(target, task, options)

    # -- 执行 --------------------------------------------------------------

    async def execute(
        self,
        task: Task,
        *,
        trace: ExecutionTrace | None = None,
        on_attempt: Callable[[AttemptResult], Awaitable[None]] | None = None,
    ) -> AssistantMessage:
        """执行 task：按处置决策在重试 / 降级 / 报错中三选一。

        候选链是 :attr:`Decision.candidates` **整条**（需求第 78 行要求"只要有能力的时候
        都提供主备两个路由"，主备只是前两名，后面还有多少就还能降多少次）。逐个尝试，
        每个模型只尝试一次（同模型内的有限重试由 :meth:`_attempt` 负责），因此"每个模型
        最多尝试一次"对内容拒答天然成立。

        ``trace`` 是可选出参，用来回传"重试了几次、是否降级、最终处置、告警"；
        ``on_attempt`` 是可选回调，**每次尝试结束**（无论成败）都会被调用一次，供调用方
        逐条落库——只在最后落一条会让失败与重试在 Trace 里消失。
        """
        if self._adapter_for is None:
            raise RuntimeError("Router 未配置 adapter_for，无法执行调用")

        decision = self.route(task)
        if decision.primary is None:
            if trace is not None:
                trace.disposition = ErrorDisposition.FAIL.value
                trace.warnings.append("ROUTE_NO_CANDIDATE: " + decision.reason)
            return _error_message("ROUTE_NO_CANDIDATE: " + decision.reason)

        # 整条候选链；``candidates`` 理论上非空（primary 非 None 即已保证），兜底仍取主路由。
        models = list(decision.candidates) or [decision.primary]

        message = _error_message(f"{ErrorCode.UNKNOWN.value}: 未执行任何模型")
        previous = ""
        for index, model in enumerate(models, start=1):
            started = self.clock.now()
            message, calls, retries = await self._attempt(model, task, decision.profile, trace)
            ended = self.clock.now()

            code = _code_from(message)
            error_decision = decision_for(code) if message.stop_reason == "error" else None
            if trace is not None:
                trace.attempt_index = index
                if error_decision is not None:
                    trace.disposition = error_decision.disposition.value

            # FAIL：确定性失败（请求非法、已流式输出后中断等），换模型也不会变好。
            nxt = (
                models[index]
                if error_decision is not None
                and error_decision.disposition is not ErrorDisposition.FAIL
                and index < len(models)
                else None
            )

            if on_attempt is not None:
                await on_attempt(
                    AttemptResult(
                        index=index,
                        model=model,
                        message=message,
                        degraded_from=previous,
                        disposition=error_decision.disposition.value if error_decision else "",
                        final=nxt is None,
                        attempt=calls,
                        retry=retries,
                        started=started,
                        ended=ended,
                    )
                )

            if message.stop_reason != "error":
                if trace is not None:
                    trace.served_model = model
                    # ``previous`` 非空意味着前一跳失败过，本条是降级后的那条。
                    trace.fallback = bool(previous)
                return message

            if nxt is None:
                return message

            # RETRY 的同模型重试已在 _attempt 内耗尽；DEGRADE 则本就不重试当前模型。
            # 两种情况都按需求「处理策略」列走"按照路由更换模型再试"。
            if trace is not None:
                trace.fallback = True
                trace.degraded_from = model.label()
                trace.degraded_to = nxt.label()
                trace.warnings.append(
                    f"{code.value}: 已降级 {model.label()} → {nxt.label()}；"
                    f"{error_decision.strategy}"  # type: ignore[union-attr] - nxt 非空已保证非 FAIL
                )
            previous = model.label()

        return message

    async def _attempt(
        self,
        model: Model,
        task: Task,
        profile: GwProfile | None,
        trace: ExecutionTrace | None = None,
    ) -> tuple[AssistantMessage, int, int]:
        """对单个模型执行一次（含有限重试）。

        返回 ``(终态消息, 对上游的调用次数, 同模型重试次数)``——逐次尝试要各落一条记录，
        调用方得知道**这一跳**烧了几次配额，而不是整条请求的累计值。
        """
        advanced = resolve_advanced(model, profile)

        # 工具轮数护栏：网关不编排 agent 的工具循环，因此只能在请求进入上游之前
        # 用历史里已有的工具调用轮数做校验，避免明知超限还消耗一次配额。
        limit = advanced.max_tool_rounds
        if limit is not None and task.tool_rounds() > limit:
            return (
                _error_message(
                    f"{ErrorCode.TOOL_ROUNDS_EXCEEDED.value}: 工具调用轮数 "
                    f"{task.tool_rounds()} 超过上限 {limit}"
                ),
                0,
                0,
            )

        adapter = self._adapter_for(model)  # type: ignore[misc]
        options = self._options_for(model, advanced)
        policy = self.policy_for(profile)

        stats = {"retries": 0}

        async def produce() -> AssistantMessage:
            try:
                return await adapter.complete(model, task, options)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 归一为错误消息交由重试分类
                # 带上稳定错误码前缀：Harness 靠 "<CODE>: <detail>" 前缀还原
                # error_code 落库，缺了前缀决策表就查不到、trace 里只剩 UNKNOWN。
                return _error_message(f"{classify_error_code(exc).value}: {exc}")

        message = await retry_assistant_call(
            produce,
            policy,
            self.clock,
            callbacks=RetryCallbacks(on_retry_finished=_record_retries(stats)),
            # 决策表是"能不能重试"的唯一真源，不再走正则分类。
            is_retryable=lambda response: should_auto_retry(_code_from(response)),
        )
        retries = stats["retries"]
        calls = retries + 1
        if trace is not None:
            trace.attempts += calls
            trace.retries += retries
        if message.stop_reason != "error":
            self.registry.record_usage(model)
        return message, calls, retries


def _record_retries(stats: dict[str, int]):
    """把 ``on_retry_finished`` 的尝试次数记进可变字典，供调用方累加。"""

    def _callback(_success: bool, attempts: int, _error: str | None) -> None:
        stats["retries"] = attempts

    return _callback


def _code_from(message: AssistantMessage) -> ErrorCode:
    """从 ``"<CODE>: <detail>"`` 前缀还原稳定错误码；无前缀按 ``UNKNOWN``。"""
    if message.stop_reason != "error" or not message.error_message:
        return ErrorCode.UNKNOWN
    prefix = message.error_message.split(":", 1)[0].strip()
    try:
        return ErrorCode(prefix)
    except ValueError:
        return ErrorCode.UNKNOWN


def _error_message(error: str) -> AssistantMessage:
    return AssistantMessage(stop_reason="error", error_message=error)
