# 版本更新说明

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## 0.8.0

落实需求"模型管理层"第 1、2 条与"管理与交互层"第 6 条：**模型配置改为向供应商
实时查询**（可选模型、能力），**高级配置项逐项可开关且支持互斥**，并新增**模型连接
测试**与**页面版本号 / 更新日志**。

### 新增

- **模型发现**（`llm_gw/adapter/discovery.py`）：新增 `list_models` 实时拉取供应商
  `GET {base_url}/models`，逐项带出能力、上下文窗口与价格；上游不可用（端点不存在、
  鉴权失败、网络不通）时回退内置 preset 清单，并如实回报 `source` 与 `error`，界面
  据此提示而不是假装一切正常。上游只回模型 id，能力与价格按 preset 已知型号补齐，
  未知型号标记 `known=false` 并只给协议默认能力——比编造能力值更诚实。
- **高级配置项清单**：各协议声明自己真的认得的参数（OpenAI 的 chat.completions 不
  接受 `top_k`，Anthropic Messages 接受），避免配好了却在调用时被上游 400 拒掉。
  思考模式的"开启 / 关闭"是一组互斥选项。
- **模型连接测试**（`POST /api/models:test`）：复用 adapter 的非流式调用路径发一次
  **真实最小对话**（`ping` + `max_tokens=1`），回报成功/失败、错误码与耗时；不入库、
  不计费、不落 Trace。请求体与新增/编辑模型一致，因此**未保存**的模型也能先测再存。
- **新端点**：`GET /api/providers/{provider}/models`（1b 模型下拉 +
  1a 能力 + 1c 高级项）、`GET /api/meta`（版本号与更新日志原文）。
- **控制台**：模型名称改为下拉选择（可用供应商清单，也允许手填）；选中模型后能力、
  上下文窗口、成本自动带出；高级配置项每项一个勾选框，取消勾选则该参数不发送，
  互斥项自动取消另一个；新增「测试连接」按钮与结果横幅；侧边栏底部显示版本号，
  点开可看更新日志。

### 设计取舍

- **勾选状态与发送值分开表达**（`webapp/src/lib/model-catalog.js`）：勾选框记的是
  界面意图，提交时把未勾选项写成 `null`。`null` 的语义是"不发送该字段"，与 `0`
  完全不同——`temperature=0` 是确定性采样，当成"未配置"会静默改变模型行为。
- **回退而非报错**：模型清单一无所得会让"新增模型"整条路走不下去，因此上游查询
  失败只降级为内置清单 + 提示。
- **连接测试发真实请求而非探活**：只有真正走一遍鉴权与模型 ID，才能验证"配好了能用"；
  输出压到 1 token 把费用降到最低。上游失败属于测试结论，因此返回 200 + `ok=false`，
  只有请求体本身不合法才是 422。

### 修复

- 「模型定义」页的「协议」下拉选项值写成了 `openai` / `anthropic`，而 adapter 认识的
  协议 ID 是 `openai-completions` / `anthropic-messages`。选供应商时会用 preset 的
  正确值覆盖，因此只有手动改动该下拉才会暴露——一旦改动，模型（含「测试连接」）会以
  "未知的 API 协议"失败。已改为协议 ID，并在前端冒烟里固化。

### 变更

- `llm_gw/harness/service.py` 的 `create_app` 改为引用 `__version__`，版本号不再有两处
  硬编码。
- 版本号 `0.7.0` → `0.8.0`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`README.md`）。

## 0.7.0

为**面向后端 agent 的接口加上简单口令鉴权**（需求：Harness 层功能第 1 条"支持简单的
password"）。此前 `/v1/tasks` 与 `/v1/tasks:stream` 完全裸奔，任何能访问该端口的人都能
消耗你的模型配额。

### 新增

- **口令鉴权**（`llm_gw/harness/service.py`）：新增 `require_agent_password` 依赖，
  挂在 `/v1/tasks` 与 `/v1/tasks:stream` 上。口令取自环境变量
  `LLM_GW_AGENT_PASSWORD`，以 `Authorization: Bearer <password>` 提交；比较用
  `secrets.compare_digest`（定时安全，避免按耗时逐字节试出凭证）。
- **`AUTH_REQUIRED` 错误码**（`llm_gw/core/errors.py`）：网关入口鉴权失败返回 401 +
  该码，处置为 `fail`。与上游密钥失效的 `AUTH_INVALID`（处置 `degrade`，换模型继续）
  **刻意区分**——入口鉴权失败发生在选模型之前，换模型毫无意义，复用会让客户端误判为
  "供应商密钥问题"而去做无意义的模型切换。

### 设计取舍

- **未配置口令时不强制**：本地开发与 TDD 迭代不必先造一个口令。一旦配置则立即生效，
  因此生产环境只需设好环境变量，无需改代码或改库。
- **`/health` 豁免**：探活程序（负载均衡、k8s probe）通常不带凭证，要求凭证会让它们
  把"服务正常"误判为"服务不可用"。
- **控制台 `/api/*` 不在保护范围内**：鉴权只作用于 agent 接口。给控制台也加门会连带
  保护它的静态资源，页面会直接打不开。
- **口令只从环境变量读**，不写进 SQLite 也不进代码库——口令是部署期凭据，不是业务
  配置；混进 `llm_gw.sqlite3` 会让"把库拷走"等于"拿到口令"。

### 测试

- `tests/harness/test_agent_auth.py`（新增 7 例）：未配置放行、缺凭证 401、错口令
  401、非 Bearer 方案 401、正确口令放行、流式端点同样受保护、`/health` 豁免。
- `tests/test_runtime.py::test_runtime_guards_agent_api_but_not_console`：真实运行时
  应用上的端到端验证——401 错误码可达、带凭证后进入业务逻辑（非法 JSON 得 400）、
  `/health` 与控制台接口不受影响。

## 0.6.0

修复**agent API 未挂载进运行时组合根**的已知限制——此前 `runtime.create_runtime_app`
只把控制台挂在 `/`，导致真实进程里访问不到 `/health` 与 `/v1/tasks`，只能靠临时入口
或 `ASGITransport` 绕过。

### 修复

- **agent API 与控制台同进程共存**（`llm_gw/harness/service.py` + `llm_gw/runtime.py`）：
  把 `/health` 与 `/v1/tasks*` 从 `create_app` 内抽成 `agent_router(service)`，组合根
  **先** `include_router(agent_router(service))` **再** `mount("/", console)`。
  注册顺序是硬约束——`mount("/")` 是兜底挂载，agent 路由若排在它之后会被全部吃掉（404）。
- **回归测试**（`tests/test_runtime.py`）：新增 `test_agent_api_is_mounted_alongside_console`，
  断言 `/health` 返回 200、`POST /v1/tasks` 非法 JSON 返回 400 `REQUEST_INVALID`
  （而非被控制台吞掉的 404）、`/v1/tasks:stream` 同样可达、控制台 `/api/models` 仍 200。

### 文档

- `docs/architecture.md` 补充运行时装配顺序的硬约束说明。
- `docs/test-evidence.md` 第 6.2 节的"已知限制"标记为已修复，并新增 6.4 节记录真实
  uvicorn 进程验证（`/health` 200、`/v1/tasks` 非法 JSON → 400 `REQUEST_INVALID`）。

### 测试

- 后端 **343 例通过**（`tests/test_runtime.py` +1），覆盖率 92%。

## 0.5.0

本轮**整体重做控制台前端**：在不改动后端 `/api` 契约的前提下，把五个页面从
"纯手写 CSS + 原生表格"迁移到组件化 UI，并补齐表格排序与指标图表。

### 新增

- **UI 基础库**（`webapp/src/components/ui/`）：引入 Tailwind CSS v4（CSS-first，
  `@theme` 定义深色 design token）与一组 shadcn 风格的原语组件
  （`Button` / `Card` / `StatCard` / `Badge` / `Alert` / `Field` / `Input` /
  `Select` / `Textarea` / `Checkbox` / `Table` / `DataTable` / `PageHeader`）。
- **可排序表格**：`DataTable` 基于 TanStack Table v8，模型清单、Profile 清单、
  Dashboard 模型状态、Trace 列表四张表统一支持点表头排序。
- **Dashboard 图表**：新增「模型调用分布」（成功/失败分组柱）与「Token 消耗」
  两张 Recharts 图，指标卡改用 `StatCard`。
- **侧边栏布局**：顶部横向页签改为左侧导航 + 内容区（参考 Langfuse / LiteLLM
  Admin UI 的信息架构），五个功能入口文案不变。

### 变更

- **样式入口**（`webapp/src/styles.css`）：重写为 Tailwind v4 入口
  （`@import "tailwindcss"` + `@theme`），原 design token 保留为 CSS 变量；
  瀑布图 / 聊天气泡 / Trace 明细 / 选中行等复合样式收敛到 `@layer components`，
  类名与语义（`.waterfall-bar` / `.waterfall-ok|bad|warn` / `.row-selected`）保持不变。
- **构建**（`webapp/vite.config.js`）：挂载 `@tailwindcss/vite` 插件。
- **依赖**（`webapp/package.json`）：新增 `tailwindcss`、`@tailwindcss/vite`、
  `@tanstack/react-table`、`recharts`、`lucide-react`、`clsx`、`tailwind-merge`。
- **后端契约零改动**：`/api` 端点、请求/响应字段、SSE 事件语义均未变化；
  Chat 页仅更换外观，SSE 消费逻辑与 `api.js` 保持原样。
- 版本号 `0.4.0` → `0.5.0`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`llm_gw/harness/service.py`）。

### 测试

- 前端冒烟 **8 例全部通过且断言未修改**（`webapp/src/__tests__/smoke.test.jsx`）：
  五个页签文案、默认页含「模型清单」「高级配置」、SSE 解析 3 例、
  任务瀑布图 3 例（`group_by_task` / `bar_geometry` / `TaskWaterfall` 渲染）。
- 后端 **342 例通过**（本次为纯前端改动，用例数与覆盖率不变）。

## 0.4.0

本轮按需求文档业务与交互层第 34 行实现 Trace 页的**按任务图形化展示**：除逐条调用
列表外，新增「任务视图」，按 `run_id`（agent 的一次 task）分组做时间轴瀑布图。

### 新增

- **任务视图瀑布图**（`webapp/src/pages/TracePage.jsx`）：新增 `TaskWaterfall`
  组件与 `group_by_task` / `bar_geometry` 纯函数。
  - 按 `run_id` 分组，`run_id` 为空的记录归入「未标记任务」；组内按时间正序。
  - 每根横条宽度**正比于 `total_ms`**（以本次结果里最长的调用为基准，跨任务可横向比较），
    条内浅色段标出 **TTFT** 分界，颜色按终态区分（done / error / cancelled）。
  - 组头给出任务级汇总：调用数 / 合计耗时 / 错误数 / 合计成本。
  - 点击横条复用既有链路明细（8 维度结构化展示）。
- **视图切换**：Trace 页搜索行新增「列表 / 任务视图」切换，默认仍为列表。
- **样式**（`webapp/src/styles.css`）：新增 `.waterfall` / `.task-group` /
  `.waterfall-row` 等类，沿用既有 design token。

### 变更

- 版本号 `0.3.0` → `0.4.0`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`llm_gw/harness/service.py`）。

## 0.3.0

本轮按需求文档 Harness 层第 91-104 行实现**错误处置三选一**：网关根据收集到的错误
情况，在「**重试 / 降级 / 报错**」中选择一个执行，并把决策与执行事实妥善记录。

### 新增

- **错误处置决策表**（`llm_gw/core/errors.py`）：新增 `ErrorDisposition`
  （`retry` / `degrade` / `fail`）与 `ErrorDecision`（`disposition` + `strategy`），
  落成 `ERROR_DECISIONS: dict[ErrorCode, ErrorDecision]`——需求「处理策略」列的
  逐行对应。**数据与错误码放在一起**，避免错误码与处置分类两处漂移。
  - `AUTO_RETRYABLE_CODES` 改为**派生**自决策表（`disposition is RETRY`），
    结果集与旧值一致：`{CONN_FAILED, RATE_LIMITED, UPSTREAM_OVERLOADED}`。
- **判定函数**（`llm_gw/harness/decisions.py`）：`decision_for` / `disposition_for` /
  `should_auto_retry` / `should_degrade` / `describe_decision`。
- **`ExecutionTrace`**（`llm_gw/router/router.py`）：执行事实出参，承载
  `attempts` / `retries` / `fallback` / `disposition` / `degraded_from` /
  `degraded_to` / `warnings`。`AssistantMessage` 是 adapter 共享传输模型、没有
  承载位，故经可选出参回传。
- **非阻塞告警**：`POST /v1/tasks` 响应新增 `warnings` 数组。认证失败降级时写入
  `"AUTH_INVALID: 已降级 A → B；…"`，请求本身正常返回。
- **`resilience.disposition`**：`ResilienceInfo` 新增字段并展开到
  `CallRecord.to_dict()`，随 `requests.payload` 落库，Trace 页「弹性」分组可查。

### 变更

- **重试谓词可注入**（`llm_gw/harness/retry.py`）：`retry_assistant_call` 新增
  `is_retryable` 参数。原先由 `retry_assistant_call` 内部正则判断可重试、与
  `RETRY_DECISION` 各判一次，两者可能漂移；现在路由层注入
  `should_auto_retry(_code_from(response))`，**决策表成为唯一真源**。
- **`Router.execute()` 按处置遍历候选链**：`retry` 先在同一模型内有限重试、
  耗尽后换模型；`degrade` 直接换下一个；`fail` 立即返回。
- **错误消息统一带错误码前缀**：`Router._attempt` 的异常分支由
  `_error_message(str(exc))` 改为 `f"{classify_error_code(exc).value}: {exc}"`。
  此前 trace 里的 `error_code` 常因缺前缀而归为 `UNKNOWN`。
- **`resilience` 真实落库**：`GatewayService.complete()` 此前既不传 `on_fallback`
  也不填 `resilience`，`requests` 表的 `attempt` / `retry` / `fallback` 恒为
  `1 / 0 / 0`；现经 `ExecutionTrace` 回传并如实写入。**无需 DDL 变更**
  （`disposition` 走 `payload` JSON）。
- 版本号提升至 `0.3.0`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、FastAPI `version`）。

### 破坏性变更

- **认证失败不再快速失败**：`AUTH_INVALID` 由 `fail` 改为 `degrade`——若路由表中
  还有下一个模型就换模型继续并附告警，只有没有下一个模型时才返回错误。
- **内容拒答改为换模型重试**：`CONTENT_REFUSED` 由"不换模型绕过"改为
  `degrade`——更换模型重试，**每个模型最多尝试一次**（候选链主备各一次，
  天然满足）。这是安全语义的反转，请确认符合预期。
- **移除 `RetryAction` 与 `RETRY_DECISION`**：由 `ErrorDisposition` /
  `ErrorDecision` / `ERROR_DECISIONS` 取代。
- **移除 `should_fallback`**：由 `should_degrade` 取代（语义从"是否降级"
  收窄为"是否**直接**降级、不重试当前模型"）。
- **`Router.execute()` 签名变更**：`on_fallback` 回调改为 `trace` 出参，
  调用方（`GatewayService`）需相应调整。

### 迁移说明

- **调用方**：若依赖 `RetryAction` / `RETRY_DECISION` / `should_fallback`，
  改用 `ErrorDisposition` / `ERROR_DECISIONS` / `should_degrade`；
  `Router.execute()` 的 `on_fallback` 改为传入 `ExecutionTrace` 实例。
- **旧数据库**：无需迁移，`requests` 表结构未变。

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
  超过上限即拒绝（处置为 `fail`），在调用上游之前生效。
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
