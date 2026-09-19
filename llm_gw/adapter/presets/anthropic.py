"""Anthropic preset（Messages 协议）。"""

from __future__ import annotations

from ...core.messages import Capabilities, CostRates, Model
from .registry import Preset

__all__ = ["PRESET", "MODELS"]

_API = "anthropic-messages"
_BASE_URL = "https://api.anthropic.com/v1"

MODELS: tuple[Model, ...] = (
    Model(
        id="claude-3-5-haiku-20241022",
        name="Claude 3.5 Haiku",
        api=_API,
        provider="anthropic",
        base_url=_BASE_URL,
        context_window=200_000,
        max_tokens=8_192,
        cost=CostRates(input=0.80, output=4.00, cache_read=0.08, cache_write=1.00),
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=False, vision=True, reasoning=False
        ),
        display_provider="Anthropic",
    ),
    Model(
        id="claude-3-5-sonnet-20241022",
        name="Claude 3.5 Sonnet",
        api=_API,
        provider="anthropic",
        base_url=_BASE_URL,
        context_window=200_000,
        max_tokens=8_192,
        cost=CostRates(input=3.00, output=15.00, cache_read=0.30, cache_write=3.75),
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=False, vision=True, reasoning=False
        ),
        display_provider="Anthropic",
    ),
)

PRESET = Preset(
    provider="anthropic",
    display_name="Anthropic",
    api=_API,
    base_url=_BASE_URL,
    env_key="ANTHROPIC_API_KEY",
    models=MODELS,
    notes="Messages 协议；无原生 response_format，结构化输出走 prompt 约束 + 本地校验。",
)
