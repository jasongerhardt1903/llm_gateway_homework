"""组合根：把 core / adapter / router / harness / web 装配成一个可运行的进程。

前面各层都只依赖注入的接口（Router 依赖 ``adapter_for``、GatewayService 依赖
``Storage``），因此需要一个地方回答"真实运行时这些依赖各自是什么"。
这个文件就是那个地方——它是唯一知道"全貌"的模块。

启动方式::

    uvicorn llm_gw.runtime:create_runtime_app --factory

刻意不做成模块级 ``app = create_runtime_app()``：那会让"导入这个模块"带上
构造 httpx 连接池的副作用，测试与工具脚本一 import 就中招。

密钥优先取 Web 界面配置在模型上的值，未配置时回退到环境变量（preset 的
``env_key``）。密钥写入数据库但**不回显**到控制台——``model_to_payload`` 一律
把它置为 ``None``，只回显"是否已配置"。

同理，agent 接口口令既可在网页上配置（落 config 表），也可走环境变量
``LLM_GW_AGENT_PASSWORD``；**环境变量优先**，容器化部署因此不必把口令写进
``llm_gw.sqlite3``。

版本：0.2.1
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import httpx
from fastapi import FastAPI

from . import __version__
from .adapter.base import AdapterOptions
from .adapter.factory import create_adapter
from .adapter.presets.registry import all_models, get_preset
from .core.messages import Model
from .harness.ratelimit import ModelRateLimiter, policy_from_env
from .harness.retry import RetryPolicy
from .harness.service import GatewayService, agent_router
from .harness.storage import Storage
from .router.registry import CapabilityRegistry
from .router.router import Router
from .web.app import create_web_app, restore_config

__all__ = ["create_runtime_app", "default_db_path", "DEFAULT_DB_FILE"]

#: 默认 SQLite 文件名（可用环境变量 ``LLM_GW_DB`` 覆盖）。
DEFAULT_DB_FILE = "llm_gw.sqlite3"

#: 上游调用超时。流式响应总时长可能很长，因此只约束连接与两次数据之间的间隔。
_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


def default_db_path() -> str:
    return os.environ.get("LLM_GW_DB", str(Path.cwd() / DEFAULT_DB_FILE))


def create_runtime_app(*, db_path: str | None = None) -> FastAPI:
    """构造完整的控制台应用（含启动/关闭生命周期）。

    启动时把持久化的模型清单与路由配置恢复到内存注册表，关闭时释放
    httpx 连接池与数据库连接——否则热重载会不断泄漏文件描述符。
    """
    client = httpx.AsyncClient(timeout=_TIMEOUT)
    registry = CapabilityRegistry(models=all_models())
    storage = Storage(db_path or default_db_path())

    router = Router(
        registry,
        adapter_for=lambda model: create_adapter(model.api, client),
        options_for=lambda model, advanced: AdapterOptions(
            api_key=_api_key_for(model), advanced=advanced
        ),
        retry_policy=RetryPolicy(),
    )
    service = GatewayService(
        router, storage, limiter=ModelRateLimiter(lambda _label: policy_from_env())
    )
    # 控制台也要查上游（供应商模型清单、模型连接测试），因此把同一个连接池与
    # 同一套密钥解析策略注入进去，避免控制台另建一份、两处口径漂移。
    console = create_web_app(
        service, registry=registry, storage=storage, client=client, api_key_for=_api_key_for
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await storage.init()
        # 恢复上次的模型清单与 gwprofile；config 表为空时保持 preset 默认值。
        await restore_config(registry, storage)
        # 恢复控制台配置的 agent 口令（需求 Harness 层功能第 1 条）。环境变量仍然优先，
        # 由 ``service.effective_password()`` 裁决。
        await service.load_agent_password()
        try:
            yield
        finally:
            # 不释放的话，热重载会不断泄漏连接池与文件描述符。
            with contextlib.suppress(Exception):
                await client.aclose()
            await storage.close()

    # 外层负责生命周期，并同时提供 agent API 与控制台两套路由。
    app = FastAPI(title="LLM Gateway", version=__version__, lifespan=lifespan)
    # agent API 必须**先**注册：``mount("/")`` 是兜底挂载，注册在它之后的路径会
    # 全部被控制台应用吃掉——/health 与 /v1/tasks 之前就是这样在真实进程里 404 的。
    app.include_router(agent_router(service))
    app.mount("/", console)
    return app


def _api_key_for(model: Model) -> str | None:
    """取模型密钥：Web 界面配置的优先，其次回退到供应商 preset 约定的环境变量。

    需求第 30 行允许"API key 可以在 Web 界面配置"，同时保留环境变量这条路径，
    便于容器化部署时不必把密钥写进数据库。
    """
    if model.api_key:
        return model.api_key
    preset = get_preset(model.provider)
    if preset is None:
        return None
    return os.environ.get(preset.env_key) or None
