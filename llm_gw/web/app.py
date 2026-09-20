"""Web 控制台后端：模型定义、gwprofile 配置、Chat SSE 代理、Dashboard、Trace 搜索。

与 ``harness/service.py`` 的关系：service 是**面向 agent 的 API**（``/v1/*``），
本模块是**面向人的控制台 API**（``/api/*``）。两者共用同一个 Router 与 Storage，
但契约不同——控制台需要模型 CRUD 与聚合视图，agent 不需要。

模型清单与 gwprofile 通过 :class:`Storage` 的 config 表持久化，重启后不丢失。

版本：0.8.1
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..adapter import discovery
from ..adapter.presets.registry import get_preset, provider_choices
from ..core.messages import Model
from ..core.schema import SchemaViolation, SyntaxViolation, Task, validate_schema, validate_syntax
from ..harness.service import GatewayService
from ..harness.storage import Storage
from ..router.registry import CapabilityRegistry
from .api_models import (
    ChatRequest,
    ModelPayload,
    ProfilePayload,
    model_from_payload,
    model_to_payload,
    profile_from_payload,
    profile_to_payload,
)

__all__ = ["create_web_app", "restore_config", "CONFIG_MODELS", "CONFIG_PROFILES"]

#: config 表中的键名。
CONFIG_MODELS = "models"
CONFIG_PROFILES = "profiles"

#: 前端构建产物目录（Phase 5 的 React/Vite 工程）。
_WEBAPP_DIST = Path(__file__).resolve().parents[2] / "webapp" / "dist"

#: 更新日志文件（需求"管理与交互层"第 6 条要求页面可查看更新日志）。
_CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

#: 未注入连接池时，控制台自己查询上游用的超时。
_UPSTREAM_TIMEOUT = httpx.Timeout(30.0)


def create_web_app(
    service: GatewayService,
    *,
    registry: CapabilityRegistry | None = None,
    storage: Storage | None = None,
    client: httpx.AsyncClient | None = None,
    api_key_for: Callable[[Model], str | None] | None = None,
) -> FastAPI:
    """构造控制台应用。

    ``registry`` 默认取 ``service.router.registry``；``storage`` 默认取
    ``service.storage``。显式传入便于测试注入内存库。

    ``client`` 与 ``api_key_for`` 供"查询供应商模型清单"和"模型连接测试"使用：
    组合根把真实运行时的连接池与密钥解析策略注入进来，测试则注入 mocktransport
    驱动的客户端；两者不注入时由本模块临时建一个连接池、密钥只认模型上已存的
    值，控制台因此可以独立使用。
    """
    router = service.router
    registry = registry if registry is not None else router.registry
    storage = storage if storage is not None else service.storage

    app = FastAPI(title="LLM Gateway Console", version=__version__)

    # 供应商清单很稳定，缓存起来，避免每开一次「新增模型」就打一次上游。
    catalog = discovery.ModelCatalogCache()

    # -- 供应商（模型定义页的下拉菜单数据源） ------------------------------

    @app.get("/api/providers")
    async def list_providers() -> list[dict[str, str]]:
        return provider_choices()

    # -- 供应商可选模型与能力（需求"模型管理层"第 1 条） -------------------

    @app.get("/api/providers/{provider}/models")
    async def list_provider_models(provider: str) -> dict:
        """向供应商查询可选模型版本（1b）及其能力 / 高级配置项（1a、1c）。

        供应商未知时 404：没有 preset 就没有 base URL 与协议，无从查询。
        结果按 ``(provider, api, base_url)`` 走短时缓存，见 ``ModelCatalogCache``。
        """
        preset = get_preset(provider)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"未知供应商 {provider}")
        async with _upstream_client(client) as upstream:
            return await catalog.get(
                provider,
                api=preset.api,
                base_url=_provider_base_url(registry, provider) or preset.base_url,
                client=upstream,
                api_key=_provider_api_key(registry, provider),
            )

    # -- 模型 CRUD --------------------------------------------------------

    @app.get("/api/models")
    async def list_models() -> list[dict]:
        return [model_to_payload(model).model_dump() for model in registry.all_models()]

    @app.post("/api/models")
    async def create_model(payload: ModelPayload) -> dict:
        model = model_from_payload(payload)
        registry.register(model)
        await _persist_models(registry, storage)
        return model_to_payload(model).model_dump()

    @app.put("/api/models/{provider}/{model_id}")
    async def update_model(provider: str, model_id: str, payload: ModelPayload) -> dict:
        label = f"{provider}/{model_id}"
        existing = registry.get(label)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"模型 {label} 不存在")
        # api_key 为 None 表示"不修改"：界面不回显密钥，因此每次保存都不会带上它，
        # 若直接覆盖就会把已配置的密钥清空。
        api_key = existing.api_key if payload.api_key is None else payload.api_key
        # 改 id/provider 时先移除旧标签，避免留下孤儿条目。
        _remove_model(registry, label)
        model = model_from_payload(payload, api_key=api_key)
        registry.register(model)
        await _persist_models(registry, storage)
        return model_to_payload(model).model_dump()

    @app.delete("/api/models/{provider}/{model_id}")
    async def delete_model(provider: str, model_id: str) -> dict:
        label = f"{provider}/{model_id}"
        if not _remove_model(registry, label):
            raise HTTPException(status_code=404, detail=f"模型 {label} 不存在")
        await _persist_models(registry, storage)
        return {"deleted": label}

    # -- 模型连接测试（需求"模型管理层"第 2 条） ---------------------------

    @app.post("/api/models:test")
    async def test_model(payload: ModelPayload) -> dict:
        """发一次最小对话验证连接、鉴权与模型 ID。

        请求体与新增/编辑模型一致，因此**未保存**的模型也能先测再存；密钥优先级
        与运行期一致：本次请求带的 > 该模型已保存的 > 环境变量。

        测试结果是业务结论而非控制台错误，因此上游失败也返回 200 + ``ok=false``，
        由界面按结果展示原因；只有请求体本身不合法才走 422。
        """
        model = model_from_payload(payload)
        existing = registry.get(model.label())
        if payload.api_key is not None:
            api_key = payload.api_key or None
        elif existing is not None and existing.api_key:
            api_key = existing.api_key
        else:
            api_key = api_key_for(model) if api_key_for is not None else None
        async with _upstream_client(client) as upstream:
            return await discovery.probe_connection(model, client=upstream, api_key=api_key)

    # -- 版本号与更新日志（需求"管理与交互层"第 6 条） ----------------------

    @app.get("/api/meta")
    async def meta() -> dict:
        return {"version": __version__, "changelog": _changelog_text()}

    # -- gwprofile（需求第 31 行） -----------------------------------------

    @app.get("/api/profiles")
    async def list_profiles() -> list[dict]:
        return [profile_to_payload(profile).model_dump() for profile in registry.profiles.values()]

    @app.post("/api/profiles")
    async def create_profile(payload: ProfilePayload) -> dict:
        try:
            profile = profile_from_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        registry.set_profile(profile)
        await _persist_profiles(registry, storage)
        return profile_to_payload(profile).model_dump()

    @app.put("/api/profiles/{name}")
    async def update_profile(name: str, payload: ProfilePayload) -> dict:
        if registry.get_profile(name) is None:
            raise HTTPException(status_code=404, detail=f"profile {name} 不存在")
        try:
            profile = profile_from_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # 改名时先删旧键，避免留下一个同名旧 profile。
        registry.remove_profile(name)
        registry.set_profile(profile)
        await _persist_profiles(registry, storage)
        return profile_to_payload(profile).model_dump()

    @app.delete("/api/profiles/{name}")
    async def delete_profile(name: str) -> dict:
        if registry.get_profile(name) is None:
            raise HTTPException(status_code=404, detail=f"profile {name} 不存在")
        registry.remove_profile(name)
        await _persist_profiles(registry, storage)
        return {"deleted": name}

    # -- Dashboard --------------------------------------------------------

    @app.get("/api/dashboard")
    async def dashboard() -> dict:
        if service.query is None:
            raise HTTPException(status_code=503, detail="未配置存储，Dashboard 不可用")
        data = await service.query.dashboard()
        # 补上"各模型使用次数 / token 数 / 剩余 token"，供 Dashboard 表格直接渲染。
        data["models"] = _model_usage(registry, await service.query.storage.model_health())
        return data

    # -- Trace 搜索 -------------------------------------------------------

    @app.get("/api/traces")
    async def search_traces(q: str = "", limit: int = 50) -> list[dict]:
        if storage is None:
            raise HTTPException(status_code=503, detail="未配置存储，Trace 不可用")
        if q:
            return await storage.search_traces(q, limit=limit)
        return await storage.recent_calls(limit=limit)

    @app.get("/api/traces/{trace_id}")
    async def get_trace(trace_id: str) -> dict:
        if storage is None:
            raise HTTPException(status_code=503, detail="未配置存储，Trace 不可用")
        calls = await storage.trace(trace_id)
        if not calls:
            raise HTTPException(status_code=404, detail=f"trace {trace_id} 不存在")
        return {"trace_id": trace_id, "calls": calls}

    # -- Chat SSE 代理 ----------------------------------------------------

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest) -> StreamingResponse:
        task = _task_from_chat(payload)
        return StreamingResponse(
            service.stream_sse(task, trace_id=f"chat-{task.task_id}"),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> dict:
        task = _task_from_chat(payload)
        message = await service.complete(task, trace_id=f"chat-{task.task_id}")
        return {
            "task_id": task.task_id,
            "terminal": message.terminal_state(),
            "text": message.text(),
            "stop_reason": message.stop_reason,
            "error_message": message.error_message,
        }

    # -- 原始 task 调试接口（两层校验的错误码同样返回给控制台） -----------

    @app.post("/api/tasks:validate")
    async def validate_task(request: Request) -> dict:
        raw = await request.body()
        try:
            data = validate_syntax(raw)
        except SyntaxViolation as exc:
            raise HTTPException(status_code=400, detail={"code": "REQUEST_INVALID", "message": str(exc)})
        try:
            validate_schema(data)
        except SchemaViolation as exc:
            raise HTTPException(status_code=422, detail={"code": "REQUEST_INVALID", "message": str(exc)})
        return {"valid": True}

    _mount_frontend(app)
    return app


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


async def restore_config(registry: CapabilityRegistry, storage: Storage) -> None:
    """把持久化的模型清单与 gwprofile 恢复到内存注册表。

    进程启动时调用一次。config 表为空（首次启动）时保持 preset 默认值。
    """
    models = await storage.load_config(CONFIG_MODELS)
    if models:
        registry.models = [model_from_payload(ModelPayload.model_validate(item)) for item in models]

    profiles = await storage.load_config(CONFIG_PROFILES)
    if profiles:
        registry.profiles = {
            payload.name: profile_from_payload(payload)
            for payload in (ProfilePayload.model_validate(item) for item in profiles)
        }


def _remove_model(registry: CapabilityRegistry, label: str) -> bool:
    before = len(registry.models)
    registry.models = [model for model in registry.models if model.label() != label]
    return len(registry.models) != before


@contextlib.asynccontextmanager
async def _upstream_client(client: httpx.AsyncClient | None) -> AsyncGenerator[httpx.AsyncClient, None]:
    """查询上游用的连接池：优先复用组合根注入的那个。

    没有注入（测试直接建应用、或把控制台当库用）时临时建一个，用完即关，
    避免把"必须由组合根先建好连接池"变成使用者的隐性前置条件。
    """
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT) as temp:
        yield temp


def _provider_api_key(registry: CapabilityRegistry, provider: str) -> str | None:
    """该供应商的密钥：先看已保存模型上的值，再看 preset 约定的环境变量。

    ``/models`` 是供应商级接口，调用时还没有"这个模型"这个概念，因此只能按
    供应商取密钥——同一供应商的各模型共用一把密钥，这与运行期的取值口径一致。
    """
    for model in registry.all_models():
        if model.provider == provider and model.api_key:
            return model.api_key
    preset = get_preset(provider)
    if preset is None:
        return None
    return os.environ.get(preset.env_key) or None


def _provider_base_url(registry: CapabilityRegistry, provider: str) -> str | None:
    """已保存模型上的 base URL：用户可能把同一供应商指到自建代理或兼容网关。"""
    for model in registry.all_models():
        if model.provider == provider and model.base_url:
            return model.base_url
    return None


def _changelog_text() -> str:
    """读取更新日志原文。

    直接回原文而不解析成结构化数据：更新日志是给人看的，多一层解析就多一处
    与实际文件不一致的机会。文件缺失（如只装了 wheel）时返回空串，界面会显示
    "暂无更新日志"，不会因此打不开页面。
    """
    try:
        return _CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return ""


async def _persist_models(registry: CapabilityRegistry, storage: Storage | None) -> None:
    if storage is None:
        return
    await storage.save_config(
        CONFIG_MODELS, [model_to_payload(model).model_dump() for model in registry.all_models()]
    )


async def _persist_profiles(registry: CapabilityRegistry, storage: Storage | None) -> None:
    if storage is None:
        return
    await storage.save_config(
        CONFIG_PROFILES,
        [profile_to_payload(profile).model_dump() for profile in registry.profiles.values()],
    )


def _model_usage(registry: CapabilityRegistry, health: list[dict]) -> list[dict]:
    """把模型定义与健康数据合成 Dashboard 表格。

    "剩余 token" = 配额 - 已消费；未配置配额时为 ``None``（前端显示为"—"）。
    """
    by_model = {row["model"]: row for row in health}
    rows: list[dict] = []
    for model in registry.all_models():
        row = by_model.get(model.id, {})
        used = registry.usage(model)
        quota = registry.quota.get(model.label())
        rows.append(
            {
                "model": model.id,
                "provider": model.provider,
                "display_provider": model.display_provider or model.provider,
                "status": row.get("status", "unknown"),
                "available": registry.is_available(model),
                "success_count": row.get("success_count", 0),
                "error_count": row.get("error_count", 0),
                "last_error": row.get("last_error"),
                "used_tokens": used,
                "quota_tokens": quota,
                "remaining_tokens": None if quota is None else max(0, quota - used),
            }
        )
    return rows


def _task_from_chat(payload: ChatRequest) -> Task:
    """ChatRequest → 统一 Task。task_id 由网关生成（Chat 页没有幂等需求）。"""
    import uuid

    body = {
        "task_id": f"chat-{uuid.uuid4().hex[:12]}",
        "input": {
            "messages": [message.model_dump(exclude_none=True) for message in payload.messages],
            "stream": payload.stream,
        },
    }
    if payload.profile:
        body["profile"] = payload.profile
    if payload.system:
        body["input"]["system"] = payload.system
    if payload.tools:
        body["input"]["tools"] = [tool.model_dump() for tool in payload.tools]
    if payload.response_schema is not None:
        body["input"]["response_schema"] = payload.response_schema
    if payload.max_tokens is not None:
        body["input"]["max_tokens"] = payload.max_tokens
    if payload.temperature is not None:
        body["input"]["temperature"] = payload.temperature
    return validate_schema(json.loads(json.dumps(body)))


def _mount_frontend(app: FastAPI) -> None:
    """若前端已构建（``webapp/dist``），把静态资源挂到根路径。

    未构建时只提供 ``/api/*``——开发期前端由 Vite dev server 提供，
    通过代理访问这里。
    """
    if not _WEBAPP_DIST.is_dir():
        return
    assets = _WEBAPP_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_WEBAPP_DIST / "index.html")