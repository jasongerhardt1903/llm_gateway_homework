# 版本更新说明

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## 0.2.0

本轮按需求文档更新实现：新增 **gwprofile 层**、模型**高级配置项**与**密钥管理**，
引入 git 版本控制，并补齐 README 与 CHANGELOG。

### 新增

- **gwprofile 层**（`llm_gw/router/profile.py`）：新增 `GwProfile` / `ProfileModelRef`。
  一个 profile 声明包含哪些模型，并提供：
  - profile 内生效的**统一高级配置模版**（`template_enabled` + `template`）；
  - 每个模型可勾选 **"本模型配置优先于模版"**（`prefer_own_config`）；
  - **路由模式**：`dynamic`（动态）或 `static`（静态），静态用逗号分隔写死优先顺序
    （`static_order`，支持全角/半角逗号与顿号，保序去重）；
  - **per-profile 重试策略**（`retry_enabled` / `max_retries`）。
- **模型高级配置项**（`llm_gw/core/advanced.py`）：`AdvancedConfig` 承载
  `temperature` / `top_p` / `top_k` / `thinking_mode` / `max_tool_rounds` / `max_tokens`。
  全部字段默认 `None`，语义是"请求时不发送该字段"。
  - `thinking_mode` 三态：`default`（跟随模型默认）/ `on`（开启）/ `off`（关闭）。
    `on` 时 openai 协议发 `{"thinking": {"type": "enabled"}}`，anthropic 协议额外带
    `budget_tokens`。
- **模型 Tag 与展示名**：`Model.tag`，控制台模型清单可显示与检索。
- **API key 可在 Web 界面配置**（`Model.api_key`）：
  - 密钥写入 SQLite，**只写不回显**——`model_to_payload` 一律把 `api_key` 置为 `None`，
    只回显 `api_key_set: bool`；
  - `api_key=None` 表示"不修改"，显式空串表示"清除"；
  - 取值优先级：模型密钥 > 供应商 preset 约定的环境变量；
  - 脱敏覆盖 `api_key` 键名（`redact_mapping`），密钥不进入 Trace / Logs。
- **`TOOL_ROUNDS_EXCEEDED` 错误码**：`max_tool_rounds` 作为请求校验护栏，
  超过上限即拒绝（`RetryAction.NEVER`），在调用上游之前生效。
- **控制台 Profile 页**：模型多选 + 模版 + 路由模式 + 静态顺序 + 重试配置。
- **`.gitignore`**：排除 `.venv/`、`__pycache__/`、`node_modules/`、`webapp/dist/`、
  `llm_gw.sqlite3`、`.env`、`.pytest_cache/`、`.coverage`、`.trae/`。
- **README.md / CHANGELOG.md**：运行说明与版本更新说明。

### 变更

- **路由入口改为 profile 作用域**：task 指定 profile 时，候选只来自该 profile 内的模型；
  未指定时走 `default` profile；一个 profile 都没配时退回全局模型池（开箱即用）。
  显式指定**不存在**的 profile 属于配置错误，**快速失败**，不静默退化为全局池。
- **静态路由语义**：`is_pinned()` 为真（`route_mode == "static"` 且顺序非空）时，
  动态打分不得改写顺序；显式顺序中的标签全部无效时退化为动态选择。
- **请求体优先级**：高级配置（模型自身 / 模版）< task 显式指定 < `extra_body`。
- 版本号提升至 `0.2.0`（`llm_gw/__init__.py`、`pyproject.toml`、`webapp/package.json`），
  FastAPI 的 `version` 改为从 `__version__` 读取。

### 破坏性变更

- **字段重命名 `logical_model` → `profile`**：涉及 `Task`、`Decision`、`CallRecord`、
  `requests` 表列、Web payload 与前端。调用方（agent）需把 task 中的 `logical_model`
  改为 `profile`。
- **移除 `/api/routes`（GET / PUT）**：由 `/api/profiles` 取代。
- **`requests` 表列改名**：`logical_model` → `profile`。启动时自动迁移
  （`PRAGMA table_info` 探测 + `ALTER TABLE ... RENAME COLUMN`），旧库无需手工处理。
- 移除 `CapabilityRegistry.static_routes` / `set_static_route` / `static_candidates`，
  静态顺序改由 profile 承载。

### 迁移说明

- **旧数据库**：直接用 v0.2.0 启动 v0.1.0 生成的 `llm_gw.sqlite3` 即可，启动时自动迁移列名。
- **调用方**：把请求体 / task 里的 `logical_model` 字段改名为 `profile`；
  若原本依赖 `/api/routes`，改用 `/api/profiles`。

## 0.1.0

初始实现。四层架构（core / adapter / router / harness + web 与前端控制台）：

- **core**：消息与能力模型、task schema 校验、稳定错误码与重试决策表、遥测记录。
- **adapter**：openai / anthropic 两种协议，SSE 流式解析、错误归一化与脱敏。
- **router**：能力注册表、动态路由（消费比 → 单价）、主备降级、模型健康与配额。
- **harness**：对外 `/v1/*` HTTP + SSE，两层校验（语法 400 / schema 422）、
  单一终态（正常发 `[DONE]`，错误/取消不发）、每次调用落 `requests` 表。
- **web / webapp**：模型定义、Chat、Dashboard、Trace 四个页签的控制台。
- 启动入口 `llm_gw.runtime:create_runtime_app`（`--factory`），`./run.sh` 启动脚本。
