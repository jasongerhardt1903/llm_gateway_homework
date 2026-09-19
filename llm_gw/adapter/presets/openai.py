"""OpenAI 官方 preset。"""

from __future__ import annotations

from ...core.messages import Capabilities, CostRates, Model
from .registry import Preset

__all__ = ["PRESET", "MODELS"]

_API = "openai-completions"
_BASE_URL = "https://api.openai.com/v1"

MODELS: tuple[Model, ...] = (
    Model(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        api=_API,
        provider="openai",
        base_url=_BASE_URL,
        context_window=128_000,
        max_tokens=16_384,
        cost=CostRates(input=0.15, output=0.60, cache_read=0.075),
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=True, vision=True, reasoning=False
        ),
        display_provider="OpenAI",
    ),
    Model(
        id="gpt-4o",
        name="GPT-4o",
        api=_API,
        provider="openai",
        base_url=_BASE_URL,
        context_window=128_000,
        max_tokens=16_384,
        cost=CostRates(input=2.50, output=10.00, cache_read=1.25),
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=True, vision=True, reasoning=False
        ),
        display_provider="OpenAI",
    ),
)

PRESET = Preset(
    provider="openai",
    display_name="OpenAI",
    api=_API,
    base_url=_BASE_URL,
    env_key="OPENAI_API_KEY",
    models=MODELS,
    notes="官方 chat.completions，支持严格 JSON Schema 结构化输出。",
)
