"""每次 LLM 调用的遥测记录。

需求要求"每次 LLM 调用都记录好以下信息"的 8 个维度：

| 维度   | 关键字段                                        |
| 关联   | trace_id, run_id, step_id, call_id              |
| 路由   | profile, provider, model, api, route（决策快照） |
| Prompt | 名称，版本，hash，Schema 版本                   |
| 用量   | input，output，cached，reasoning tokens         |
| 延迟   | queue，route，TTFT，generation，total latency   |
| 弹性   | attempt, retry, fallback, disposition, attempt_index, degraded_from, timeout budget |
| 结果   | finish reason，终态，输出校验结果               |
| 错误   | 稳定错误码，HTTP状态，供应商请求ID              |
| 成本   | 输入，输出，缓存及总估算成本                    |

**一次尝试一条记录**：候选链上的每次尝试（主路由、以及失败后降级到的下一个模型）
各落一条 ``CallRecord``，共享同一个 ``trace_id``，用 ``resilience.attempt_index``
标出位次。这样"哪个模型失败了、为什么、降到了谁"在 Trace 里是逐条可见的，而不是
只剩一行最终结果。因此上面的"弹性"字段描述的是本条记录那一次尝试，不是请求汇总。

记录同时服务三种消费方：**Metrics**（聚合趋势）、**Logs**（单次明细）、
**Trace**（用 trace/run/step/call 串联链路）。因此 ``to_dict`` 输出既要有
顶层可索引的标量列，也要有可 JSON 序列化的嵌套结构。

版本：0.3.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ErrorCode, redact_mapping, redact_text
from .messages import Cost, TerminalState, Usage

__all__ = [
    "PromptInfo",
    "LatencyBreakdown",
    "ResilienceInfo",
    "RouteInfo",
    "CallRecord",
    "TERMINAL_VALUES",
]

TERMINAL_VALUES: frozenset[str] = frozenset({"done", "error", "cancelled"})


@dataclass
class PromptInfo:
    """Prompt 维度：名称、版本、内容 hash、schema 版本。"""

    name: str = ""
    version: str = ""
    sha256: str = ""
    schema_version: str = ""


@dataclass
class LatencyBreakdown:
    """延迟维度（毫秒）。

    ``ttft_ms`` 的语义很关键：需求要求 TTFT **必须以第一个有业务意义的 detail
    为准**，不能把连接建立事件当作首 token。
    """

    queue_ms: float = 0.0
    route_ms: float = 0.0
    ttft_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class ResilienceInfo:
    """弹性维度：第几次尝试、是否重试、是否降级、最终处置、超时预算。

    候选链上的**每一次尝试各落一条记录**，因此这里的字段都描述**本条记录**这一次
    尝试，而不是整个请求的汇总：

    - ``attempt`` / ``retry``：本次尝试对上游调了几次（含同模型重试）；
    - ``attempt_index``：在候选链里排第几（1 起）——前端按它把同 ``trace_id`` 的
      若干条记录串成"主 → 备 → ..."的顺序；
    - ``degraded_from``：本次是从哪个模型降下来的（空 = 首跳，即主路由）。
    """

    attempt: int = 1
    retry: int = 0
    fallback: bool = False
    timeout_budget_ms: int = 0
    #: 最终处置：``retry`` / ``degrade`` / ``fail``；成功完成时为空串。
    disposition: str = ""
    #: 本条记录是候选链里的第几次尝试（1 起）。
    attempt_index: int = 1
    #: 本次尝试由哪个模型降级而来（``provider/id``）；首跳为空串。
    degraded_from: str = ""


@dataclass
class RouteInfo:
    """本条记录所属请求的**路由决策快照**。

    需求第 31 行把 profile 定为路由的作用域，但此前"为什么选它 / 为什么不选它"只活在
    一次性的内存对象里，落库后只剩一个模型名——线上问"为什么这次没走到某个模型"时
    无从回答。决策记录（``Decision.reason`` / ``candidates`` / ``rejected``）因此
    随每次尝试落库，Trace 页直接展示。
    """

    #: 人类可读的决策说明（命中静态顺序 / 动态打分 / 被谁点名 等）。
    reason: str = ""
    #: 参与本次决策、且通过能力与可用性过滤的候选（按最终顺序，``provider/id``）。
    candidates: list[str] = field(default_factory=list)
    #: 被拒的模型与原因（能力不匹配 / 不可用），``[{"model": ..., "reason": ...}]``。
    rejected: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CallRecord:
    """一次 LLM 调用的完整记录。"""

    # -- 关联 --------------------------------------------------------------
    trace_id: str = ""
    run_id: str = ""
    step_id: str = ""
    call_id: str = ""

    # -- Prompt ------------------------------------------------------------
    prompt: PromptInfo = field(default_factory=PromptInfo)

    # -- 路由/供应商 --------------------------------------------------------
    #: 生效的 gwprofile 名（需求第 31 行）。为空表示走全局模型池。
    profile: str = ""
    provider: str = ""
    model: str = ""
    api: str = ""
    #: 本次请求的路由决策快照（选谁 / 拒谁 / 为什么）。同 ``trace_id`` 的各次尝试共用。
    route: RouteInfo = field(default_factory=RouteInfo)

    # -- 用量 / 成本 --------------------------------------------------------
    usage: Usage = field(default_factory=Usage)

    # -- 延迟 --------------------------------------------------------------
    latency: LatencyBreakdown = field(default_factory=LatencyBreakdown)

    # -- 弹性 --------------------------------------------------------------
    resilience: ResilienceInfo = field(default_factory=ResilienceInfo)

    # -- 结果 --------------------------------------------------------------
    finish_reason: str = ""
    terminal: TerminalState = "done"
    output_valid: bool | None = None

    # -- 错误 --------------------------------------------------------------
    error_code: ErrorCode | None = None
    error_message: str | None = None
    http_status: int | None = None
    provider_request_id: str | None = None

    # -- 其他 --------------------------------------------------------------
    stream_chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cost(self) -> Cost:
        """成本与用量同源，避免两处各写一份导致对不上账。"""
        return self.usage.cost

    def to_dict(self) -> dict[str, Any]:
        """输出可直接落库 / 返回给 Web 的结构。

        终态必须是 done / error / cancelled 三者之一；非法值抛 ``ValueError``
        而不是静默写坏数据——错误码与终态是下游告警的依据。
        """
        if self.terminal not in TERMINAL_VALUES:
            raise ValueError(f"invalid terminal state: {self.terminal!r}; expected one of {sorted(TERMINAL_VALUES)}")

        return {
            # 关联
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "call_id": self.call_id,
            # Prompt
            "prompt_name": self.prompt.name,
            "prompt_version": self.prompt.version,
            "prompt_sha256": self.prompt.sha256,
            "prompt_schema_version": self.prompt.schema_version,
            # 路由
            "profile": self.profile,
            "provider": self.provider,
            "model": self.model,
            "api": self.api,
            # 路由决策快照（"为什么选它 / 为什么不选它"）
            "route": {
                "reason": self.route.reason,
                "candidates": list(self.route.candidates),
                "rejected": [dict(item) for item in self.route.rejected],
            },
            # 用量
            "usage": {
                "input": self.usage.input,
                "output": self.usage.output,
                "cache_read": self.usage.cache_read,
                "cache_write": self.usage.cache_write,
                "reasoning": self.usage.reasoning,
                "total_tokens": self.usage.effective_total_tokens(),
            },
            # 延迟（顶层冗余一份，便于 SQL 聚合与建索引）
            "queue_ms": self.latency.queue_ms,
            "route_ms": self.latency.route_ms,
            "ttft_ms": self.latency.ttft_ms,
            "generation_ms": self.latency.generation_ms,
            "total_ms": self.latency.total_ms,
            "latency": {
                "queue_ms": self.latency.queue_ms,
                "route_ms": self.latency.route_ms,
                "ttft_ms": self.latency.ttft_ms,
                "generation_ms": self.latency.generation_ms,
                "total_ms": self.latency.total_ms,
            },
            # 弹性
            "attempt": self.resilience.attempt,
            "retry": self.resilience.retry,
            "fallback": self.resilience.fallback,
            "disposition": self.resilience.disposition,
            "timeout_budget_ms": self.resilience.timeout_budget_ms,
            # 候选链上的位次与降级来源：前端据此把同 trace_id 的几条记录串成主 → 备 → ...
            "attempt_index": self.resilience.attempt_index,
            "degraded_from": self.resilience.degraded_from,
            # 结果
            "finish_reason": self.finish_reason,
            "terminal": self.terminal,
            "output_valid": self.output_valid,
            # 错误
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": redact_text(self.error_message) if self.error_message else None,
            "http_status": self.http_status,
            "provider_request_id": self.provider_request_id,
            # 成本
            "cost": {
                "input": self.cost.input,
                "output": self.cost.output,
                "cache_read": self.cost.cache_read,
                "cache_write": self.cost.cache_write,
                "total": self.cost.total,
            },
            # 其他
            "stream_chunk_count": self.stream_chunk_count,
            "metadata": redact_mapping(self.metadata),
        }
