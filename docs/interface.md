# 接口

## 1. 统一 Task Schema

agent 与网关之间的唯一请求形态。定义在 `core/schema.py`，两层校验（语法 → schema）。

```jsonc
{
  "task_id": "req-2026-0919-0001",     // 必填，调用方自带，便于幂等与链路追踪
  "profile": "default",                // 可选，gwprofile 名；为空时走 default profile
  "input": {
    "messages": [                      // 必填，至少 1 条
      { "role": "user", "content": "你好" }
    ],
    "system": "你是一个简洁的助手",     // 可选
    "tools": [                         // 可选，工具定义
      { "name": "get_weather", "description": "查天气", "parameters": {"type": "object", "properties": {}} }
    ],
    "response_schema": { "type": "object", "properties": { "answer": { "type": "string" } } },  // 可选，结构化输出
    "max_tokens": 512,                 // 可选
    "temperature": 0.7,                // 可选，必须 ∈ [0, 2]
    "stream": true                     // 默认 true —— adapter 层默认用 SSE 与 LLM 通讯
  },
  "metadata": { "run_id": "run-1", "step_id": "step-2", "prompt_name": "summarize" }
}
```

### 消息

`role` ∈ `system` / `user` / `assistant` / `tool`。

`content` 可以是纯字符串，也可以是内容块列表：

| 块 | 字段 | 用途 |
|---|---|---|
| `text` | `text` | 文本 |
| `image` | `data`（base64）、`mime_type` | 视觉输入 |
| `tool_call` | `id`、`name`、`arguments` | assistant 轮次里发起的工具调用 |
| `tool_result` | `tool_call_id`、`content` | 工具执行结果，回填给模型 |

`status` ∈ `ok` / `error` / `aborted`（默认 `ok`）。**失败的轮次不会回灌给模型**——把错误消息当历史会让模型模仿错误，`adapter/transform.py` 会丢弃它们。

### 两层校验

| 层 | 触发条件 | 异常 | HTTP |
|---|---|---|---|
| 语法 | 非法 UTF-8 / 非法 JSON / 根节点不是对象 | `SyntaxViolation` | 400 |
| Schema | 字段缺失、类型错误、`temperature` 越界、`messages` 为空 | `SchemaViolation` | 422 |

两者都以 `REQUEST_INVALID` 错误码返回，但 `message` 不同——schema 错误带字段路径：

```
task does not match schema:
  - input.messages.0.role: Input should be 'system', 'user', 'assistant' or 'tool'
```

### 能力推导

`task.required_capabilities()` 从 task 特征推导所需模型能力，供路由层匹配：

| 能力 | 触发条件 |
|---|---|
| `sse` | 恒为 `true` |
| `streaming` | `input.stream` |
| `tools` | `input.tools` 非空 |
| `json_schema` | `input.response_schema` 非空 |
| `vision` | 任一消息含 `image` 块 |
| `reasoning` | 由路由显式要求 |

---

## 2. agent API（`harness/service.py`）

### 鉴权

agent API 受**简单口令**保护；控制台 `/api/*` 不在保护范围内（否则页面自身都打不开）。

| 项 | 约定 |
|---|---|
| 口令来源 | **网页配置**（控制台「设置」页，落 `config` 表的 `agent_password` 键）或环境变量 `LLM_GW_AGENT_PASSWORD`；**环境变量优先**（0.8.4 起支持网页配置） |
| 携带方式 | `Authorization: Bearer <password>` |
| 未配置口令 | **不强制**（便于本地开发；生产环境请务必配置） |
| 豁免 | `GET /health`——探活程序通常不带凭证 |
| 失败 | `401` + `AUTH_REQUIRED`，响应头带 `WWW-Authenticate: Bearer` |

环境变量优先是刻意的：口令是部署期凭据，容器化部署不必把它写进 `llm_gw.sqlite3`
（"把库拷走"就等于"拿到口令"）。网页配置服务的是本地/单机场景——不必改名环境变量重启
进程。`GET /api/settings` 会如实回报口令当前来源（`env` / `console` / `none`）与
`env_key`，供页面提示用户"改网页配置在 `env` 情况下不生效"；**它不回显口令本身**。

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer $LLM_GW_AGENT_PASSWORD" \
  -H "content-type: application/json" \
  -d '{"task_id":"req-1","input":{"messages":[{"role":"user","content":"hi"}]}}'
```

`AUTH_REQUIRED`（网关入口，处置 `fail`）与 `AUTH_INVALID`（上游密钥失效，处置
`degrade`）是两回事：前者发生在选模型**之前**，换模型没有意义。

### `POST /v1/tasks` — 非流式

请求体：统一 `Task`。

响应：

```jsonc
{
  "task_id": "req-1",
  "stop_reason": "stop",          // stop | length | tool_calls | error | aborted
  "terminal": "done",             // done | error | cancelled
  "text": "你好！有什么可以帮你？",
  "error_message": null,
  "usage": { "input": 12, "output": 9, "total_tokens": 21, "cost": 0.0000234 },
  "warnings": []                  // 降级/告警信息；无告警时为空数组
}
```

`warnings` 是**非阻塞**告警：例如认证失败（`AUTH_INVALID`）时网关会换成路由表
下一个模型继续，同时把 `"AUTH_INVALID: 已降级 A → B；…"` 追加到该数组，请求本身
正常返回。处置决策见 [error-codes.md](./error-codes.md) 第 3 节。

### `POST /v1/tasks:stream` — 流式（默认）

请求体同上。响应 `content-type: text/event-stream`。可选请求头 `x-trace-id` 指定链路 ID。

### `GET /health`

```json
{ "status": "ok" }
```

---

## 3. SSE 事件

### 统一事件类型

| `event:` | `data:` 关键字段 | 说明 |
|---|---|---|
| `start` | — | 连接建立。**不计入 TTFT** |
| `text_start` | `index` | 文本块开始 |
| `text_delta` | `index`、`delta` | 文本增量 |
| `text_end` | `index`、`text` | 文本块结束（含完整文本） |
| `thinking_start` | `index` | 思考块开始 |
| `thinking_delta` | `index`、`delta` | 思考增量 |
| `thinking_end` | `index`、`thinking` | 思考块结束 |
| `toolcall_start` | `index`、`id`、`name` | 工具调用开始 |
| `toolcall_delta` | `index`、`delta` | 工具参数增量 |
| `toolcall_end` | `index`、`id`、`name`、`arguments` | 工具调用结束（参数已解析） |
| `usage` | `usage` | 用量与成本 |
| `done` | `reason`、`message` | **正常终态**，其后额外发 `data: [DONE]` |
| `error` | `reason`、`error` | **错误终态**，**不发 `[DONE]`** |
| `cancelled` | `reason`、`message` | **取消终态**，**不发 `[DONE]`** |

`index` 是**统一 `message.content` 中的位置**，不是供应商侧的块编号——供应商索引不连续、乱序也不会错位。

### 单一终态不变式

一个流只能以 `done` / `error` / `cancelled` 三者之一结束。第二个终态事件抛 `TerminalStateViolation` 而不是被静默忽略。

### `[DONE]` 的语义

| 客户端收到 | 含义 | 该做什么 |
|---|---|---|
| `event: done` + `data: [DONE]` | 正常结束 | 采用已收到的内容 |
| `event: error`（无 `[DONE]`） | 异常结束 | 已收到的内容是**部分**的；由客户端决定重试或接受 |
| `event: cancelled`（无 `[DONE]`） | 被取消 | 丢弃或保留部分内容 |

用 `[DONE]` 而非"连接关闭"作为正常结束标志，是因为连接关闭在两种情况下都会发生。

### 示例

```
event: start
data: {"type": "start"}

event: text_start
data: {"type": "text_start", "index": 0}

event: text_delta
data: {"type": "text_delta", "index": 0, "delta": "你"}

event: text_delta
data: {"type": "text_delta", "index": 0, "delta": "好"}

event: text_end
data: {"type": "text_end", "index": 0, "text": "你好"}

event: usage
data: {"type": "usage", "usage": {"input": 12, "output": 2, "total_tokens": 14, "cost": 0.0000234}}

event: done
data: {"type": "done", "reason": "stop", "message": {"role": "assistant", "content": [{"type": "text", "text": "你好"}], "usage": {...}, "stop_reason": "stop", "error_message": null, "response_model": null, "response_id": null, "raw_stop_reason": "stop", "timestamp": 1789995978.8}}

data: [DONE]
```

### `done` / `cancelled` 里的 `message` 形状

`message` 是**原始的 `AssistantMessage` 形状**（网关直接 `asdict` 展开，字段名不转驼峰），因此：

- 内容**只有 `content[]` 数组**这一个入口，块类型是 `text` / `thinking` / `toolCall` / `toolResult`；**没有** `message.text` 这样的平铺字段；
- 工具调用在 `content[]` 里、块类型是驼峰的 `toolCall`，**顶层没有** `tool_calls`；
- 是否要求调工具看 `stop_reason`（`tool_use` / `stop` / `length` …），原始原因在 `raw_stop_reason`。

带工具调用的一轮实测原文：

```
event: toolcall_start
data: {"type": "toolcall_start", "index": 0, "id": "call_abc", "name": "get_time"}

event: toolcall_end
data: {"type": "toolcall_end", "index": 0, "id": "call_abc", "name": "get_time", "arguments": {}}

event: done
data: {"type": "done", "reason": "tool_use", "message": {"role": "assistant", "content": [{"type": "toolCall", "id": "call_abc", "name": "get_time", "arguments": {}}], "usage": {...}, "stop_reason": "tool_use", "error_message": null, "response_model": null, "response_id": null, "raw_stop_reason": "tool_calls", "timestamp": 1789995972.7}}

data: [DONE]
```

客户端编排工具循环时，`toolcall_end` 与 `done.message.content` 里的 `toolCall` 是同一份信息的两种给法：前者随流逐块到达（适合边收边执行），后者是一次性汇总（适合回灌历史）。**网关不会替你跑这个循环**——把 `tool_result` 塞回 `messages` 再调一次是调用方的责任，这也正是 Console 的 Chat 页在演示的事。

### 客户端断连

客户端断开 → ASGI 抛 `CancelledError` → `GatewayService.stream_sse` 取消上游 httpx 请求并释放并发槽，然后落一条 `cancelled` 终态记录。不取消的话，上游请求会继续跑到结束，既浪费配额也让并发槽迟迟不释放。

---

## 4. 控制台 API（`web/app.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/providers` | 供应商下拉菜单数据源（provider / display_name / api / base_url / env_key / notes） |
| GET | `/api/providers/{provider}/models` | 向供应商实时查询可选模型 + 能力 + 高级配置项清单；未知供应商 404（0.8.0 新增） |
| GET | `/api/models` | 模型清单 |
| POST | `/api/models:test` | 模型连接测试：发一次最小对话验证连通性（0.8.0 新增） |
| GET | `/api/meta` | 当前版本号与更新日志原文（0.8.0 新增） |
| POST | `/api/models` | 新增模型 |
| PUT | `/api/models/{provider}/{model_id}` | 修改模型（改 id/provider 时先移除旧标签，不留孤儿条目） |
| DELETE | `/api/models/{provider}/{model_id}` | 删除模型 |
| GET | `/api/profiles` | gwprofile 清单 |
| POST | `/api/profiles` | 新增 gwprofile |
| PUT | `/api/profiles/{name}` | 修改 gwprofile |
| DELETE | `/api/profiles/{name}` | 删除 gwprofile |
| GET | `/api/dashboard` | 聚合指标 + 各模型状态表 |
| GET | `/api/traces?q=&limit=` | 关键字搜索调用记录（空 `q` 返回最近记录） |
| GET | `/api/traces/{trace_id}` | 按 `trace_id` 取整条链路 |
| GET | `/api/settings` | 网关设置：口令是否已配、来源（`env`/`console`/`none`）、`env_key`（0.8.4 新增，不回显口令） |
| PUT | `/api/settings/agent-password` | 配置 agent 口令；空串/`null` 表示清除（0.8.4 新增） |
| GET | `/api/exchanges?q=&task_id=&limit=` | 与后端 agent 的通讯原始日志；`task_id` 优先于 `q`（0.8.4 新增） |
| GET | `/api/exchanges/{exchange_id}` | 单条通讯的原始往来报文（0.8.4 新增） |
| POST | `/api/tasks:validate` | 用两层校验试跑一个原始 task（调试用） |

模型清单与 gwprofile 通过 `Storage` 的 `config` 表持久化，进程重启后由 `restore_config()` 恢复。

> **0.8.4 起控制台不再有 Chat 专属契约。** 原先的 `POST /api/chat` 与
> `POST /api/chat/stream` 已删除：Chat 页作为"一个简单的后端 agent Loop"，
> 直接按 agent 的 `Task` schema 调 `/v1/tasks:stream`（需求管理与交互层功能第 7 条）。
> 这样控制台用的是与真实 agent **同一份**契约，代理层带来的口径漂移随之消失。

### `GET /api/exchanges` — 通讯原始日志（0.8.4 新增）

与 `/api/traces` 是**两层不同的切法**，刻意不合并：

| | 记录什么 | 粒度 |
|---|---|---|
| `requests`（`/api/traces`） | 翻译后的调用：落到哪个模型、花了多少钱 | 一次**模型调用** |
| `exchanges`（`/api/exchanges`） | agent 发来什么字节、网关回什么字节 | 一次**通讯** |

一次通讯可能对应 0 次模型调用（两层校验失败时一次都没有），因此不能合成一张表。
**被两层校验挡下的通讯同样有记录**（400 / 422），这类记录连 `task_id` 都只能从原文里
尽力抠：语法合法就取报文里的 `task_id`，连 JSON 都不合法则归为空串一组——否则 agent
最常踩的 schema 错误反而查不到。按 `(task_id, flow_index)` 两级组织——同一个 task 可能
来回多次（校验失败重发、流式重连、多轮补充），`flow_index` 是那个"第二级"，从 1 起递增。

```jsonc
{
  "exchange_id": "ex-3f9a1c2b7d40",
  "task_id": "req-1",
  "trace_id": "tr-1",              // 取自请求头 x-trace-id，可为空
  "ts": 1789976473.12,             // epoch 秒
  "flow_index": 1,                 // 同一 task 内的第几次通讯
  "endpoint": "/v1/tasks:stream",  // 或 "/v1/tasks"
  "stream": true,
  "request_raw": "{\"task_id\":\"req-1\", ...}",   // agent 发来的原文
  "response_raw": "event: text_delta\ndata: {...}\n\n ... event: done\n",  // 实际发出的原文
  "status": "done",                // done | error | cancelled（通讯的真实结局，不是 HTTP 码）
  "error_code": null,
  "error_message": null,
  "duration_ms": 812.4,
  "meta": { "model": "openai/gpt-4o-mini", "profile": "prod",
            "degraded_from": "", "degraded_to": "" }
}
```

两侧报文都**先脱敏再落盘**：agent 可能在 metadata 里夹带自己的凭据，原样入库就等于把
凭据写到了磁盘上。`response_raw` 是实际发给客户端的 SSE 帧原文（含 `event:` / `data:`
行与 `[DONE]`），不是渲染后的结果——日志页据此提供"raw data 模式"与"渲染后易读模式"
两种查看方式（需求 Harness 层功能第 3 条）。被放弃的那条流（首 delta 前降级）的帧
**不进记录**——客户端从没收到过它们。

### 流式降级（0.8.4 新增）

`/v1/tasks:stream` 现在也支持降级（需求 adapter 层功能第 8 条"降级时按路由中可用模型
执行"）。**换模型只发生在首个业务 delta 之前**：那时客户端一个业务字节都没收到，重开
一条流不会造成重复内容。一旦吐过业务 delta 就不再换模型——需求明令"已流式输出 →
不盲目重新生成"。

对外仍然只有一个终态：被放弃的那条流的 `error` 事件**不会**转发给客户端，只有最后
落地的那条流才产出终态事件与可选的 `[DONE]`。落库时 `resilience.attempt` 记实际开过的
流数、`fallback` 记是否降级、`resilience.disposition` 记触发降级的那个处置。

### `GET /api/profiles` / `POST /api/profiles`

```jsonc
{
  "name": "prod",                  // 必填
  "display_name": "生产",          // 可选
  "models": [                      // 必填，至少 1 个
    { "label": "gpt-4o-mini", "prefer_own_config": false }
  ],
  "template_enabled": false,       // 可选，默认 false
  "template": {                    // 可选，profile 内统一高级配置模版
    "temperature": 0.7, "top_p": null, "top_k": null,
    "thinking_mode": "default", "max_tool_rounds": null, "max_tokens": null
  },
  "route_mode": "dynamic",         // dynamic | static，默认 dynamic
  "static_order": ["a/gpt-4o-mini", "b/deepseek-flash"],  // static 时的执行顺序（自上而下）
  "retry_enabled": true,
  "max_retries": 3                 // 0–10，per-profile 重试上限
}
```

- `models[].label` 引用模型（`provider/id` 或别名）；勾选 `prefer_own_config` 表示该模型在本 profile 内忽略模版、用自己的高级配置。
- 模版判定矩阵：启用 + 未勾选 `prefer_own_config` → 用模版；启用 + 勾选 → 用模型自身配置；未启用 → 用模型自身配置。
- `route_mode == "static"` 且 `static_order` 非空时 `is_pinned()` 为真，动态打分不得改写顺序；顺序中标签全部无效则退化为动态选择。
- `static_order` 是 `models[].label` 的**有序数组**。控制台的「路由表」编辑器用拖拉拽维护它：左侧是 profile 已选的模型，拖到右侧成为路由链，右侧内部可上下拖拽排序，**执行自上而下**（需求路由模块功能第 6 条）。profile 的 `name` 就是这张路由表的名称。
- 删除 profile 时，引用它的旧调用记录里仍保留当时生效的 `profile` 名（作废的引用会自动跳过被删模型）。

### 模型 payload 与高级配置、密钥

`GET /api/models` 返回每条模型：

```jsonc
{
  "provider": "openai", "id": "gpt-4o-mini", "name": "GPT-4o Mini", "api": "openai-completions",
  "base_url": "...", "context_window": 128000, "max_tokens": 16384,
  "capabilities": { "sse": true, "streaming": true, "tools": true, "json_schema": true, "vision": true, "reasoning": false },
  "cost": { "input": 0.00015, "output": 0.0006, "cache_read": 0, "cache_write": 0 },
  "tag": "",                               // 模型 tag
  "advanced": {                            // 高级配置项；null 表示请求时不发送该参数
    "temperature": null, "top_p": null, "top_k": null,
    "thinking_mode": "default", "max_tool_rounds": null, "max_tokens": null
  },
  "api_key_set": false,                    // 只读；是否已配置密钥
  "api_key": null                          // 只写不回显，恒为 null；见下
}
```

密钥约定：

- `POST/PUT /api/models` 里 `api_key` 缺省表示**不修改**已有密钥，传空串表示**清除**。
- 响应里的 `api_key` 恒为 `null`，只回显 `api_key_set` 布尔量——密钥绝不回显给控制台。
- 「只写不回显」约束的是 **HTTP 响应**，不是持久化：密钥会如实写进 SQLite 的 `config`
  表（`restore_config` 在启动时读回），否则重启后模型全部丢掉密钥、回落到环境变量。
  落库因此走内部 payload（`model_to_stored_payload`），与对外那份分开。
- 实际调用时的取值优先级：模型密钥 > 供应商 preset 约定的环境变量，见 README。
- `advanced.max_tool_rounds` 是工具调用轮数护栏，超限即返回 `TOOL_ROUNDS_EXCEEDED`（处置为 `fail`）。

### `GET /api/providers/{provider}/models`（0.8.0）

一次请求同时满足需求"模型管理层"第 1a（能力）、1b（可选模型）、1c（高级配置项）：

```jsonc
{
  "provider": "openai", "api": "openai-completions", "base_url": "https://api.openai.com/v1",
  "source": "preset",          // upstream = 实时拉到的；preset = 上游不可用，回退内置清单；empty = 两者都为空
  "error": "...",              // 上游失败时的原因原文（含 URL 与截断到 200 字的响应体），成功为 null
  "advanced": {
    "items": [                 // 1c：该协议真的认得的参数，未知协议给全集
      { "key": "temperature", "label": "temperature", "kind": "number", "step": 0.05 },
      { "key": "thinking_mode", "label": "思考模式", "kind": "choice",
        "choices": [{ "value": "on", "label": "开启" }, { "value": "off", "label": "关闭" }],
        "note": "开启与关闭互斥；都不勾选则跟随模型默认。" }
    ]
  },
  "models": [
    { "id": "gpt-4o-mini", "name": "GPT-4o Mini", "known": true,   // known=false 表示上游新出现、preset 里没有的型号
      "display_provider": "OpenAI",
      "api": "openai-completions", "base_url": "...",
      "context_window": 128000, "max_tokens": 16384,
      "capabilities": { "sse": true, "streaming": true /* … */ },
      "cost": { "input": 0.00015, "output": 0.0006, "cache_read": 0, "cache_write": 0 } }
  ]
}
```

- 上游只回模型 id，因此能力与价格按 preset 已知型号补齐；**preset 里没有的型号标
  `known=false` 并只给协议默认能力**（`sse`/`streaming`），不编造数值。
- 查询用的密钥取"已保存的模型密钥 > preset 约定的环境变量"，与真实调用的优先级一致。
- 未知供应商返回 `404`。
- **结果有短时缓存**（v0.8.1）：同一 `(provider, api, base_url)` 成功缓存 300 秒、
  失败缓存 30 秒，因此连续打开「新增模型」不会反复打上游；失败缓存更短是为了让
  "刚换上有效密钥"能在半分钟内恢复正常。连接测试（`/api/models:test`）**不走**这份
  缓存——它验证的就是"此刻能不能通"。

### `POST /api/models:test`（0.8.0）

请求体与 `POST /api/models` 完全一致（未保存的模型也可以先测再存），返回：

```jsonc
{ "ok": false, "code": "AUTH_INVALID", "message": "provider (401): Authentication Fails (governor)",
  "latency_ms": 92, "text": "", "response_model": "" }
```

- 走 adapter 的非流式调用路径发一次**真实最小对话**（`ping` + `max_tokens=1`），因此
  能同时验证网络、鉴权、模型 ID 三件事；**不入库、不计费、不落 Trace**。
- **上游失败也是 `200` + `ok=false`**：模型通不通是"测试结论"，不是"请求非法"；
  只有请求体本身不合法才返回 `422`。前端据此区分"表单填错"和"密钥填错"。

### `GET /api/meta`（0.8.0）

```jsonc
{ "version": "0.8.0", "changelog": "# 版本更新说明\n\n…" }
```

控制台侧边栏据此显示版本号与更新日志；`changelog` 读不到文件时为空串，不影响页面。

### `GET /api/dashboard`

```jsonc
{
  "qps_1m": 0.05, "qps_1h": 0.01,
  "error_rate_1m": 0.0,
  "p99_latency_1m": 842.3,
  "total_cost_1h": 0.0012,
  "health": [ /* model_health 原始行 */ ],
  "models": [
    {
      "model": "gpt-4o-mini", "provider": "openai", "display_provider": "OpenAI",
      "status": "healthy", "available": true,
      "success_count": 12, "error_count": 1, "last_error": null,
      "used_tokens": 4821, "quota_tokens": null, "remaining_tokens": null
    }
  ]
}
```

未配置配额的模型 `remaining_tokens` 为 `null`，前端显示为「—」。

### `GET /api/traces`

返回 `CallRecord.to_dict()` 的数组，覆盖 8 个维度：

```jsonc
{
  "trace_id": "chat-chat-0fcc9f54c857", "run_id": "", "step_id": "", "call_id": "call-f4676a1dced6",
  "prompt_name": "", "prompt_version": "", "prompt_sha256": "3f2a...", "prompt_schema_version": "",
  "profile": "", "provider": "openai", "model": "gpt-4o-mini", "api": "openai-completions",
  "usage": { "input": 12, "output": 9, "cache_read": 0, "cache_write": 0, "reasoning": null, "total_tokens": 21 },
  "ttft_ms": 412.5, "generation_ms": 1103.2, "total_ms": 1515.7,
  "queue_ms": 0.0, "route_ms": 0.0, "latency": { /* 同上，嵌套一份 */ },
  "attempt": 1, "retry": 0, "fallback": false, "timeout_budget_ms": 0,
  "finish_reason": "stop", "terminal": "done", "output_valid": null,
  "error_code": null, "error_message": null, "http_status": null, "provider_request_id": null,
  "cost": { "input": 0.0000018, "output": 0.0000054, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0000072 },
  "stream_chunk_count": 18,
  "metadata": {},
  "ts": 1789754037.43
}
```

`ts` 是落库时间（秒，UTC 浮点），冗余进 payload 以便 Trace 页直接展示"请求时间"。

---

## 5. 启动

```bash
# 控制台 + agent API（同一进程）；推荐直接用 ./run.sh（会自动激活 venv 并加载 .env）
./run.sh
.venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000

# 环境变量
export OPENAI_API_KEY=sk-...
export DEEPSEEK_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_GW_DB=/path/to/llm_gw.sqlite3   # 可选，默认 ./llm_gw.sqlite3
export LLM_GW_HOST=0.0.0.0                 # 可选，局域网访问
export LLM_GW_AGENT_PASSWORD=...           # 可选；设置后 /v1/tasks* 要求 Bearer 口令
```

前端开发模式：

```bash
cd webapp && npm install && npm run dev    # http://localhost:5173，/api 代理到 8000
cd webapp && npm run build                 # 产物到 webapp/dist，由 FastAPI 同源托管
```

密钥可来自环境变量（preset 约定的 `env_key`），也可在控制台模型页录入。录入的值写入 SQLite 但**不回显**（只显示"已配置/未配置"），优先级高于环境变量；`.gitignore` 已排除 `llm_gw.sqlite3` 与 `.env`。
