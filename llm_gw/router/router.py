"""路由器：把 task 落到具体模型并执行，按处置决策在重试/降级/报错中三选一。

路由决策（选谁）与执行（调用 + 重试 + 降级）放在一起，因为它们共享同一个
``Decision``：只有先知道主备是谁，才谈得上降级。

需求 Harness 层第 91 行要求网关在**重试、降级、报错**三者中选一个。判定数据在
``core/errors.py`` 的 ``ERROR_DECISIONS``，执行落在这里：

- ``RETRY``：先由 :func:`retry_assistant_call` 对**同一**模型有限重试；
- ``DEGRADE``：不重试当前模型，直接换 ``Decision.backup``；
- ``FAIL``：立即返回错误，不做任何后续动作。

两者的差别只在"要不要先同模型重试"；重试耗尽后同样按路由换模型，与需求
「处理策略」列一致。

版本：0.3.0
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

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

__all__ = ["Router", "ExecutionTrace"]


@dataclass
class ExecutionTrace:
    """本次执行的弹性事实，供 Harness 落库与对外告警。

    需求要求"妥善记录"处置结果，而 :class:`AssistantMessage` 是 adapter 共享的传输
    模型、没有承载元数据的位置，因此执行层把事实写进这个可选的出参，由调用方决定
    怎么落库（Harness 写进 ``CallRecord.resilience``）与怎么对外告警。
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
    #: 不阻塞但需知会的告警（如"认证失败已换模型"）。
    warnings: list[str] = field(default_factory=list)


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

    def stream(self, task: Task) -> tuple[Decision, AssistantEventStream]:
        """按决策发起流式调用。

        流式**不做跨模型降级**：一旦开始吐字，换模型重来就会产出重复内容
        （需求：「已流式输出 → 不盲目重新生成」，处置为 ``FAIL``）。因此这里只用主
        路由，降级决策留给调用方在首 delta 之前处理。
        """
        if self._adapter_for is None:
            raise RuntimeError("Router 未配置 adapter_for，无法发起流式调用")

        decision = self.route(task)
        if decision.primary is None:
            out = AssistantEventStream()
            out.push(ErrorEvent(reason="error", error=_error_message("ROUTE_NO_CANDIDATE: " + decision.reason)))
            return decision, out

        adapter = self._adapter_for(decision.primary)
        advanced = resolve_advanced(decision.primary, decision.profile)
        options = self._options_for(decision.primary, advanced)
        return decision, adapter.stream(decision.primary, task, options)

    # -- 执行 --------------------------------------------------------------

    async def execute(
        self,
        task: Task,
        *,
        trace: ExecutionTrace | None = None,
    ) -> AssistantMessage:
        """执行 task：按处置决策在重试 / 降级 / 报错中三选一。

        候选链就是 ``[主路由] + [备用路由]``（需求第 78 行："只要有能力的时候，
        都提供主备两个路由"）。逐个尝试，每个模型只尝试一次（同模型内的有限重试
        由 :meth:`_attempt` 负责），因此"每个模型最多尝试一次"对内容拒答天然成立。

        ``trace`` 是可选出参，用来回传"重试了几次、是否降级、最终处置、告警"。
        """
        if self._adapter_for is None:
            raise RuntimeError("Router 未配置 adapter_for，无法执行调用")

        decision = self.route(task)
        if decision.primary is None:
            if trace is not None:
                trace.disposition = ErrorDisposition.FAIL.value
                trace.warnings.append("ROUTE_NO_CANDIDATE: " + decision.reason)
            return _error_message("ROUTE_NO_CANDIDATE: " + decision.reason)

        models = [decision.primary]
        if decision.backup is not None:
            models.append(decision.backup)

        message = _error_message(f"{ErrorCode.UNKNOWN.value}: 未执行任何模型")
        for index, model in enumerate(models):
            message = await self._attempt(model, task, decision.profile, trace)
            if message.stop_reason != "error":
                return message

            code = _code_from(message)
            error_decision = decision_for(code)
            if trace is not None:
                trace.disposition = error_decision.disposition.value

            # FAIL：确定性失败（请求非法、已流式输出后中断等），换模型也不会变好。
            if error_decision.disposition is ErrorDisposition.FAIL:
                return message

            nxt = models[index + 1] if index + 1 < len(models) else None
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
                    f"{error_decision.strategy}"
                )

        return message

    async def _attempt(
        self,
        model: Model,
        task: Task,
        profile: GwProfile | None,
        trace: ExecutionTrace | None = None,
    ) -> AssistantMessage:
        """对单个模型执行一次（含有限重试）。"""
        advanced = resolve_advanced(model, profile)

        # 工具轮数护栏：网关不编排 agent 的工具循环，因此只能在请求进入上游之前
        # 用历史里已有的工具调用轮数做校验，避免明知超限还消耗一次配额。
        limit = advanced.max_tool_rounds
        if limit is not None and task.tool_rounds() > limit:
            return _error_message(
                f"{ErrorCode.TOOL_ROUNDS_EXCEEDED.value}: 工具调用轮数 "
                f"{task.tool_rounds()} 超过上限 {limit}"
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
        if trace is not None:
            trace.attempts += stats["retries"] + 1
            trace.retries += stats["retries"]
        if message.stop_reason != "error":
            self.registry.record_usage(model)
        return message


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
