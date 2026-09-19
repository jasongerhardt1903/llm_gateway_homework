"""adapter 测试的公共设施。

所有 adapter 测试都由 ``httpx.MockTransport`` 驱动（需求明确要求），
因此这里只负责把 mock transport 包成 adapter 可直接使用的形态。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.adapter.base import Adapter
from llm_gw.adapter.factory import create_adapter
from llm_gw.adapter.presets.registry import find_model

from tests.support.mock_transport import client_for


def _model(provider: str, model_id: str):
    model = find_model(provider, model_id)
    assert model is not None, f"preset 中缺少 {provider}/{model_id}"
    return model


@pytest.fixture
def openai_model():
    return _model("openai", "gpt-4o-mini")


@pytest.fixture
def deepseek_model():
    return _model("deepseek", "deepseek-chat")


@pytest.fixture
def anthropic_model():
    return _model("anthropic", "claude-3-5-haiku-20241022")


@pytest.fixture
def make_adapter():
    """按模型协议构造 adapter，并注入 mock transport。"""

    def _make(model, transport: httpx.MockTransport) -> Adapter:
        client = client_for(transport, base_url=model.base_url)
        return create_adapter(model.api, client)

    return _make
