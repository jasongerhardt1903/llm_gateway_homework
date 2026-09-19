# 架构

## 四层结构

需求给出的链路是 `LLM —— adapter —— 路由层 —— Harness层 —— service`。本项目按这个链路分层，**依赖只允许从右向左**：

```
后端 agent
    │  POST /v1/tasks  ·  POST /v1/tasks:stream (SSE)
    ▼
Harness 层        harness/service.py  harness/storage.py  harness/query.py  harness/sse.py
    │             对外 HTTP 契约、单一终态编码、调用记录落库、Metrics/Trace 查询
    ▼
路由层            router/registry.py  router/rules.py  router/router.py
    │             能力注册表、静态/动态路由、主备选择、重试与降级执行
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
| `router/` | `core/`、`adapter/base.py`（只依赖 `Adapter` 抽象）、`harness/retry.py` | 具体协议实现、FastAPI |
| `harness/` | 全部下层 | 具体 Web 框架之外的东西 |

`adapter/factory.py` 是唯一知道"协议标识 → 实现类"映射的地方；`runtime.py` 是唯一知道"运行时这些依赖各是什么"的地方。两者都是**组合根**，其余模块只依赖注入进来的接口。

## 一次请求的数据流

以流式请求为例（`POST /v1/tasks:stream`）：

1. **Harness 层**：`validate_syntax(raw)` 校验语法（非 JSON → 400），`validate_schema(data)` 校验结构（字段缺失 → 422），得到统一 `Task`。
2. **路由层**：`route(task)` 从 `task.required_capabilities()` 推导所需能力 → 静态路由优先 → 动态过滤（能力不匹配 / 不可用记入 `rejected`）→ 取前两名为 `primary` / `backup`。产出 `Decision`。
3. **adapter 层**：`adapter.stream(model, task, opts)` 把统一 task 翻译成供应商请求体（`build_request`），发出 SSE 请求，再用 `StreamAssembler` 把供应商 delta 装配成**统一事件序列**（`start` → `text_start` → `text_delta`* → `text_end` → `usage` → `done`）。
4. **Harness 层**：`encode_event()` 把统一事件编码成对外 SSE 帧；`done` 额外发 `data: [DONE]`，`error` / `cancelled` **不发**。
5. **落库**：`GatewayService._record()` 构造 `CallRecord` 写入 SQLite 的 `requests` + `cost_ledger` + `model_health`。

## 关键设计决策

### 统一事件序列 + 单一终态

所有供应商差异在 adapter 层被抹平成同一套事件（`core/events.py`）。`EventStream.push()` 强制：一个流只能以 `done` / `error` / `cancelled` 三者之一结束，**第二个终态抛 `TerminalStateViolation`** 而不是被静默忽略——静默会掩盖上游状态机 bug。

TTFT 口径：只认**有业务意义的 delta**（`text_delta` / `thinking_delta` / `toolcall_delta`），`start` 只是连接建立信号，把它当首 token 会系统性低估 TTFT。

### 两层校验

| 层 | 函数 | 失败异常 | HTTP |
|---|---|---|---|
| 语法 | `validate_syntax` | `SyntaxViolation` | 400 |
| Schema | `validate_schema` | `SchemaViolation` | 422 |

两层分开的理由：语法错误说明请求根本不是 JSON；schema 错误可以指出**字段路径**（`input.messages.0.role: ...`），对 agent 自我修正更有用。

### 结构化输出双层保证

1. **请求侧**：带 `response_schema` 时翻译成供应商的 JSON Schema 参数（OpenAI `response_format.json_schema`、Anthropic tool-use 形态）。
2. **响应侧**：本地用 pydantic 再校验一次——供应商的 schema 保证不等于应用层不会收到坏数据。
3. **流式边界**：delta 阶段用 `parse_streaming_json` 做增量解析（不能等全部 chunk 到齐），但部分 JSON 无法做 Schema 校验，因此**终校验只在流结束后做一次**。
4. **修复有界**：`validate_with_repair(..., max_attempts=2)`，超限抛 `OUTPUT_SCHEMA_INVALID`，绝不静默吞掉。

### 路由：静态优先于动态，主备始终保留

`apply_static` → `dynamic_select` → `build_primary_backup`。

- 命中静态路由时 `pinned=True`，**动态打分不得改写顺序**——那是运维显式指定的主备顺序。
- 未命中时按 `(消费比, 输入单价, 标签)` 排序，消费比低者优先。
- `Decision.rejected` 逐条记录被拒模型与原因。路由"为什么选它"和"为什么不选它"同样重要。

### 流式不做跨模型降级

`Router.stream()` 只用主路由。一旦开始吐字再换模型重来会产出**重复内容**。降级决策只存在于非流式的 `Router.execute()`，且仅对瞬时失败生效——认证 / 配额 / 内容拒答换模型也不会变好，降级只是浪费。

### 可观测性：一份数据，三种切法

Metrics / Logs / Trace 不是三套系统，而是同一份 `requests` 表的三种查询：

| 视图 | 切法 | 入口 |
|---|---|---|
| Logs | 一行 = 一次调用明细 | `storage.recent_calls()` |
| Trace | 按 `trace_id` 串联 | `storage.trace(trace_id)` |
| Metrics | 按时间窗聚合 | `storage.qps/error_rate/p99_latency/total_cost` |

`CallRecord.to_dict()` 同时输出顶层可索引的标量列（便于 SQL 聚合建索引）与嵌套结构（便于 Web 直接渲染）。

### 可注入时钟

所有等待都走 `Clock` 协议。`FakeClock` 记录 `sleeps: list[float]` 并立即返回，因此**重试测试零真实等待**，断言的是退避序列本身而不是"跑得快"。窗口类指标同理：`Storage` 的时间来自注入的 `now` 函数，测试可以精确驱动时间窗。

### 密钥不落在 Model 上

`Model` 是纯描述（id / 协议 / baseUrl / 价格 / 能力），密钥通过 `AdapterOptions.api_key` 调用级传入，来源是 preset 约定的环境变量（`OPENAI_API_KEY` 等）。这样模型配置可以自由落库、回显到控制台、写进日志，都不会泄漏密钥。`CallRecord` 落库前还会过一次 `redact_text` / `redact_mapping` 兜底。

### DeepSeek 是 preset 而不是独立协议

DeepSeek 官方 API 就是 OpenAI 兼容协议（参考实现 `pi` 里的 `deepseek.ts` 全文只是 `openAICompletionsApi` + 换 baseUrl）。因此不为它写独立协议 adapter，而是实现为 `adapter/presets/deepseek.py`。真正压测翻译层的是两种**协议不同**的 adapter：OpenAI `chat.completions` vs Anthropic Messages。

## 目录

```
llm_gw/
  runtime.py              组合根（唯一的进程装配点，uvicorn 入口）
  core/                   schema.py messages.py events.py errors.py telemetry.py json_utils.py
  adapter/                base.py transform.py structured.py factory.py
    protocols/            openai_compat.py anthropic_messages.py
    presets/              openai.py deepseek.py anthropic.py registry.py
  router/                 registry.py rules.py router.py
  harness/                service.py retry.py decisions.py storage.py query.py sse.py
  util/                   clock.py
  web/                    app.py api_models.py
webapp/                   React + Vite 控制台
tests/                    core/ adapter/ router/ harness/ web/ support/
docs/                     architecture.md interface.md error-codes.md retry-strategy.md
```

## 控制台与 agent API 的关系

两者共用同一个 `Router` 与 `Storage`，但契约不同：

| | agent API | 控制台 API |
|---|---|---|
| 模块 | `harness/service.py` | `web/app.py` |
| 前缀 | `/v1/*` | `/api/*` |
| 请求体 | 统一 `Task` | 控制台专用模型（`ChatRequest` 等） |
| 面向 | 程序 | 人 |

`web/app.py` 额外提供模型 CRUD、路由配置、Dashboard 聚合与 Trace 搜索——这些 agent 不需要。`runtime.py` 把两者装配到同一个进程：外层 app 只负责生命周期（启动时 `storage.init()` + `restore_config()`，关闭时释放 httpx 连接池与数据库连接），控制台应用按根路径挂载。
