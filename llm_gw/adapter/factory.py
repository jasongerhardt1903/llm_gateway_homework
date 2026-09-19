"""Adapter 工厂：按 ``Model.api`` 选协议实现。"""

from __future__ import annotations

import httpx

from .base import Adapter
from .protocols.anthropic_messages import AnthropicMessagesAdapter
from .protocols.openai_compat import OpenAICompatAdapter

__all__ = ["create_adapter"]


def create_adapter(api: str, client: httpx.AsyncClient | None = None) -> Adapter:
    """按协议标识构造 adapter。协议适配逻辑只有这里知道映射关系。"""
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    if api == OpenAICompatAdapter.api:
        return OpenAICompatAdapter(client)
    if api == AnthropicMessagesAdapter.api:
        return AnthropicMessagesAdapter(client)
    raise ValueError(f"未知的 API 协议: {api!r}")