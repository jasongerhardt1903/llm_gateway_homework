"""agent 接口鉴权测试（需求：Harness 层功能第 1 条"支持简单的 password"）。

约定（与 docs/interface.md 同步）：

* 密码来自环境变量 ``LLM_GW_AGENT_PASSWORD``，不写进数据库也不入库代码；
* 请求以 ``Authorization: Bearer <password>`` 携带；
* **未配置密码时不强制**——本地开发与 TDD 迭代不需要先造一个密码，
  启动时会打一条告警提示"接口未设防"；
* ``/health`` 豁免，负载均衡探活通常不带凭证；
* 鉴权只作用于 agent 接口（``/v1/tasks``、``/v1/tasks:stream``），
  控制台 ``/api/*`` 不受影响，否则页面自己都打不开。

失败一律返回 401 + 稳定错误码 ``AUTH_REQUIRED``（与上游密钥失效的 ``AUTH_INVALID``
刻意区分：前者发生在选模型之前，换模型毫无意义），错误体结构与既有接口一致。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.adapter.factory import create_adapter
from llm_gw.harness.service import GatewayService, create_app
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock

from tests.support.mock_transport import client_for

#: 鉴权读取的环境变量名——测试与实现共用一个常量来源，避免拼写漂移。
from llm_gw.harness.service import AGENT_PASSWORD_ENV

PASSWORD = "s3cret-pass"

TASK_PAYLOAD = {
    "task_id": "t-1",
    "input": {"messages": [{"role": "user", "content": "hi"}]},
}


def _completion_body(text: str = "你好") -> dict:
    """非流式 chat.completions 响应体。"""
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


def _app(model, transport: httpx.MockTransport):
    client = client_for(transport, base_url=model.base_url)
    adapter = create_adapter(model.api, client)
    registry = CapabilityRegistry()
    registry.register(model)
    router = Router(registry, adapter_for=lambda _m: adapter, clock=FakeClock())
    return create_app(GatewayService(router, None, clock=FakeClock()))


@pytest.fixture
def app(openai_model):
    """一个"能正常回应"的 agent 应用（底层是 mock transport，不联网）。"""
    return _app(openai_model, _json_transport(_completion_body()))


def _require_password(monkeypatch) -> None:
    """把密码写进环境变量；用例结束由 monkeypatch 自动还原。"""
    monkeypatch.setenv(AGENT_PASSWORD_ENV, PASSWORD)


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


# --------------------------------------------------------------------------
# 未配置密码：不强制
# --------------------------------------------------------------------------


async def test_allows_when_password_not_configured(monkeypatch, app):
    """未配置密码时放行——本地开发不必先造密码。"""
    monkeypatch.delenv(AGENT_PASSWORD_ENV, raising=False)

    async with await _client(app) as client:
        response = await client.post("/v1/tasks", json=TASK_PAYLOAD)

    assert response.status_code == 200
    assert response.json()["text"] == "你好"


# --------------------------------------------------------------------------
# 已配置密码：强制校验
# --------------------------------------------------------------------------


async def test_rejects_missing_credentials(monkeypatch, app):
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.post("/v1/tasks", json=TASK_PAYLOAD)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_rejects_wrong_password(monkeypatch, app):
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.post(
            "/v1/tasks", json=TASK_PAYLOAD, headers={"authorization": "Bearer wrong-pass"}
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_rejects_non_bearer_scheme(monkeypatch, app):
    """只接受 Bearer：Basic / 裸密码都不算，避免"看起来传了"其实没校验。"""
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.post(
            "/v1/tasks", json=TASK_PAYLOAD, headers={"authorization": f"Basic {PASSWORD}"}
        )

    assert response.status_code == 401


async def test_accepts_correct_password(monkeypatch, app):
    """凭证正确时请求进入业务逻辑（正常返回翻译后的文本）。"""
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.post(
            "/v1/tasks", json=TASK_PAYLOAD, headers={"authorization": f"Bearer {PASSWORD}"}
        )

    assert response.status_code == 200
    assert response.json()["text"] == "你好"


async def test_stream_endpoint_is_guarded_too(monkeypatch, app):
    """流式端点同样是门——不能只守住非流式那一半。"""
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.post("/v1/tasks:stream", json=TASK_PAYLOAD)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_health_is_exempt(monkeypatch, app):
    """/health 豁免：探活程序不会带凭证，被 401 会误判服务不可用。"""
    _require_password(monkeypatch)
    async with await _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}