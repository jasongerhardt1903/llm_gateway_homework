"""模型发现与连接测试的测试（需求"模型管理层"第 1、2 条，0.8.0）。

覆盖三件事：

1. 供应商模型清单：上游 ``GET /models`` 可达时以它为准，不可达时回退 preset；
2. 能力与高级配置项：已知型号按 preset 带出，未知型号给协议默认并标记未知；
3. 连接测试：真实最小对话（``ping`` + ``max_tokens=1``）的成功与各类失败。

上游全部由 ``httpx.MockTransport`` 模拟，**不联网**。
"""

from __future__ import annotations

import json

import httpx
import pytest

from llm_gw import __version__
from llm_gw.adapter import discovery
from llm_gw.adapter.factory import create_adapter
from llm_gw.adapter.presets.registry import all_models, find_model
from llm_gw.core.messages import Model
from llm_gw.harness.retry import RetryPolicy
from llm_gw.harness.service import GatewayService
from llm_gw.harness.storage import Storage
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock
from llm_gw.web.app import create_web_app

from tests.support.mock_transport import client_for, sse_transport


@pytest.fixture
def model():
    found = find_model("openai", "gpt-4o-mini")
    assert found is not None
    return found


@pytest.fixture
async def storage():
    store = await Storage(":memory:").init()
    yield store
    await store.close()


def _completion_body(text: str = "ok") -> dict:
    return {
        "id": "chatcmpl-probe",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


def _transport(payload: dict | None = None, *, status: int = 200):
    """返回 JSON 的 mock transport；``payload`` 为 None 时回一段最小对话。"""
    body = json.dumps(_completion_body() if payload is None else payload)
    return sse_transport([body], status=status, content_type="application/json")


def _build(model, transport, storage, **kwargs):
    adapter = create_adapter(model.api, client_for(transport, base_url=model.base_url))
    registry = CapabilityRegistry()
    registry.register_all(all_models())
    router = Router(
        registry,
        adapter_for=lambda _m: adapter,
        retry_policy=RetryPolicy(enabled=False),
        clock=FakeClock(),
    )
    service = GatewayService(router, storage, clock=FakeClock())
    return create_web_app(service, registry=registry, storage=storage, **kwargs), registry


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _probe_payload(**overrides) -> dict:
    payload = {
        "id": "gpt-4o-mini",
        "name": "GPT-4o mini",
        "provider": "openai",
        "api": "openai-completions",
        "base_url": "https://api.openai.com/v1",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# 高级配置项与鉴权头（纯函数）
# --------------------------------------------------------------------------


def test_advanced_items_follow_protocol_capability():
    """只展示协议真的认得的参数：OpenAI 不接受 top_k，Anthropic 接受。"""
    openai_keys = [item["key"] for item in discovery.advanced_items("openai-completions")]
    anthropic_keys = [item["key"] for item in discovery.advanced_items("anthropic-messages")]
    assert "top_k" not in openai_keys
    assert "top_k" in anthropic_keys
    # 未知协议不擅自省略可选项，交给用户判断。
    assert "top_k" in [item["key"] for item in discovery.advanced_items("some-new-api")]


def test_thinking_mode_is_mutually_exclusive_choice():
    """思考模式是一组互斥选项，供界面"勾一个自动取消另一个"。"""
    item = next(item for item in discovery.advanced_items("anthropic-messages") if item["key"] == "thinking_mode")
    assert item["kind"] == "choice"
    assert [choice["value"] for choice in item["choices"]] == ["on", "off"]


def test_advanced_items_are_copies():
    """调用方拿到的是副本，改它不会污染注册表。"""
    items = discovery.advanced_items("openai-completions")
    items[0]["label"] = "改了"
    assert discovery.advanced_items("openai-completions")[0]["label"] != "改了"


def test_auth_headers_match_protocol():
    assert discovery.auth_headers("openai-completions", "sk-1") == {"authorization": "Bearer sk-1"}
    assert discovery.auth_headers("anthropic-messages", "sk-1") == {
        "x-api-key": "sk-1",
        "anthropic-version": "2023-06-01",
    }
    # 没有密钥时不带鉴权头，由上游决定是否拒绝。
    assert discovery.auth_headers("openai-completions", None) == {}


# --------------------------------------------------------------------------
# 供应商模型清单
# --------------------------------------------------------------------------


async def test_provider_models_prefers_upstream(model, storage):
    captured: list = []
    transport = sse_transport(
        [json.dumps({"data": [{"id": "zzz-model"}, {"id": "gpt-4o-mini"}, {"id": "aaa-model"}]})],
        captured=captured,
        content_type="application/json",
    )
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        response = await client.get("/api/providers/openai/models")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "upstream"
    assert body["error"] is None
    # 上游清单为准且按 id 排序，界面下拉顺序因此稳定。
    assert [item["id"] for item in body["models"]] == ["aaa-model", "gpt-4o-mini", "zzz-model"]
    assert captured[0].method == "GET"
    assert captured[0].url.endswith("/v1/models")


async def test_provider_models_carry_capabilities_and_flags(model, storage):
    """已知型号带出 preset 的真实参数，未知型号标记 known=False。"""
    transport = _transport({"data": [{"id": "gpt-4o-mini"}, {"id": "brand-new"}]})
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.get("/api/providers/openai/models")).json()

    unknown, known = body["models"]
    assert known["known"] is True
    assert known["capabilities"]["vision"] is True
    assert known["context_window"] == 128_000
    assert known["name"] == "GPT-4o mini"

    assert unknown["known"] is False
    # 未知型号只敢断言"能连、能流式"。
    assert unknown["capabilities"] == {
        "sse": True,
        "streaming": True,
        "tools": False,
        "json_schema": False,
        "vision": False,
        "reasoning": False,
    }
    assert unknown["context_window"] == 0
    assert body["advanced"]["items"], "无论上游是否可达，都要给出高级配置项"


async def test_provider_models_falls_back_to_preset(model, storage):
    """上游不可用时退回内置清单，并如实回报原因。"""
    transport = _transport({"error": "boom"}, status=500)
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.get("/api/providers/openai/models")).json()

    assert body["source"] == "preset"
    assert "HTTP 500" in body["error"]
    assert "gpt-4o-mini" in [item["id"] for item in body["models"]]


async def test_provider_models_uses_saved_key(model, storage):
    """密钥优先取已保存模型上的值——界面上配的密钥（而非环境变量）也要能用。"""
    captured: list = []
    transport = sse_transport(
        [json.dumps({"data": [{"id": "gpt-4o-mini"}]})], captured=captured, content_type="application/json"
    )
    app, registry = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    registry.register(
        Model(
            id="ui-model",
            name="UI",
            api="openai-completions",
            provider="openai",
            base_url=model.base_url,
            api_key="sk-from-ui",
        )
    )
    async with _client(app) as client:
        await client.get("/api/providers/openai/models")

    assert captured[0].header("authorization") == "Bearer sk-from-ui"


async def test_provider_models_reports_unparsable_response(model, storage):
    """上游返回 HTML / 纯文本（网关拦截页很常见）时也要降级，而不是 500。"""
    transport = sse_transport(["<html>not json</html>"], content_type="text/html")
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.get("/api/providers/openai/models")).json()

    assert body["source"] == "preset"
    assert "不是合法 JSON" in body["error"]


async def test_unknown_provider_is_404(model, storage):
    transport = _transport({"data": []})
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        response = await client.get("/api/providers/not-a-vendor/models")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# 模型连接测试
# --------------------------------------------------------------------------


async def test_probe_success(model, storage):
    transport = _transport()
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        response = await client.post("/api/models:test", json=_probe_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["code"] is None
    assert body["latency_ms"] >= 0
    assert body["text"] == "ok"


async def test_probe_sends_minimal_request(model, storage):
    """测试只发一次最小对话：max_tokens=1，且不流式。"""
    captured: list = []
    transport = sse_transport(
        [json.dumps(_completion_body())], captured=captured, content_type="application/json"
    )
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        await client.post("/api/models:test", json=_probe_payload(api_key="sk-probe"))

    sent = captured[0]
    assert sent.url.endswith("/v1/chat/completions")
    assert sent.json["max_tokens"] == 1
    assert sent.json["stream"] is False
    assert sent.header("authorization") == "Bearer sk-probe"


async def test_probe_reports_auth_failure(model, storage):
    transport = _transport({"error": {"message": "invalid api key"}}, status=401)
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.post("/api/models:test", json=_probe_payload())).json()

    assert body["ok"] is False
    assert body["code"] == "AUTH_INVALID"
    assert "invalid api key" in body["message"]


async def test_probe_reports_connection_failure(model, storage):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.post("/api/models:test", json=_probe_payload())).json()

    assert body["ok"] is False
    assert body["code"] == "CONN_FAILED"


async def test_probe_rejects_malformed_payload(model, storage):
    transport = _transport()
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        response = await client.post("/api/models:test", json={"provider": "openai"})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# 版本号与更新日志
# --------------------------------------------------------------------------


async def test_meta_exposes_version_and_changelog(model, storage):
    transport = _transport()
    app, _ = _build(model, transport, storage, client=client_for(transport, base_url=model.base_url))
    async with _client(app) as client:
        body = (await client.get("/api/meta")).json()

    assert body["version"] == __version__
    assert "## 0.8.0" in body["changelog"]