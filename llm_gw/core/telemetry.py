"""每次 LLM 调用的遥测记录。

需求要求"每次 LLM 调用都记录好以下信息"的 8 个维度：

| 维度   | 关键字段                                        |
| 关联   | trace_id, run_id, step_id, call_id              |
| Prompt | 名称，版本，hash，Schema 版本                   |
| 用量   | input，output，cached，reasoning tokens         |
| 延迟   | queue，route，TTFT，generation，total latency   |
| 弹性   | attempt，retry，fallback，disposition，timeout budget |
| 结果   | finish reason，终态，输出校验结果               |
| 错误   | 稳定错误码，HTTP状态，供应商请求ID              |
| 成本   | 输入，输出，缓存及总估算成本                    |

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
    """弹性维度：第几次尝试、是否重试、是否降级、最终处置、超时预算。"""

    attempt: int = 1
    retry: int = 0
    fallback: bool = False
    timeout_budget_ms: int = 0
    #: 最终处置：``retry`` / ``degrade`` / ``fail``；成功完成时为空串。
    disposition: str = ""


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
