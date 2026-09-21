# 版本更新说明

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## 0.8.4

按更新后的 `需求文档.md` 落五条新需求：流式降级、口令网页可配、通讯原始日志、
控制台不再有 Chat 专属契约、路由表拖拉拽。

### 变更

- **R2 流式路径也降级**（`llm_gw/harness/service.py`、`llm_gw/router/router.py`）。
  `Router.stream` 增加 `model=` 参数，允许 Harness 指定候选；`stream_sse` 的候选链是
  `[主路由] + [备用路由]`，**换模型只发生在首个业务 delta 之前**——那时客户端一个业务
  字节都没收到，重开一条流不会造成重复内容。一旦吐过业务 delta 就不再换模型（需求：
  "已流式输出 → 不盲目重新生成"）。对外仍只有一个终态：被放弃的那条流的 `error` 事件
  不转发。可降级的处置取 `DEGRADE` 与 `RETRY`（决策表对"首 Token 前"写的是"有限重试
  **或** fall back"，流式不做同模型重试），`FAIL` 一律不换。落库的模型改为实际服务的
  那个（`_record(..., model=)`），否则"降级到了哪个模型"会被记成没降级。
- **R5 agent 口令可网页配置**（`llm_gw/harness/service.py`、`llm_gw/web/app.py`、
  `llm_gw/runtime.py`）。新增 `GET /api/settings` 与 `PUT /api/settings/agent-password`，
  口令落 `config` 表的 `agent_password` 键、启动时由 `load_agent_password()` 恢复。
  **环境变量 `LLM_GW_AGENT_PASSWORD` 仍然优先**：口令是部署期凭据，容器化部署不必把它
  写进 `llm_gw.sqlite3`。`/api/settings` 只回来源（`env`/`console`/`none`）与 `env_key`，
  **不回显口令**。`require_agent_password` 由无参依赖改为工厂（闭包持有 service）。
- **R4 通讯原始日志**（`llm_gw/harness/storage.py`、`llm_gw/harness/service.py`、
  `llm_gw/web/app.py`）。新增 `exchanges` 表与 `save_exchange` / `recent_exchanges` /
  `exchanges_for_task` / `search_exchanges` / `exchange`，对外新增
  `GET /api/exchanges?q=&task_id=&limit=` 与 `GET /api/exchanges/{exchange_id}`。
  与 `requests` 刻意分开：前者是**通讯层**事实（agent 发来什么字节、网关回什么字节），
  后者是**调用层**事实（落到哪个模型、花了多少钱）；一次通讯可能对应 0 次模型调用
  （两层校验失败时一次都没有，也照样记——agent 最常踩的就是 schema 错误，这类记录
  连 `task_id` 都只能从原文里尽力抠）。按 `(task_id, flow_index)` 两级组织，`flow_index`
  按 task 自增。两侧报文**先脱敏再落盘**（agent 可能在 metadata 里夹带自己的凭据）。
  流式的 `response_raw` 是实际发出的 SSE 帧原文，被放弃的那条流的帧不进记录；客户端
  断开时也会补一条 `cancelled` 记录——最需要排查的中断不该反而没有痕迹。状态列记的是
  **通讯的真实结局**而非 HTTP 码：非流式调用模型失败时 HTTP 仍是 200（状态码只表达
  "请求本身合法"），日志里落的是 `error` + 具体错误码，否则页面上一片绿色而正文全是错误。
- **R1 控制台不再有 Chat 专属契约**（`llm_gw/web/app.py`、`llm_gw/web/api_models.py`、
  `webapp/src/api.js`、`webapp/src/pages/ChatPage.jsx`）。删除
  `POST /api/chat`、`POST /api/chat/stream`、`ChatRequest` 与 `_task_from_chat`：Chat 页
  作为"一个简单的后端 agent Loop"直接按 agent 的 `Task` schema 调 `/v1/tasks:stream`
  （需求管理与交互层功能第 7 条），控制台因此用的是与真实 agent **同一份**契约，
  代理层带来的口径漂移随之消失。`vite.config.js` 补上 `/v1` 代理。
- **R3 路由表拖拉拽**（`webapp/src/pages/ProfilesPage.jsx`）。「静态顺序」由逗号分隔的
  文本框换成拖拉拽编辑器：左侧是 profile 已选模型，拖到右侧组成路由链，右侧内部可
  上下拖拽排序（另有上移/下移按钮作为键盘可达的等价操作），**执行自上而下**；profile 名
  即路由表名。用原生 HTML5 Drag & Drop，未引入新依赖。`static_order` 仍只在
  `route_mode == "static"` 时提交。
- 前端新增「设置」页（`webapp/src/pages/SettingsPage.jsx`）承载 R5；`TracePage.jsx`
  新增「通讯日志」视图承载 R4 的双模式（raw data / 渲染后易读）与按字段搜索。
- 版本号 `0.8.3` → `0.8.4`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`webapp/src/styles.css`、`README.md`、
  `llm_gw/web/app.py`、`llm_gw/harness/service.py`）。
- 文档同步：`docs/interface.md` 第 2、4 节重写鉴权与通讯日志说明、删除 `/api/chat*`；
  `README.md` 控制台用法改为六个入口。

### 破坏性变更

- `POST /api/chat` 与 `POST /api/chat/stream` **已删除**（404）。控制台自身已改为直连
  `/v1/tasks:stream`；若有外部脚本依赖这两个路径，请改调 agent 接口。
- `llm_gw.harness.service.agent_password` 模块级函数已删除，改为
  `agent_password_from_env()`（另导出 `CONFIG_AGENT_PASSWORD`）。
- `Router.stream(task)` 签名变为 `Router.stream(task, *, model=None)`；不传 `model`
  时行为与之前一致（走 `decision.primary`）。
- `ProfilePayload.static_order` 一直是数组，但 `docs/interface.md` 此前把它写成了逗号
  分隔字符串，本次一并订正。

### 测试

- 后端 **389 passed**，覆盖率 **93%**（`service.py` 97%、`storage.py` 95%、`app.py` 91%）。
- 前端 **24 passed**（vitest），`npm run build` 成功。

## 0.8.3

**本次没有代码改动**，是一次版本标记：0.8.2 的 preset 换型号之后，用户库里遗留的
`config` 表 `models` 行会整体覆盖 preset（见 `docs/test-evidence.md` 第 6.6 节），
清掉该行并重启后 preset 才真正生效。因此升一个补丁号，让"跑着的这个进程是否已加载
新清单"能直接从 `/api/meta` 的版本号上看出来——否则 0.8.2 的进程与清库前的 0.8.2
进程在界面上无法区分。

### 变更

- 版本号 `0.8.2` → `0.8.3`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`webapp/src/styles.css`、`README.md`、
  `llm_gw/web/app.py`、`llm_gw/harness/service.py`）。
- 测试用例、覆盖率、接口契约均无变化（后端 372 passed / 92%，前端 15 passed）。

### 说明

运行期配置（`llm_gw.sqlite3`）不属于交付物：`t1` profile 里两条悬空的模型引用
（`deepseek/Deepseek-flash-real`、`deepseek/Deepseek-flash`）已通过
`PUT /api/profiles/t1` 清理，只保留可解析的 `deepseek/deepseek-flash`。

## 0.8.2

把 DeepSeek preset 的型号清单换成官方文档当前的型号。0.8.0 起的清单里写的
`deepseek-chat` / `deepseek-reasoner` 已经下线，下拉菜单与能力表因此一直指着一组
调用不通的名字；上游 `/models` 拉不到时（本机密钥失效就是这种情况）回退到的
正是这份过时清单，问题会被放大。

清单与价格以官方文档为准（`https://api-docs.deepseek.com` 的
"Your First API Call" 与 "Models & Pricing"）。

### 变更

- **型号换成 `deepseek-flash` 与 `deepseek-v4-pro`**（`llm_gw/adapter/presets/deepseek.py`）：
  两者共用上下文 1M、最大输出 384K；官方型号版本分别是 DeepSeek-V4.1-Flash 与
  DeepSeek-V4-Pro-0813。
- **能力差异如实声明**：`deepseek-v4-pro` 不支持视觉，`deepseek-flash` 支持；
  两者都支持工具调用与思考模式。旧的 `deepseek-reasoner` 曾以"不支持 function
  calling"作为能力表必须如实反映供应商限制的样例，该样例改由视觉能力承担。
- 版本号 `0.8.1` → `0.8.2`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`README.md`）。

### 设计取舍

- **`CostRates` 取峰值价**：官方计费分峰值 / 非峰值两档（非峰值为峰值的一半，
  峰值为 UTC 周一至周五 01:00-04:00 与 06:00-10:00），而 `CostRates` 只能存一个
  单价。取峰值价是**有意让成本估算偏高**——预算口径上低估比高估危险得多。
- **`json_schema` 仍为 `False`**：官方 JSON Output 依旧只提供
  `response_format={"type": "json_object"}`，没有严格 JSON Schema，请求翻译继续
  走降级路径（`openai_compat.py` 据 `capabilities.json_schema` 自动切换）。
- **不再列出历史名称**：`deepseek-v4-flash` 与 `deepseek-v4-flash-vision-exp`
  官方仍接受，但实际由 DeepSeek-V4.1-Flash 提供服务并按 Flash 价计费，不是独立
  型号，列出来只会让人以为存在第三个模型。
- **`base_url` 保持 `https://api.deepseek.com/v1`**：文档现在只写不带 `/v1` 的
  地址，但实测两者都返回 401（路径都存在）、功能一致，因此按"只改动必须改动的
  代码"不动它。
- **不迁移已存数据**：用户此前保存的 `deepseek-chat` 模型仍在库里，`find_model`
  找不到它时会按未知型号处理（界面标 `known=false` 并提示人工确认），不做静默改写。

## 0.8.1

给 0.8.0 的模型清单查询加一层**进程内短时缓存**。0.8.0 每次进「新增模型」
都会实打实地查一次上游，本机密钥失效时还要白等一个 401 往返——而"这家供应商
有哪些模型"几分钟内根本不会变。

### 新增

- **`ModelCatalogCache`**（`llm_gw/adapter/discovery.py`）：按
  `(provider, api, base_url)` 缓存 `list_models` 的结果，成功缓存
  `CACHE_TTL_OK`（300 秒）、失败缓存 `CACHE_TTL_ERROR`（30 秒）。
  缓存由 `create_web_app` 按应用实例持有，因此不跨进程共享、也不在测试间串味。

### 设计取舍

- **失败也缓存，但只缓存 30 秒**：不缓存失败等于默认"密钥随时会修好"，
  页面来回切换就会反复打上游；缓存太久又会让"刚换上有效密钥"白等 5 分钟。
  两个 TTL 是这中间的折中。
- **`base_url` 进缓存键**：用户可能把同一供应商指到自建代理，换了地址必须
  重新查，否则拿到的还是官方地址的清单。
- **返回副本**：缓存里那一份不能被调用方改坏。
- **时钟可注入**：测试用 `FakeClock` 直接推进时间验证过期，不产生真实等待
  （与重试测试同一套约定）。
- 连接测试（`/api/models:test`）**不吃这份缓存**：它验证的就是"此刻能不能通"。

### 变更

- 版本号 `0.8.0` → `0.8.1`（`llm_gw/__init__.py`、`pyproject.toml`、
  `webapp/package.json`、`README.md`）。

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
