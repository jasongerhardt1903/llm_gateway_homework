"""Harness 测试的公共设施。

服务层需要"路由 + adapter + mock transport"三件套，且全部不联网、不真实等待。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.adapter.factory import create_adapter
from llm_gw.adapter.presets.registry import find_model
from llm_gw.harness.retry import RetryPolicy
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock

from tests.support.mock_transport import client_for


@pytest.fixture
def openai_model():
    model = find_model("openai", "gpt-4o-mini")
    assert model is not None
    return model


def build_router(model, transport: httpx.MockTransport, *, clock=None) -> Router:
    """把 mock transport 包成 Router（单模型，重试关闭以便断言调用次数）。"""
    client = client_for(transport, base_url=model.base_url)
    adapter = create_adapter(model.api, client)
    registry = CapabilityRegistry()
    registry.register(model)
    return Router(
        registry,
        adapter_for=lambda _model: adapter,
        retry_policy=RetryPolicy(enabled=False),
        clock=clock or FakeClock(),
    )
