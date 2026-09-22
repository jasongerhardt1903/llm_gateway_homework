"""提示词版本管理：模板存储、变量替换、版本引用。

需求三大能力之一要求"支持模板存储、变量替换和版本引用"。分三层验证：

* **纯逻辑层**（:mod:`llm_gw.harness.prompts`）——占位符抽取与渲染，含"缺变量 /
  多给变量都报错"这条刻意从严的约定。
* **存储层**——``(name, version)`` 唯一键、同名多版本、``version`` 省略取最新。
* **接口层**——``prompt`` 引用真的渲染进上游请求体，解析失败是 **422 PROMPT_INVALID**
  （不是 500、也不是静默产出残缺提示）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from llm_gw.adapter.factory import create_adapter
from llm_gw.harness.prompts import (
    PromptTemplate,
    PromptTemplateError,
    extract_variables,
    render,
)
from llm_gw.harness.service import GatewayService, create_app
from llm_gw.harness.storage import Storage
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.util.clock import FakeClock

from tests.support.mock_transport import client_for


# --------------------------------------------------------------------------
# 纯逻辑层
# --------------------------------------------------------------------------


def test_extract_variables_is_sorted_and_deduped():
    """变量清单要能直接与调用方对齐，因此排序去重后存库。"""
    body = "你是 {{persona}}。请回答 {{question}}，用 {{persona}} 的口吻，再补 {{question}}。"

    assert extract_variables(body) == ["persona", "question"]
    # 带空格的写法同样认。
    assert extract_variables("{{  spaced  }}") == ["spaced"]


def test_render_substitutes_and_jsonifies_non_strings():
    """非字符串变量走 JSON：``str({...})`` 会产出单引号的"半 JSON"，容易被模型误解。"""
    out = render("问 {{q}}，选项 {{opts}}，次数 {{n}}", {"q": "天气", "opts": ["a", "b"], "n": 3})

    assert out == '问 天气，选项 ["a", "b"]，次数 3'


def test_render_rejects_missing_and_unknown_variables():
    """缺变量报错是底线；多给变量也报错——变量名拼错是模板最常见的故障。"""
    with pytest.raises(PromptTemplateError, match="缺少变量: q"):
        render("{{q}}", {})

    with pytest.raises(PromptTemplateError, match="未引用这些变量"):
        render("{{q}}", {"q": "hi", "qusetion": "拼错了"})


def test_template_render_adds_which_version_failed():
    """报错要指出是哪个版本，否则同名多版本时根本查不出问题出在哪一版。"""
    template = PromptTemplate(name="qa", version="v2", body="{{q}}")

    with pytest.raises(PromptTemplateError, match=r"模板 qa@v2 缺少变量: q"):
        template.render({})


# --------------------------------------------------------------------------
# 存储层
# --------------------------------------------------------------------------


@pytest.fixture
async def storage():
    store = await Storage(":memory:").init()
    yield store
    await store.close()


def _template(name: str, version: str, body: str) -> PromptTemplate:
    return PromptTemplate(name=name, version=version, body=body, variables=extract_variables(body))


async def test_storage_keeps_every_version_of_a_name(storage):
    """同名多版本共存：这正是"版本引用"的前提。"""
    await storage.save_prompt(_template("qa", "v1", "旧：{{q}}"))
    await storage.save_prompt(_template("qa", "v2", "新：{{q}}"))

    versions = await storage.prompt_versions("qa")

    assert [item.version for item in versions] == ["v2", "v1"]
    assert [item.body for item in versions] == ["新：{{q}}", "旧：{{q}}"]


async def test_storage_resolves_version_and_latest(storage):
    await storage.save_prompt(_template("qa", "v1", "旧：{{q}}"))
    await storage.save_prompt(_template("qa", "v2", "新：{{q}}"))

    assert (await storage.prompt("qa", "v1")).body == "旧：{{q}}"
    # version 省略 = 取最新；否则调用方升级模板就得改代码。
    assert (await storage.prompt("qa")).version == "v2"
    assert await storage.prompt("qa", "v9") is None
    assert await storage.prompt("nope") is None


async def test_storage_same_key_overwrites_and_delete_reports_missing(storage):
    """模板是配置：改错一个字再存一次是常态，重复写入应当覆盖而不是冲突。"""
    await storage.save_prompt(_template("qa", "v1", "第一版"))
    await storage.save_prompt(_template("qa", "v1", "改过的第一版"))

    assert len(await storage.prompt_versions("qa")) == 1
    assert (await storage.prompt("qa", "v1")).body == "改过的第一版"

    assert await storage.delete_prompt("qa", "v1") is True
    assert await storage.delete_prompt("qa", "v1") is False
    assert await storage.list_prompts() == []


# --------------------------------------------------------------------------
# 接口层
# --------------------------------------------------------------------------


def _body(text: str = "你好") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _capturing_transport(seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_body(), headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _service(openai_model, transport, *, storage=None) -> GatewayService:
    adapter = create_adapter(openai_model.api, client_for(transport, base_url=openai_model.base_url))
    registry = CapabilityRegistry()
    registry.register(openai_model)
    router = Router(registry, adapter_for=lambda _m: adapter, clock=FakeClock())
    return GatewayService(router, storage, clock=FakeClock())


def _payload(**overrides) -> dict:
    return {
        "task_id": "t-prompt",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        **overrides,
    }


async def test_prompt_reference_is_rendered_into_the_upstream_request(openai_model):
    """引用的模板要真的替换变量、真的出现在发给上游的 system 里。"""
    storage = await Storage(":memory:").init()
    seen: list[dict] = []
    try:
        await storage.save_prompt(_template("qa", "v1", "你是 {{persona}}，回答要简短。"))
        service = _service(openai_model, _capturing_transport(seen), storage=storage)
        async with await _client(create_app(service)) as client:
            response = await client.post(
                "/v1/tasks",
                json=_payload(prompt={"name": "qa", "version": "v1", "variables": {"persona": "严谨助教"}}),
            )
    finally:
        await storage.close()

    assert response.status_code == 200
    upstream_system = seen[0]["messages"][0]
    assert upstream_system == {"role": "system", "content": "你是 严谨助教，回答要简短。"}


async def test_each_version_renders_its_own_body(openai_model):
    """版本引用必须是**精确**的：点 v1 就不能拿到 v2 的正文。"""
    storage = await Storage(":memory:").init()
    seen: list[dict] = []
    try:
        await storage.save_prompt(_template("qa", "v1", "旧口径：{{q}}"))
        await storage.save_prompt(_template("qa", "v2", "新口径：{{q}}"))
        service = _service(openai_model, _capturing_transport(seen), storage=storage)
        async with await _client(create_app(service)) as client:
            await client.post(
                "/v1/tasks",
                json=_payload(task_id="t-v1", prompt={"name": "qa", "version": "v1", "variables": {"q": "x"}}),
            )
            # 省略 version：由网关补成最新版（v2）。
            await client.post(
                "/v1/tasks",
                json=_payload(task_id="t-latest", prompt={"name": "qa", "variables": {"q": "x"}}),
            )
    finally:
        await storage.close()

    assert [body["messages"][0]["content"] for body in seen] == ["旧口径：x", "新口径：x"]


async def test_missing_template_returns_422_with_prompt_invalid(openai_model):
    """引用不存在的模板 = 请求本身不合法（422），不是上游故障。"""
    storage = await Storage(":memory:").init()
    try:
        service = _service(openai_model, _capturing_transport([]), storage=storage)
        async with await _client(create_app(service)) as client:
            response = await client.post(
                "/v1/tasks", json=_payload(prompt={"name": "不存在", "variables": {}})
            )
    finally:
        await storage.close()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROMPT_INVALID"
    assert "不存在" in response.json()["detail"]["message"]


async def test_missing_variable_returns_422_and_is_logged_as_an_exchange(openai_model):
    """变量对不上同样是 422 —— 且要在通讯日志里留痕，否则 agent 查不到自己发了什么。"""
    storage = await Storage(":memory:").init()
    try:
        await storage.save_prompt(_template("qa", "v1", "请回答 {{question}}"))
        service = _service(openai_model, _capturing_transport([]), storage=storage)
        async with await _client(create_app(service)) as client:
            response = await client.post(
                "/v1/tasks",
                json=_payload(task_id="t-var", prompt={"name": "qa", "version": "v1", "variables": {}}),
            )
        rows = await storage.exchanges_for_task("t-var")
    finally:
        await storage.close()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROMPT_INVALID"
    assert [row["status"] for row in rows] == ["error"]
    assert rows[0]["error_code"] == "PROMPT_INVALID"


async def test_used_version_is_recorded_in_the_call_record(openai_model):
    """可观测性要求：记录里要能看出"这次用了哪个模板的哪一版"。"""
    storage = await Storage(":memory:").init()
    try:
        await storage.save_prompt(_template("qa", "v1", "旧：{{q}}"))
        await storage.save_prompt(_template("qa", "v2", "新：{{q}}"))
        service = _service(openai_model, _capturing_transport([]), storage=storage)
        async with await _client(create_app(service)) as client:
            await client.post(
                "/v1/tasks",
                json=_payload(
                    prompt={"name": "qa", "variables": {"q": "x"}},
                    metadata={"run_id": "r-1"},
                ),
            )
        calls = await storage.recent_calls(limit=1)
    finally:
        await storage.close()

    record = calls[0]
    assert record["prompt_name"] == "qa"
    # 缺省引用解析后固定到 v2，记录里不能留空——否则"发布过哪一版"事后无法复原。
    assert record["prompt_version"] == "v2"
    assert record["prompt_sha256"]


async def test_prompt_is_appended_before_the_callers_own_system(openai_model):
    """模板当背景、调用方当即时指令：模板在前，原 system 在后。"""
    storage = await Storage(":memory:").init()
    seen: list[dict] = []
    try:
        await storage.save_prompt(_template("qa", "v1", "背景：{{bg}}"))
        service = _service(openai_model, _capturing_transport(seen), storage=storage)
        async with await _client(create_app(service)) as client:
            await client.post(
                "/v1/tasks",
                json=_payload(
                    input={
                        "messages": [{"role": "user", "content": "hi"}],
                        "system": "只答一句。",
                    },
                    prompt={"name": "qa", "version": "v1", "variables": {"bg": "你是助教"}},
                ),
            )
    finally:
        await storage.close()

    assert seen[0]["messages"][0]["content"] == "背景：你是助教\n\n只答一句。"