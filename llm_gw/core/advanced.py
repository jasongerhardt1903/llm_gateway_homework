"""模型高级配置项。

需求第 30 行："高级配置项目有模型的 URL，输入和输出的上下文窗口，工具调用轮数，
思考模式（跟随模型默认，开启，关闭），temperature，top_p，top_k 等。"

其中 URL 与上下文窗口是模型身份的一部分，落在 :class:`~llm_gw.core.messages.Model`
的 ``base_url`` / ``context_window`` / ``max_tokens`` 上；本模块承载的是可被
profile 模版整体替换的**采样与行为参数**。

放在 core 而不是 router：``Model`` 需要持有它，而 core 不得反向依赖 router。

版本：0.2.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["THINKING_MODES", "AdvancedConfig"]

#: 思考模式三态。需求第 30 行："思考模式（跟随模型默认，开启，关闭）"。
#: ``default`` 表示不发送该参数、交给供应商默认；``on``/``off`` 才显式表达意图。
THINKING_MODES: tuple[str, ...] = ("default", "on", "off")


@dataclass
class AdvancedConfig:
    """一份高级配置。

    ``None`` 语义是"不发送该字段"，而不是"发送 0"——``temperature=0`` 是确定性
    采样，与"不传、用供应商默认"是两回事，混同会静默改变模型行为。
    """

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    thinking_mode: str = "default"
    max_tool_rounds: int | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        _check_range("temperature", self.temperature, 0.0, 2.0)
        _check_range("top_p", self.top_p, 0.0, 1.0)
        _check_range("top_k", self.top_k, 0, None)
        _check_range("max_tool_rounds", self.max_tool_rounds, 0, None)
        _check_range("max_tokens", self.max_tokens, 1, None)
        if self.thinking_mode not in THINKING_MODES:
            raise ValueError(
                f"thinking_mode must be one of {', '.join(THINKING_MODES)}, got {self.thinking_mode!r}"
            )

    def is_empty(self) -> bool:
        """是否没有任何要发送的参数。"""
        return (
            self.temperature is None
            and self.top_p is None
            and self.top_k is None
            and self.max_tokens is None
            and self.max_tool_rounds is None
            and self.thinking_mode == "default"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "thinking_mode": self.thinking_mode,
            "max_tool_rounds": self.max_tool_rounds,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AdvancedConfig:
        """从字典构造，忽略未知键以便配置表向前兼容。"""
        known = cls.__dataclass_fields__
        return cls(**{key: value for key, value in (data or {}).items() if key in known})


def _check_range(name: str, value: float | int | None, low: float, high: float | None) -> None:
    """越界参数会被供应商拒绝，本地先拦住可以省掉一次无效上游调用。"""
    if value is None:
        return
    if value < low or (high is not None and value > high):
        bound = f"{low}" if high is None else f"{low} and {high}"
        raise ValueError(f"{name} must be between {bound}, got {value}")
