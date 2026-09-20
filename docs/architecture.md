# 架构

## 五层结构

需求给出的链路是 `LLM —— adapter —— 路由层 —— Harness层 —— service`。本项目按这个链路分层，**依赖只允许从右向左**；需求第 31 行要求的 **gwprofile 层**是路由层的作用域来源，与路由层同级（路由层依赖它，它不反向依赖路由层）：

```
后端 agent
    │  POST /v1/tasks  ·  POST /v1/tasks:stream (SSE)
    ▼
Harness 层        harness/service.py  harness/storage.py  harness/query.py  harness/sse.py
    │             对外 HTTP 契约、单一终态编码、调用记录落库、Metrics/Trace 查询
    ▼
路由层            router/rules.py  router/router.py
    │             解析 profile → 静态优先 → 动态筛选 → 主备选择 → 重试与降级执行
    │
    ├── gwprofile 层   router/profile.py  router/registry.py
    │                模型编组、profile 内统一高级配置模版、dynamic/static 路由配置
    ▼
adapter 层        adapter/base.py  adapter/protocols/*  adapter/presets/*  adapter/structured.py
    │             统一 task ⇄ 供应商协议互译、流式装配、结构化输出校验
    ▼
LLM 供应商        OpenAI · DeepSeek · Anthropic
```

反向依赖被刻意切断：

| 层 | 依赖 | 不依赖 |
|---|---|---|
| `core/` | 无（只有 pydantic / 标准库） | 任何上层 |
| `adapter/` | `core/`、`harness/sse.py` | 路由层、Harness 服务 |
| `router/profile.py` | `core/`（含 `core/advanced.py`） | I/O、事件流、Web 层 |
| `router/` | `core/`、`adapter/base.py`（只依赖 `Adapter` 抽象）、`harness/retry.py` | 具体协议实现、FastAPI |
| `harness/` | 全部下层 | 具体 Web 框架之外的东西 |

`AdvancedConfig` 放在 `core/advanced.py` 而不是 `router/`：`Model` 需要持有它，而 core 不得反向依赖 router。`adapter/factory.py` 是唯一知道"协议标识 → 实现类"映射的地方；`runtime.py` 是唯一知道"运行时这些依赖各是什么"的地方。两者都是**组合根**，其余模块只依赖注入进来的接口。

## 一次请求的数据流

以流式请求为例（`POST /v1/tasks:stream`）：

1. **Harness 层**：`validate_syntax(raw)` 校验语法（非 JSON → 400），`validate_schema(data)` 校验结构（字段缺失 → 422），得到统一 `Task`。
2. **路由层**：`resolve_profile(task)` 取 `task.profile`，未指定时取 `default`；显式指定了不存在的 profile 属配置错误，`Decision` 直接给出拒绝原因（**不退化**为全局模型池）。`apply_static(profile)` 按 profile 的 `order()` 取候选（静态模式顺序即主备顺序）→ `dynamic_select` 按能力过滤（能力不匹配 / 不可用记入 `rejected`）→ 取前两名为 `primary` / `backup`。产出 `Decision`。
3. **adapter 层**：`resolve_advanced(primary, profile)` 先算出该模型生效的高级配置（模版判定矩阵，见下），`adapter.stream(model, task, opts)` 把它翻译进供应商请求体（`build_request`），发出 SSE 请求，再用 `StreamAssembler` 把供应商 delta 装配成**统一事件序列**（`start` → `text_start` → `text_delta`* → `text_end` → `usage` → `done`）。
4. **Harness 层**：`encode_event()` 把统一事件编码成对外 SSE 帧；`done` 额外发 `data: [DONE]`，`error` / `cancelled` **不发**。
5. **落库**：`GatewayService._record()` 构造 `CallRecord`（含生效的 `profile` 名）写入 SQLite 的 `requests` + `cost_ledger` + `model_health`。

## 关键设计决策

### gwprofile 是路由的作用域

需求第 31 行的 gwprofile 定义"包含哪些模型"。本项目把它实现为**路由作用域**：agent 在 task 里指定 profile，路由只在该 profile 声明的模型里选。

| task 的 profile | 行为 |
|---|---|
| 指定且存在 | 候选只来自 profile 内模型 |
| 指定但不存在 | **快速失败**，给出拒绝原因；不退化为全局池（静默扩大候选集会让配置错误变成线上事故） |
| 未指定 | 走 `default` profile |
| 一个 profile 都没配 | 退回全局模型池，保证开箱即用 |

### profile 内高级配置的模版判定矩阵

`resolve_advanced(model, profile)` 的四种组合：

| 模版启用 | 勾选"本模型配置优先于模版" | 生效配置 |
|---|---|---|
| 否 | — | 模型自身配置 |
| 是 | 否 | **模版** |
| 是 | 是 | 模型自身配置 |

模版只作用于**该 profile 内**的模型。`AdvancedConfig` 字段默认 `None` 表示"不发送该参数"，因此"模版留空某字段"会真的把该字段从请求体里去掉，而不是传 0。

请求体优先级：高级配置（模型 / 模版）< task 显式指定 < `extra_body`（调用方最清楚自己要什么）。

### 统一事件序列 + 单一终态

所有供应商差异在 adapter 层被抹平成同一套事件（`core/events.py`）。`EventStream.push()` 强制：一个流只能以 `done` / `error` / `cancelled` 三者之一结束，**第二个终态抛 `TerminalStateViolation`** 而不是被静默忽略——静默会掩盖上游状态机 bug。

TTFT 口径：只认**有业务意义的 delta**（`text_delta` / `thinking_delta` / `toolcall_delta`），`start` 只是连接建立信号，把它当首 token 会系统性低估 TTFT。

### 两层校验

| 层 | 函数 | 失败异常 | HTTP |
|---|---|---|---|
| 语法 | `validate_syntax` | `SyntaxViolation` | 400 |
| Schema | `validate_schema` | `SchemaViolation` | 422 |

两层分开的理由：语法错误说明请求根本不是 JSON；schema 错误可以指出**字段路径**（`input.messages.0.role: ...`），对 agent 自我修正更有用。

### 工具调用轮数护栏

`AdvancedConfig.max_tool_rounds` 是**请求校验护栏**：`Router._attempt()` 在调用上游之前用 `Task.tool_rounds()` 统计工具调用轮数，超限即返回 `TOOL_ROUNDS_EXCEEDED`（处置为 `fail`）。放在上游调用之前，避免为注定被拒的请求付费。

### 结构化输出双层保证

1. **请求侧**：带 `response_schema` 时翻译成供应商的 JSON Schema 参数（OpenAI `response_format.json_schema`、Anthropic tool-use 形态）。
2. **响应侧**：本地用 pydantic 再校验一次——供应商的 schema 保证不等于应用层不会收到坏数据。
3. **流式边界**：delta 阶段用 `parse_streaming_json` 做增量解析（不能等全部 chunk 到齐），但部分 JSON 无法做 Schema 校验，因此**终校验只在流结束后做一次**。
4. **修复有界**：`validate_with_repair(..., max_attempts=2)`，超限抛 `OUTPUT_SCHEMA_INVALID`，绝不静默吞掉。

### 路由：profile 定作用域，静态优先于动态

`resolve_profile` → `apply_static` → `dynamic_select` → `build_primary_backup`。

- `profile.is_pinned()`（`route_mode == "static"` 且 `static_order` 非空）为真时 `pinned=True`，**动态打分不得改写顺序**——那是运维显式指定的主备顺序。顺序中的标签若全部无效（模型已被删），退化为动态选择而不是报错。
- 未固定顺序时按 `(消费比, 输入单价, 标签)` 排序，消费比低者优先。
- `Decision.rejected` 逐条记录被拒模型与原因。路由"为什么选它"和"为什么不选它"同样重要。

### 错误处置：重试 / 降级 / 报错 三选一

需求 Harness 层第 91 行要求网关"根据收集到的错误情况，在重试、降级、报错三者中选一个执行，并妥善记录"。落地方式：

- 每个错误码在 `core/errors.py` 的 `ERROR_DECISIONS` 里绑定一个 `ErrorDisposition`
  （`retry` / `degrade` / `fail`）与一段中文 `strategy`。**数据放在错误码旁边**，
  判定函数（`harness/decisions.py`）只读不改，避免错误码与处置分类两处漂移。
- `Router.execute()` 遍历 `[主路由] + [备用路由]`：`retry` 先在**同一模型**内有限重试、
  耗尽后换模型；`degrade` 不重试当前模型、直接换下一个；`fail` 立即返回。
- `retry_assistant_call` 的可重试谓词由路由层注入（`should_auto_retry`），
  决策表因此是**唯一真源**，不会出现"重试层判一次、降级层再判一次"的漂移。
- 执行事实经 `ExecutionTrace` 出参回传（`AssistantMessage` 是 adapter 共享传输模型，
  没有承载位），落库为 `CallRecord.resilience` 的 `attempt` / `retry` / `fallback` /
  `disposition`，并随 `requests.payload` 供 Trace 页查看。
- `AUTH_INVALID` 的"报错但不阻塞"体现为响应里的 `warnings` 数组与落库的
  `disposition=degrade`，请求本身继续正常返回。

逐错误码的处置见 [error-codes.md](./error-codes.md) 第 3 节。

### 流式不做跨模型降级

`Router.stream()` 只用主路由。一旦开始吐字再换模型重来会产出**重复内容**。降级决策只存在于非流式的 `Router.execute()`。至于"哪些错误值得换模型"由处置决策表回答：瞬时失败（`retry`）与配置类失败（`degrade`）会换模型，确定性失败（`fail`，如请求非法、已流式输出后中断）不会——换模型也不会变好，降级只是浪费。

### 可观测性：一份数据，三种切法

Metrics / Logs / Trace 不是三套系统，而是同一份 `requests` 表的三种查询：

| 视图 | 切法 | 入口 |
|---|---|---|
| Logs | 一行 = 一次调用明细 | `storage.recent_calls()` |
| Trace | 按 `trace_id` 串联 | `storage.trace(trace_id)` |
| Metrics | 按时间窗聚合 | `storage.qps/error_rate/p99_latency/total_cost` |

`CallRecord.to_dict()` 同时输出顶层可索引的标量列（便于 SQL 聚合建索引）与嵌套结构（便于 Web 直接渲染）。记录里的路由维度是 `profile` 名。

### 可注入时钟

所有等待都走 `Clock` 协议。`FakeClock` 记录 `sleeps: list[float]` 并立即返回，因此**重试测试零真实等待**，断言的是退避序列本身而不是"跑得快"。窗口类指标同理：`Storage` 的时间来自注入的 `now` 函数，测试可以精确驱动时间窗。

### 密钥可落库，但不回显、不进日志

需求第 30 行允许"API key 可以在 Web 界面配置"，因此 `Model.api_key` 会写进 SQLite。为此做了三件事：

1. **不回显**：`model_to_payload` 一律把 `api_key` 置为 `None`，只回显 `api_key_set: bool`；`api_key=None` 表示"不修改"，空串才表示"清除"。
2. **不进日志 / Trace**：`redact_mapping` / `redact_text` 的敏感键标记包含 `apikey` / `authorization` / `token` / `secret` / `password`，`api_key` 命中即掩码。
3. **不进仓库**：`.gitignore` 排除 `llm_gw.sqlite3` 与 `.env`——密钥进数据库与 git init 叠加会直接泄漏。

取值优先级：**模型密钥 > 供应商 preset 约定的环境变量**（`OPENAI_API_KEY` 等），后者便于容器化部署时不必把密钥写进数据库。

> 已知局限（v0.3.0 期间发现，**未在本次修复**）：`_persist_models` 用的是
> `model_to_payload(model)`，而它刻意把 `api_key` 置为 `None`，因此密钥**实际没有
> 写进 SQLite**，进程重启后模型密钥为空、回退到环境变量。上面第 1 条（不回显）
> 的写法同时承担了"写库"与"出参"两个职责，是这次缺口的根因。详见
> [test-evidence.md](./test-evidence.md) 第 6 节。

### DeepSeek 是 preset 而不是独立协议

DeepSeek 官方 API 就是 OpenAI 兼容协议（参考实现 `pi` 里的 `deepseek.ts` 全文只是 `openAICompletionsApi` + 换 baseUrl）。因此不为它写独立协议 adapter，而是实现为 `adapter/presets/deepseek.py`。真正压测翻译层的是两种**协议不同**的 adapter：OpenAI `chat.completions` vs Anthropic Messages。

## 目录

```
llm_gw/
  runtime.py              组合根（唯一的进程装配点，uvicorn 入口）
  core/                   schema.py messages.py advanced.py events.py errors.py telemetry.py json_utils.py
  adapter/                base.py transform.py structured.py factory.py
    protocols/            openai_compat.py anthropic_messages.py
    presets/              openai.py deepseek.py anthropic.py registry.py
  router/                 profile.py registry.py rules.py router.py
  harness/                service.py retry.py decisions.py storage.py query.py sse.py
  util/                   clock.py
  web/                    app.py api_models.py
webapp/                   React + Vite 控制台
tests/                    core/ adapter/ router/ harness/ web/ support/
docs/                     architecture.md interface.md error-codes.md retry-strategy.md test-evidence.md
```

## 控制台与 agent API 的关系

两者共用同一个 `Router` 与 `Storage`，但契约不同：

| | agent API | 控制台 API |
|---|---|---|
| 模块 | `harness/service.py` | `web/app.py` |
| 前缀 | `/v1/*` | `/api/*` |
| 请求体 | 统一 `Task` | 控制台专用模型（`ChatRequest` 等） |
| 面向 | 程序 | 人 |
| 鉴权 | 需要：`Authorization: Bearer <password>`（口令取自 `LLM_GW_AGENT_PASSWORD`） | 不需要（浏览器自用，加门会连页面一起挡掉） |

`web/app.py` 额外提供模型 CRUD、gwprofile 配置、Dashboard 聚合与 Trace 搜索——这些 agent 不需要。`runtime.py` 把两者装配到同一个进程：外层 app 负责生命周期（启动时 `storage.init()` + `restore_config()`，关闭时释放 httpx 连接池与数据库连接），并同时提供两套路由——**先** `include_router(agent_router(service))` 注册 `/health` 与 `/v1/tasks*`，**再** `mount("/", console)` 挂控制台。

注册顺序是硬约束：`mount("/")` 会兜住所有剩余路径，agent 路由必须排在它前面，否则 `/health` 与 `/v1/tasks` 会被控制台吃掉而返回 404。`harness/service.py` 因此把 agent 路由抽成 `agent_router(service)`（而非只提供整建应用的 `create_app`），让组合根与测试复用同一份定义。
