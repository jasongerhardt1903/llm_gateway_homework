"""统一消息与模型类型。

这些类型是 LLM GW 与后端 agent 之间的契约：**供应商差异到此为止**。
adapter 负责把供应商的请求/响应翻译成这里定义的结构，路由与 Harness
只看到统一结构，因此不感知任何供应商细节。

成本计算移植自 pi 的 ``calculateCost``：``单价 / 1e6 × token 数``，
并支持按总输入量命中的阶梯价。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .advanced import AdvancedConfig

__all__ = [
    "Capabilities",
    "CostRates",
    "CostTier",
    "Cost",
    "Usage",
    "Model",
    "StopReason",
    "TerminalState",
    "TextContent",
    "ThinkingContent",
    "ImageContent",
    "ToolCall",
    "ContentBlock",
    "AssistantMessage",
    "calculate_cost",
]

StopReason = Literal["pending", "stop", "length", "tool_use", "error", "aborted"]

#: 流式响应允许的唯一三种终态（需求："流式结束只有一个原因"）。
TerminalState = Literal["done", "error", "cancelled"]

TERMINAL_STATES: frozenset[str] = frozenset({"done", "error", "cancelled"})


@dataclass(frozen=True)
class Capabilities:
    """模型能力，供路由层做能力匹配。"""

    sse: bool = True
    streaming: bool = True
    tools: bool = False
    json_schema: bool = False
    vision: bool = False
    reasoning: bool = False


@dataclass(frozen=True)
class CostRates:
    """每百万 token 的单价（美元）。"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class CostTier(CostRates):
    """阶梯价：当总输入量超过 ``input_tokens_above`` 时整单改用本档费率。"""

    input_tokens_above: int = 0


@dataclass
class Cost:
    """一次调用的估算成本（美元）。"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    """token 用量。``reasoning`` 是 ``output`` 的子集，不重复计费。"""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int | None = None
    total_tokens: int = 0
    cost: Cost = field(default_factory=Cost)

    def effective_total_tokens(self) -> int:
        """供应商未给出 total 时按 input + output 推导。"""
        if self.total_tokens:
            return self.total_tokens
        return self.input + self.output


@dataclass
class Model:
    """统一模型描述。``api`` 决定用哪个协议 adapter，``provider`` 是供应商。"""

    id: str
    name: str
    api: str
    provider: str
    base_url: str
    context_window: int = 0
    max_tokens: int = 0
    cost: CostRates = field(default_factory=CostRates)
    tiers: list[CostTier] = field(default_factory=list)
    capabilities: Capabilities = field(default_factory=Capabilities)
    headers: dict[str, str] = field(default_factory=dict)
    display_provider: str = ""
    #: 需求第 30 行的可选配置项"模型 tag"，用于控制台分组与筛选。
    tag: str = ""
    #: 本模型的高级配置项（采样与行为参数）。可被 profile 模版整体替换，
    #: 替换规则见 :func:`llm_gw.router.profile.resolve_advanced`。
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    #: 密钥。可由 Web 界面录入（存 SQLite），为空时回退环境变量。
    #: 这是敏感字段：任何序列化边界都必须显式排除它，见 ``web/api_models.py``。
    api_key: str = ""

    def label(self) -> str:
        return f"{self.provider}/{self.id}"


# --------------------------------------------------------------------------
# 内容块
# --------------------------------------------------------------------------


@dataclass
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None


@dataclass
class ImageContent:
    type: Literal["image"] = "image"
    data: str = ""
    mime_type: str = "image/png"


@dataclass
class ToolCall:
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)


ContentBlock = TextContent | ThinkingContent | ImageContent | ToolCall


@dataclass
class AssistantMessage:
    """统一助手消息。终态与错误信息都挂在这里。"""

    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = "pending"
    error_message: str | None = None
    response_model: str | None = None
    response_id: str | None = None
    raw_stop_reason: str | None = None
    timestamp: float = 0.0

    def text(self) -> str:
        """拼接所有文本块，便于测试与简单消费方使用。"""
        return "".join(block.text for block in self.content if isinstance(block, TextContent))

    def tool_calls(self) -> list[ToolCall]:
        return [block for block in self.content if isinstance(block, ToolCall)]

    def terminal_state(self) -> TerminalState:
        """把 stop_reason 归一到三种终态之一。"""
        if self.stop_reason == "aborted":
            return "cancelled"
        if self.stop_reason == "error":
            return "error"
        return "done"


# --------------------------------------------------------------------------
# 成本
# --------------------------------------------------------------------------


def _select_rates(model: Model, usage: Usage) -> CostRates:
    """按总输入量选择费率档位；命中最高阈值者生效。"""
    total_input = usage.input + usage.cache_read + usage.cache_write
    rates: CostRates = model.cost
    matched_threshold = -1
    for tier in model.tiers:
        if total_input > tier.input_tokens_above and tier.input_tokens_above > matched_threshold:
            rates = tier
            matched_threshold = tier.input_tokens_above
    return rates


def calculate_cost(model: Model, usage: Usage) -> Cost:
    """计算并写回 ``usage.cost``，返回同一个对象。

    单价单位为「每百万 token」，因此除以 1e6。
    """
    rates = _select_rates(model, usage)
    cost = usage.cost
    cost.input = (rates.input / 1_000_000) * usage.input
    cost.output = (rates.output / 1_000_000) * usage.output
    cost.cache_read = (rates.cache_read / 1_000_000) * usage.cache_read
    cost.cache_write = (rates.cache_write / 1_000_000) * usage.cache_write
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost
