"""Web 控制台 API 测试。

与 ``test_service.py`` 一样用 ``httpx.ASGITransport`` 直接调用应用，不启动服务器；
上游 LLM 由 ``httpx.MockTransport`` 模拟。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.adapter.factory import create_adapter
from llm_gw.adapter.presets.registry import all_models, find_model
from llm_gw.harness.retry import RetryPolicy
from llm_gw.harness.service import GatewayService
from llm_gw.harness.storage import Storage
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock
from llm_gw.web.api_models import ProfilePayload, profile_from_payload
from llm_gw.web.app import create_web_app, restore_config

from tests.support.mock_transport import client_for, openai_sse, sse_transport


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


def _completion_body(text: str = "hi") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _json_transport(payload: dict) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def _build(model, transport, storage):
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
    return create_web_app(service, registry=registry, storage=storage), registry, router


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _model_payload(model_id: str = "custom-1", **overrides) -> dict:
    payload = {
        "id": model_id,
        "name": "Custom",
        "provider": "openai",
        "api": "openai-completions",
        "base_url": "https://mock.local/v1",
        "context_window": 8000,
        "max_tokens": 1000,
        "cost": {"input": 1.0, "output": 2.0},
        "capabilities": {"streaming": True, "sse": True, "tools": True},
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# 供应商下拉
# --------------------------------------------------------------------------


async def test_providers_endpoint_returns_choices(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.get("/api/providers")

    assert response.status_code == 200
    providers = {item["provider"] for item in response.json()}
    assert {"openai", "deepseek", "anthropic"} <= providers
    # 下拉菜单需要 env_key 提示用户配哪个环境变量
    assert all(item["env_key"] for item in response.json())


# --------------------------------------------------------------------------
# 模型 CRUD
# --------------------------------------------------------------------------


async def test_list_models_returns_presets(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.get("/api/models")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} >= {"gpt-4o-mini", "deepseek-flash"}


async def test_create_model_persists_and_registers(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post("/api/models", json=_model_payload())

    assert response.status_code == 200
    assert registry.get("openai/custom-1") is not None

    persisted = await storage.load_config("models")
    assert any(item["id"] == "custom-1" for item in persisted)


async def test_delete_model_removes_and_persists(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        await client.post("/api/models", json=_model_payload())
        response = await client.delete("/api/models/openai/custom-1")

    assert response.status_code == 200
    assert registry.get("openai/custom-1") is None
    persisted = await storage.load_config("models")
    assert all(item["id"] != "custom-1" for item in persisted)


async def test_delete_missing_model_returns_404(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.delete("/api/models/openai/nope")

    assert response.status_code == 404


async def test_update_model_replaces_definition(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        await client.post("/api/models", json=_model_payload())
        response = await client.put(
            "/api/models/openai/custom-1", json=_model_payload(name="Renamed")
        )

    assert response.status_code == 200
    assert registry.get("openai/custom-1").name == "Renamed"


# --------------------------------------------------------------------------
# 模型高级配置与密钥（需求第 30 行）
# --------------------------------------------------------------------------


async def test_model_advanced_config_round_trips(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post(
            "/api/models",
            json=_model_payload(
                tag="便宜",
                advanced={
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "top_k": 40,
                    "thinking_mode": "on",
                    "max_tool_rounds": 5,
                },
            ),
        )

    assert response.status_code == 200
    saved = registry.get("openai/custom-1")
    assert saved.tag == "便宜"
    assert saved.advanced.temperature == 0.3
    assert saved.advanced.thinking_mode == "on"
    assert saved.advanced.max_tool_rounds == 5
    # 回显的 payload 同样带上高级配置，供界面回填表单
    assert response.json()["advanced"]["top_k"] == 40


async def test_model_rejects_invalid_advanced_config(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post(
            "/api/models", json=_model_payload(advanced={"temperature": 9.0})
        )

    assert response.status_code == 422


async def test_api_key_is_write_only(model, storage):
    """密钥只写不回显：任何响应都不得带出 ``api_key``，只给 ``api_key_set``。"""
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        created = await client.post("/api/models", json=_model_payload(api_key="sk-secret"))
        listing = await client.get("/api/models")

    assert created.status_code == 200
    assert created.json()["api_key"] is None
    assert created.json()["api_key_set"] is True
    assert registry.get("openai/custom-1").api_key == "sk-secret"

    body = listing.text
    assert "sk-secret" not in body
    saved = next(item for item in listing.json() if item["id"] == "custom-1")
    assert saved["api_key"] is None and saved["api_key_set"] is True


async def test_update_model_without_api_key_keeps_existing(model, storage):
    """界面不回显密钥，因此保存时不会带上它；不能把已配置的密钥清空。"""
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        await client.post("/api/models", json=_model_payload(api_key="sk-secret"))
        await client.put("/api/models/openai/custom-1", json=_model_payload(name="Renamed"))

    assert registry.get("openai/custom-1").api_key == "sk-secret"


async def test_update_model_with_empty_api_key_clears_it(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        await client.post("/api/models", json=_model_payload(api_key="sk-secret"))
        await client.put("/api/models/openai/custom-1", json=_model_payload(api_key=""))

    assert registry.get("openai/custom-1").api_key == ""


# --------------------------------------------------------------------------
# gwprofile（需求第 31 行）
# --------------------------------------------------------------------------


def _profile_payload(name: str = "prod", **overrides) -> dict:
    payload = {
        "name": name,
        "display_name": name,
        "models": [{"label": "openai/gpt-4o-mini", "prefer_own_config": False}],
        "template_enabled": False,
        "template": {"temperature": 0.7},
        "route_mode": "dynamic",
        "static_order": [],
        "retry_enabled": True,
        "max_retries": 3,
    }
    payload.update(overrides)
    return payload


async def test_get_profiles_reflects_registry(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)
    registry.set_profile(profile_from_payload(ProfilePayload.model_validate(_profile_payload())))

    async with await _client(app) as client:
        response = await client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["prod"]
    assert body[0]["models"] == [{"label": "openai/gpt-4o-mini", "prefer_own_config": False}]
    assert body[0]["retry_enabled"] is True


async def test_create_profile_persists(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)

    async with await _client(app) as client:
        response = await client.post(
            "/api/profiles",
            json=_profile_payload(route_mode="static", static_order=["openai/gpt-4o-mini"]),
        )

    assert response.status_code == 200
    profile = registry.get_profile("prod")
    assert profile is not None
    assert profile.route_mode == "static"
    assert profile.static_order == ["openai/gpt-4o-mini"]

    persisted = await storage.load_config("profiles")
    assert persisted[0]["name"] == "prod"


async def test_update_profile_replaces_definition(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)

    async with await _client(app) as client:
        await client.post("/api/profiles", json=_profile_payload())
        response = await client.put(
            "/api/profiles/prod", json=_profile_payload(max_retries=7)
        )

    assert response.status_code == 200
    assert registry.get_profile("prod").max_retries == 7


async def test_delete_profile_removes_and_persists(model, storage):
    app, registry, _ = _build(model, sse_transport(openai_sse()), storage)

    async with await _client(app) as client:
        await client.post("/api/profiles", json=_profile_payload())
        response = await client.delete("/api/profiles/prod")

    assert response.status_code == 200
    assert registry.get_profile("prod") is None
    assert await storage.load_config("profiles") == []


async def test_delete_missing_profile_returns_404(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)

    async with await _client(app) as client:
        response = await client.delete("/api/profiles/nope")

    assert response.status_code == 404


async def test_create_profile_rejects_invalid_route_mode(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)

    async with await _client(app) as client:
        response = await client.post("/api/profiles", json=_profile_payload(route_mode="random"))

    assert response.status_code == 422


async def test_restore_config_round_trips(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        await client.post("/api/models", json=_model_payload())
        await client.post(
            "/api/profiles",
            json=_profile_payload(
                models=[{"label": "openai/custom-1", "prefer_own_config": True}],
                route_mode="static",
                static_order=["openai/custom-1"],
                max_retries=2,
            ),
        )

    # 模拟重启：新的空注册表 + 从 config 恢复
    fresh = CapabilityRegistry()
    await restore_config(fresh, storage)

    assert fresh.get("openai/custom-1") is not None
    restored = fresh.get_profile("prod")
    assert restored is not None
    assert restored.static_order == ["openai/custom-1"]
    assert restored.max_retries == 2
    assert restored.ref_for("openai/custom-1").prefer_own_config is True


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


async def test_dashboard_returns_metrics_and_model_rows(model, storage):
    app, _, _ = _build(model, _json_transport(_completion_body()), storage)

    async with await _client(app) as client:
        await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False}
        )
        response = await client.get("/api/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert "qps_1m" in body and "error_rate_1m" in body
    rows = {row["model"]: row for row in body["models"]}
    assert "gpt-4o-mini" in rows
    assert rows["gpt-4o-mini"]["available"] is True
    assert rows["gpt-4o-mini"]["remaining_tokens"] is None  # 未配置配额


async def test_dashboard_remaining_tokens_respects_quota(model, storage):
    app, registry, _ = _build(model, _json_transport(_completion_body()), storage)
    registry.quota = {"openai/gpt-4o-mini": 100}
    registry.record_usage(registry.get("openai/gpt-4o-mini"), 30)

    async with await _client(app) as client:
        response = await client.get("/api/dashboard")

    rows = {row["model"]: row for row in response.json()["models"]}
    assert rows["gpt-4o-mini"]["used_tokens"] == 30
    assert rows["gpt-4o-mini"]["remaining_tokens"] == 70


# --------------------------------------------------------------------------
# Trace 搜索
# --------------------------------------------------------------------------


async def test_trace_search_and_detail(model, storage):
    app, _, _ = _build(model, _json_transport(_completion_body()), storage)

    async with await _client(app) as client:
        await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False}
        )
        listing = await client.get("/api/traces")
        assert listing.status_code == 200
        call = listing.json()[0]
        trace_id = call["trace_id"]

        detail = await client.get(f"/api/traces/{trace_id}")

    assert detail.status_code == 200
    assert detail.json()["trace_id"] == trace_id
    assert len(detail.json()["calls"]) == 1


async def test_trace_search_filters_by_keyword(model, storage):
    app, _, _ = _build(model, _json_transport(_completion_body()), storage)

    async with await _client(app) as client:
        await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "stream": False}
        )
        hit = await client.get("/api/traces", params={"q": "gpt-4o-mini"})
        miss = await client.get("/api/traces", params={"q": "no-such-model"})

    assert len(hit.json()) == 1
    assert miss.json() == []


async def test_missing_trace_returns_404(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.get("/api/traces/does-not-exist")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


async def test_chat_stream_proxies_sse(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse(text="你好")), storage)

    async with await _client(app) as client:
        response = await client.post(
            "/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
        )

    assert response.status_code == 200
    assert "event: text_delta" in response.text
    assert "data: [DONE]" in response.text


async def test_chat_stream_records_trace_with_chat_prefix(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse(text="hi")), storage)

    async with await _client(app) as client:
        await client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})

    calls = await storage.recent_calls()
    assert calls[0]["trace_id"].startswith("chat-")


async def test_chat_rejects_empty_messages(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post("/api/chat", json={"messages": []})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# 原始 task 校验接口
# --------------------------------------------------------------------------


async def test_validate_task_reports_syntax_error(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post(
            "/api/tasks:validate", content=b"{bad", headers={"content-type": "application/json"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"


async def test_validate_task_accepts_valid_payload(model, storage):
    app, _, _ = _build(model, sse_transport(openai_sse()), storage)
    async with await _client(app) as client:
        response = await client.post(
            "/api/tasks:validate",
            json={"task_id": "t-1", "input": {"messages": [{"role": "user", "content": "hi"}]}},
        )

    assert response.status_code == 200
    assert response.json() == {"valid": True}