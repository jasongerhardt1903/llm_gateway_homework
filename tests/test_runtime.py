"""组合根测试。

`runtime.py` 是唯一知道"运行时这些依赖各是什么"的模块，也是唯一没有任何
单元测试覆盖的模块。这里验证两件在真实起服时才会暴露、静态分析看不出来的事：

1. **生命周期真的被挂上去了**：启动时建表并恢复配置，关闭时释放资源。
   忘记挂 lifespan 的话，`storage.init()` 永不执行，所有 `/api/*` 会在
   第一次查询时抛"Storage 尚未初始化"。
2. **配置跨进程重启不丢**：模型清单与路由规则写进 SQLite，第二次构造
   （模拟重启）时被 `restore_config` 读回内存注册表。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.runtime import create_runtime_app, default_db_path


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def test_default_db_path_honours_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_GW_DB", "/tmp/custom-gw.sqlite3")
    assert default_db_path() == "/tmp/custom-gw.sqlite3"

    monkeypatch.delenv("LLM_GW_DB")
    assert default_db_path().endswith("llm_gw.sqlite3")


async def test_lifespan_initialises_storage_and_serves_console(tmp_path) -> None:
    app = create_runtime_app(db_path=str(tmp_path / "gw.sqlite3"))

    # 未进入 lifespan 前没有建表，说明初始化确实挂在生命周期上而非 import 副作用。
    async with app.router.lifespan_context(app):
        response = await _get(app, "/api/models")
        assert response.status_code == 200
        # preset 模型清单在启动时被装载进注册表。
        assert {model["id"] for model in response.json()} >= {"gpt-4o-mini", "deepseek-chat"}


async def test_config_survives_restart(tmp_path) -> None:
    db_path = str(tmp_path / "gw.sqlite3")
    profile_config = {
        "name": "default",
        "display_name": "默认",
        "models": [{"label": "openai/gpt-4o-mini", "prefer_own_config": False}],
        "template_enabled": False,
        "template": {},
        "route_mode": "static",
        "static_order": ["openai/gpt-4o-mini"],
        "retry_enabled": True,
        "max_retries": 7,
    }

    first = create_runtime_app(db_path=db_path)
    async with first.router.lifespan_context(first):
        transport = httpx.ASGITransport(app=first)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/profiles", json=profile_config)
            assert created.status_code == 200

    # 第二次构造 = 重启：内存注册表是全新的，配置只能来自 SQLite。
    second = create_runtime_app(db_path=db_path)
    async with second.router.lifespan_context(second):
        profiles = await _get(second, "/api/profiles")
        assert profiles.status_code == 200
        body = profiles.json()
        assert [item["name"] for item in body] == ["default"]
        assert body[0]["max_retries"] == 7


async def test_unknown_api_path_is_not_swallowed_by_root_mount(tmp_path) -> None:
    """根路径挂载控制台应用后，未命中的 API 仍应返回 404 而不是前端页面。"""
    app = create_runtime_app(db_path=str(tmp_path / "gw.sqlite3"))
    async with app.router.lifespan_context(app):
        response = await _get(app, "/api/no-such-endpoint")
        assert response.status_code == 404


@pytest.mark.parametrize("provider", ["openai", "deepseek", "anthropic"])
async def test_preset_providers_are_available(provider: str, tmp_path) -> None:
    """三种供应商的 preset 都应出现在下拉菜单数据源里。"""
    app = create_runtime_app(db_path=str(tmp_path / "gw.sqlite3"))
    async with app.router.lifespan_context(app):
        providers = (await _get(app, "/api/providers")).json()
    assert provider in {item["provider"] for item in providers}
