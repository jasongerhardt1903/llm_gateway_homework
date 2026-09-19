"""错误处置决策表的行为封装。

需求 Harness 层第 91-104 行给出一张「阶段 / 典型错误 / 是否适合重试 / 处理策略」
的表，并要求网关在**重试、降级、报错**三者中选一个。表的**数据**放在
``core/errors.py``（错误码与处置分类同源，避免两处漂移），这里提供面向调用方的
判定函数：给定错误码，回答"该重试 / 该换模型 / 该直接报错"。

这样路由与 Harness 层不必自己写 ``if code in {...}``，判定集中在一处。

版本：0.3.0
"""

from __future__ import annotations

from ..core.errors import ERROR_DECISIONS, ErrorCode, ErrorDecision, ErrorDisposition

__all__ = [
    "ErrorDecision",
    "ErrorDisposition",
    "decision_for",
    "disposition_for",
    "should_auto_retry",
    "should_degrade",
    "describe_decision",
]

#: 决策表未覆盖的错误码按"直接报错"处理——不做任何动作比盲目重试安全。
_DEFAULT = ErrorDecision(ErrorDisposition.FAIL, "未知错误码，直接返回。")


def decision_for(code: ErrorCode) -> ErrorDecision:
    """查询错误码的处置决策；未知码按"直接报错"处理。"""
    return ERROR_DECISIONS.get(code, _DEFAULT)


def disposition_for(code: ErrorCode) -> ErrorDisposition:
    """该错误码应走三者中的哪一个：重试 / 降级 / 报错。"""
    return decision_for(code).disposition


def should_auto_retry(code: ErrorCode) -> bool:
    """是否允许网关自行有限重试**同一**模型（不改变请求）。"""
    return decision_for(code).retryable


def should_degrade(code: ErrorCode) -> bool:
    """是否应**直接**切换路由表中下一个模型（不重试当前模型）。"""
    return disposition_for(code) is ErrorDisposition.DEGRADE


def describe_decision(code: ErrorCode) -> str:
    """给日志/响应用的中文说明。"""
    return decision_for(code).strategy
