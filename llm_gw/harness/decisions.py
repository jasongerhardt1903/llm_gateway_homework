"""重试决策表的行为封装。

需求第 88 行给出一张"阶段 / 典型错误 / 是否适合重试"的表。表的**数据**放在
``core/errors.py``（错误码与重试分类同源，避免两处漂移），这里提供面向调用方的
判定函数：给定错误码，回答"该不该自动重试 / 该做什么"。

这样路由与 Harness 层不必自己写 ``if code in {...}``，判定集中在一处。
"""

from __future__ import annotations

from ..core.errors import RETRY_DECISION, ErrorCode, RetryAction

__all__ = [
    "RetryAction",
    "decision_for",
    "should_auto_retry",
    "should_fallback",
    "describe_decision",
]

#: 允许网关自动重试的动作集合。
_AUTO_RETRY_ACTIONS: frozenset[RetryAction] = frozenset(
    {RetryAction.LIMITED_RETRY, RetryAction.LIMITED_RETRY_OR_FALLBACK}
)

#: 允许降级到备用路由的动作集合。
_FALLBACK_ACTIONS: frozenset[RetryAction] = frozenset(
    {RetryAction.LIMITED_RETRY_OR_FALLBACK, RetryAction.REJECT_OR_SWITCH_POOL}
)


def decision_for(code: ErrorCode) -> RetryAction:
    """查询错误码对应的决策动作；未知码按"永不重试"处理。"""
    return RETRY_DECISION.get(code, RetryAction.NEVER)


def should_auto_retry(code: ErrorCode) -> bool:
    """该错误码是否允许网关自动重试（不改变请求）。"""
    return decision_for(code) in _AUTO_RETRY_ACTIONS


def should_fallback(code: ErrorCode) -> bool:
    """该错误码是否允许切换到备用路由。"""
    return decision_for(code) in _FALLBACK_ACTIONS


def describe_decision(code: ErrorCode) -> str:
    """给日志/响应用的中文说明。"""
    action = decision_for(code)
    return _DESCRIPTIONS[action]


_DESCRIPTIONS: dict[RetryAction, str] = {
    RetryAction.NEVER: "不可重试：确定性失败，重试只会浪费配额。",
    RetryAction.RETRY_AFTER_FIX: "修正请求后重试：Prompt 缺变量或超预算。",
    RetryAction.RETRY_AFTER_CONFIG: "配置变更后重试：没有兼容模型。",
    RetryAction.REJECT_OR_SWITCH_POOL: "拒绝或换池：并发已满或截止时间不足。",
    RetryAction.LIMITED_RETRY: "有限重试：连接抖动或短暂 5xx。",
    RetryAction.LIMITED_RETRY_OR_FALLBACK: "有限重试或降级备用：限流 / 上游过载。",
    RetryAction.NO_BLIND_REGENERATE: "不盲目重新生成：已流式输出后中断。",
    RetryAction.LIMITED_REPAIR: "有限修复：输出未通过 Schema 校验。",
    RetryAction.NO_BYPASS: "不得绕过：模型内容拒答。",
}
