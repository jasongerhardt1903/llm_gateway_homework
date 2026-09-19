"""路由器：把 task 落到具体模型并执行，主路由失败降级备用。

路由决策（选谁）与执行（调用 + 重试 + 降级）放在一起，因为它们共享同一个
``Decision``：只有先知道主备是谁，才谈得上降级。

版本：0.2.0
"""

from __future__ import annotations

import asyncio
from typing import Callable

from ..adapter.base import Adapter, AdapterOptions
from ..core.advanced import AdvancedConfig
from ..core.errors import ErrorCode, is_retryable_assistant_error
from ..core.events import AssistantEventStream, ErrorEvent
from ..core.messages import AssistantMessage, Model
from ..core.schema import Task
from ..harness.retry import RetryPolicy, retry_assistant_call
from ..util.clock import Clock, RealClock
from .profile import GwProfile, resolve_advanced
from .registry import CapabilityRegistry
from .rules import Decision, route as route_task

__all__ = ["Router"]


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

        需求第 128 行："最大重试次数可以在 web 界面配置。默认为 3。"重试次数挂在
        profile 上，因此按 profile 覆盖路由器的默认策略；没有 profile 时用默认。
        """
        if profile is None:
            return self.retry_policy
        return RetryPolicy(enabled=profile.retry_enabled, max_retries=profile.max_retries)

    def stream(self, task: Task) -> tuple[Decision, AssistantEventStream]:
        """按决策发起流式调用。

        流式**不做跨模型降级**：一旦开始吐字，换模型重来就会产出重复内容
        （需求决策表："已流式输出 → 不盲目重新生成"）。因此这里只用主路由，
        降级决策留给调用方在首 delta 之前处理。
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
        on_fallback: Callable[[Model, Model, str], None] | None = None,
    ) -> AssistantMessage:
        """执行 task：主路由重试后仍失败则降级备用。

        ``on_fallback(from_model, to_model, error)`` 在发生降级时回调，供落库与
        可观测性记录"这次请求其实降级了"。
        """
        if self._adapter_for is None:
            raise RuntimeError("Router 未配置 adapter_for，无法执行调用")

        decision = self.route(task)
        if decision.primary is None:
            return _error_message("ROUTE_NO_CANDIDATE: " + decision.reason)

        message = await self._attempt(decision.primary, task, decision.profile)

        # 只有瞬时失败才降级：认证/配额/内容拒答换模型也不会变好，降级只是浪费。
        if message.stop_reason == "error" and is_retryable_assistant_error(message):
            if decision.backup is not None:
                if on_fallback is not None:
                    on_fallback(decision.primary, decision.backup, message.error_message or "")
                message = await self._attempt(decision.backup, task, decision.profile)

        return message

    async def _attempt(self, model: Model, task: Task, profile: GwProfile | None) -> AssistantMessage:
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

        async def produce() -> AssistantMessage:
            try:
                return await adapter.complete(model, task, options)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 归一为错误消息交由重试分类
                return _error_message(str(exc))

        message = await retry_assistant_call(produce, policy, self.clock)
        if message.stop_reason != "error":
            self.registry.record_usage(model)
        return message


def _error_message(error: str) -> AssistantMessage:
    return AssistantMessage(stop_reason="error", error_message=error)
