"""模型发现：向供应商查询可用模型、模型能力与高级配置项。

需求（模型管理层功能第 1 条）要求配置模型时"向模型 / 模型供应商查询"三件事：

* 1a —— 查询模型**能力**，据此生成可勾选的能力项；
* 1b —— 查询供应商的**模型版本**，据此生成模型下拉菜单；
* 1c —— 按供应商情况展示**高级配置项**，每项带一个勾选框，互斥项只能选一个。

三件事的答案形态不同，因此本模块分三部分：

1. :data:`ADVANCED_ITEMS` —— 各协议支持哪些高级项、哪些项互斥；
2. :func:`list_models` —— 拉取 ``GET {base_url}/models``；供应商没有该端点或
   调用失败时回退到内置 preset 清单，并如实回报 ``source`` / ``error``。
   控制台长期运行，因此外面套一层 :class:`ModelCatalogCache` 短时缓存，避免
   每开一次"新增模型"就打一次上游；
3. :func:`probe_connection` —— 真实最小对话（``ping`` + ``max_tokens=1``），
   验证连通性、鉴权与模型 ID 是否有效；不入库、不计费、不落 Trace。

上游 ``/models`` 只回模型 id，不提供能力与价格，因此已知型号按 preset 补齐；
未知型号给协议默认值并标记 ``known=False``，由界面提示人工确认——比编造能力
更诚实。所有请求都走注入的 ``httpx.AsyncClient``，测试可由 mocktransport
全量驱动，不依赖真实供应商。
"""

from __future__ import annotations

import copy
import time
from dataclasses import asdict
from typing import Any

import httpx

from ..core.advanced import AdvancedConfig
from ..core.errors import (
    ErrorCode,
    GatewayError,
    classify_error_code,
    format_provider_error,
    normalize_provider_error,
)
from ..core.messages import Capabilities, Model
from ..core.schema import Task, TaskInput, TaskMessage
from ..util.clock import Clock, RealClock
from .base import AdapterOptions
from .factory import create_adapter
from .presets.registry import find_model, get_preset
from .protocols.anthropic_messages import ANTHROPIC_VERSION, AnthropicMessagesAdapter
from .protocols.openai_compat import OpenAICompatAdapter

__all__ = [
    "ADVANCED_ITEMS",
    "CACHE_TTL_ERROR",
    "CACHE_TTL_OK",
    "ModelCatalogCache",
    "advanced_items",
    "auth_headers",
    "list_models",
    "probe_connection",
    "PROBE_MAX_TOKENS",
]

#: 输入控件类型。``choice`` 的项是一组**互斥**选项。
_NUMBER = "number"
_CHOICE = "choice"

_TEMPERATURE: dict[str, Any] = {"key": "temperature", "label": "temperature", "kind": _NUMBER, "step": 0.05}
_TOP_P: dict[str, Any] = {"key": "top_p", "label": "top_p", "kind": _NUMBER, "step": 0.05}
_TOP_K: dict[str, Any] = {"key": "top_k", "label": "top_k", "kind": _NUMBER, "step": 1}
_TOOL_ROUNDS: dict[str, Any] = {
    "key": "max_tool_rounds",
    "label": "工具调用轮数",
    "kind": _NUMBER,
    "step": 1,
}
#: 思考模式的两个选项互斥（需求第 54 行："用户只能选择一个，另外的选项要自动
#: 被取消勾选"）；都不勾选 = 不发送该字段，跟随供应商默认。
_THINKING: dict[str, Any] = {
    "key": "thinking_mode",
    "label": "思考模式",
    "kind": _CHOICE,
    "choices": [
        {"value": "on", "label": "开启"},
        {"value": "off", "label": "关闭"},
    ],
    "note": "开启与关闭互斥；都不勾选则跟随模型默认。",
}

#: 各协议支持的高级配置项。差异来自协议本身：OpenAI 的 chat.completions 接受
#: temperature / top_p 而不接受 top_k，Anthropic Messages 两者都接受。界面因此
#: 只展示该供应商真的认得的参数，避免配好了却在调用时被上游 400 拒掉。
ADVANCED_ITEMS: dict[str, list[dict[str, Any]]] = {
    OpenAICompatAdapter.api: [_TEMPERATURE, _TOP_P, _TOOL_ROUNDS, _THINKING],
    AnthropicMessagesAdapter.api: [_TEMPERATURE, _TOP_P, _TOP_K, _TOOL_ROUNDS, _THINKING],
}

#: 未知协议：宁可多给几个可选项，由用户自行判断。
_DEFAULT_ITEMS: list[dict[str, Any]] = [_TEMPERATURE, _TOP_P, _TOP_K, _TOOL_ROUNDS, _THINKING]

#: 连接测试的提示词与输出上限。测试只验证"能不能通"，不需要真实回答，
#: 因此把输出压到 1 token，把费用与延迟都降到最低。
PROBE_PROMPT = "ping"
PROBE_MAX_TOKENS = 1

#: 清单查询的进程内缓存 TTL（秒）。"这家供应商有哪些模型"几分钟内不会变，
#: 因此成功结果缓存久一点；失败结果只缓存很短时间——密钥刚换好、代理刚起来时
#: 能很快重试成功，同时又不会因为页面来回切换就反复打上游。
CACHE_TTL_OK = 300.0
CACHE_TTL_ERROR = 30.0


class ModelCatalogCache:
    """:func:`list_models` 的进程内短时缓存。

    查一次要出网花一个往返（密钥失效时还要等一次 401 回来），而清单本身很稳定，
    所以同一个 ``(provider, api, base_url)`` 在 TTL 内只查一次。``base_url``
    必须进键：用户可能把同一供应商指到自建代理，换了地址就得重新查，否则吃的
    还是老清单。

    返回的是副本，调用方改动不会污染缓存。``clock`` 可注入（默认真实时钟），
    测试因此能直接推进时间验证过期，不必真的等待。
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else RealClock()
        self._entries: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

    async def get(
        self,
        provider: str,
        *,
        api: str,
        base_url: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """命中未过期的缓存就返回缓存值，否则查一次上游并写入缓存。"""
        key = (provider, api, base_url)
        now = self._clock.now()
        hit = self._entries.get(key)
        if hit is not None and now < hit[0]:
            return copy.deepcopy(hit[1])

        result = await list_models(provider, api=api, base_url=base_url, client=client, api_key=api_key)
        ttl = CACHE_TTL_OK if result["source"] == "upstream" else CACHE_TTL_ERROR
        self._entries[key] = (now + ttl, result)
        return copy.deepcopy(result)


def advanced_items(api: str) -> list[dict[str, Any]]:
    """某协议支持的高级配置项（深拷贝，调用方可安全修改）。"""
    return copy.deepcopy(ADVANCED_ITEMS.get(api, _DEFAULT_ITEMS))


def default_capabilities() -> Capabilities:
    """未知型号的能力缺省值。

    只敢断言"能连、能流式"——这两个由协议本身保证；工具、结构化输出、视觉、
    推理都要看具体型号，不能凭空打开。
    """
    return Capabilities(sse=True, streaming=True)


def auth_headers(api: str, api_key: str | None) -> dict[str, str]:
    """供应商的鉴权头。

    与各协议 ``build_request`` 里写的头保持一致；``/models`` 是 GET，走不到
    adapter 的请求构造，因此这里单独给一份，避免"只有列表接口忘了带密钥"。
    """
    if not api_key:
        return {}
    if api == AnthropicMessagesAdapter.api:
        return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    return {"authorization": f"Bearer {api_key}"}


async def list_models(
    provider: str,
    *,
    api: str,
    base_url: str,
    client: httpx.AsyncClient,
    api_key: str | None = None,
) -> dict[str, Any]:
    """供应商的模型清单 + 每项能力 + 该协议的高级配置项。

    先查上游 ``GET /models``；拿不到（端点不存在、鉴权失败、网络不通）就退回
    内置 preset 清单，并在 ``error`` 里说明原因。回退是必要的：Anthropic 的历史
    账号、自建代理、离线环境都可能没有这个端点，而"下拉菜单里空无一物"会让
    模型配置完全走不下去。
    """
    upstream, error = await _fetch_upstream_ids(base_url, api=api, client=client, api_key=api_key)
    if upstream:
        return _catalog(provider, api=api, base_url=base_url, source="upstream", error=None, model_ids=upstream)

    preset = get_preset(provider)
    model_ids = [model.id for model in preset.models] if preset is not None else []
    return _catalog(
        provider,
        api=api,
        base_url=base_url,
        source="preset" if model_ids else "empty",
        error=error,
        model_ids=model_ids,
    )


async def probe_connection(model: Model, *, client: httpx.AsyncClient, api_key: str | None = None) -> dict[str, Any]:
    """模型连接测试：发一次最小对话，回报成功/失败原因与耗时。

    复用 adapter 的非流式调用路径，因此错误分类、上游报文的截断与可读化
    都与真实调用一致——测试里看到的失败原因，就是线上会看到的那个。
    """
    task = Task(
        task_id="model-probe",
        input=TaskInput(messages=[TaskMessage(role="user", content=PROBE_PROMPT)], stream=False),
    )
    options = AdapterOptions(api_key=api_key, advanced=AdvancedConfig(max_tokens=PROBE_MAX_TOKENS))
    started = time.perf_counter()
    try:
        message = await create_adapter(model.api, client).complete(model, task, options)
    except GatewayError as exc:
        return _probe_result(False, started, code=exc.code.value, message=exc.message)
    except httpx.HTTPError as exc:
        return _probe_result(
            False,
            started,
            code=classify_error_code(exc).value,
            message=format_provider_error(normalize_provider_error(exc)),
        )
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要变成可读的测试结论
        return _probe_result(False, started, code=ErrorCode.UNKNOWN.value, message=str(exc))

    return _probe_result(
        True,
        started,
        code=None,
        message="连接成功",
        text=message.text(),
        response_model=message.response_model or "",
    )


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _probe_result(
    ok: bool,
    started: float,
    *,
    code: str | None,
    message: str,
    text: str = "",
    response_model: str = "",
) -> dict[str, Any]:
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "text": text,
        "response_model": response_model,
    }


async def _fetch_upstream_ids(
    base_url: str,
    *,
    api: str,
    client: httpx.AsyncClient,
    api_key: str | None,
) -> tuple[list[str], str | None]:
    """调用上游 ``GET {base_url}/models``，返回 ``(模型 id 列表, 失败原因)``。"""
    url = f"{base_url.rstrip('/')}/models"
    try:
        response = await client.get(url, headers=auth_headers(api, api_key))
    except httpx.HTTPError as exc:
        return [], f"请求 {url} 失败：{format_provider_error(normalize_provider_error(exc))}"

    if response.status_code >= 400:
        return [], f"{url} 返回 HTTP {response.status_code}：{response.text[:200]}"

    try:
        payload = response.json()
    except ValueError:
        return [], f"{url} 返回的不是合法 JSON"

    # OpenAI 兼容协议是 {"data": [{"id": ...}]}；也容忍直接返回数组的实现。
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return [], f"{url} 的响应缺少 data 数组"

    ids = [item["id"] for item in data if isinstance(item, dict) and item.get("id")]
    if not ids:
        return [], f"{url} 未返回任何模型"
    return sorted(ids), None


def _catalog(
    provider: str,
    *,
    api: str,
    base_url: str,
    source: str,
    error: str | None,
    model_ids: list[str],
) -> dict[str, Any]:
    return {
        "provider": provider,
        "api": api,
        "base_url": base_url,
        "source": source,
        "error": error,
        "advanced": {"items": advanced_items(api)},
        "models": [_spec(provider, model_id, api=api, base_url=base_url) for model_id in model_ids],
    }


def _spec(provider: str, model_id: str, *, api: str, base_url: str) -> dict[str, Any]:
    """一个模型的可配置项初值：能力、上下文、价格。

    preset 里已知的型号直接用它的真实参数；未知型号给协议默认能力并标记
    ``known=False``，界面据此提示"该型号不在内置清单中，能力请人工确认"。
    """
    known = find_model(provider, model_id)
    capabilities = known.capabilities if known is not None else default_capabilities()
    return {
        "id": model_id,
        "name": known.name if known is not None else model_id,
        "display_provider": (known.display_provider if known is not None else "") or provider,
        "api": api,
        "base_url": base_url,
        "context_window": known.context_window if known is not None else 0,
        "max_tokens": known.max_tokens if known is not None else 0,
        "cost": asdict(known.cost) if known is not None else {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "capabilities": asdict(capabilities),
        "known": known is not None,
    }