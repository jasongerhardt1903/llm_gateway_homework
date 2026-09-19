# 接口

## 1. 统一 Task Schema

agent 与网关之间的唯一请求形态。定义在 `core/schema.py`，两层校验（语法 → schema）。

```jsonc
{
  "task_id": "req-2026-0919-0001",     // 必填，调用方自带，便于幂等与链路追踪
  "logical_model": "default",          // 可选，逻辑模型名；为空时走默认路由
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
  "usage": { "input": 12, "output": 9, "total_tokens": 21, "cost": 0.0000234 }
}
```

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
data: {"type": "done", "reason": "stop", "message": {"text": "你好", "stop_reason": "stop", "terminal": "done", "usage": {...}}}

data: [DONE]
```

### 客户端断连

客户端断开 → ASGI 抛 `CancelledError` → `GatewayService.stream_sse` 取消上游 httpx 请求并释放并发槽，然后落一条 `cancelled` 终态记录。不取消的话，上游请求会继续跑到结束，既浪费配额也让并发槽迟迟不释放。

---

## 4. 控制台 API（`web/app.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/providers` | 供应商下拉菜单数据源（provider / display_name / api / base_url / env_key / notes） |
| GET | `/api/models` | 模型清单 |
| POST | `/api/models` | 新增模型 |
| PUT | `/api/models/{provider}/{model_id}` | 修改模型（改 id/provider 时先移除旧标签，不留孤儿条目） |
| DELETE | `/api/models/{provider}/{model_id}` | 删除模型 |
| GET | `/api/routes` | 静态路由 + 重试配置 |
| PUT | `/api/routes` | 保存静态路由 + 重试配置 |
| GET | `/api/dashboard` | 聚合指标 + 各模型状态表 |
| GET | `/api/traces?q=&limit=` | 关键字搜索调用记录（空 `q` 返回最近记录） |
| GET | `/api/traces/{trace_id}` | 按 `trace_id` 取整条链路 |
| POST | `/api/chat/stream` | Chat 页的 SSE 代理 |
| POST | `/api/chat` | Chat 页的非流式版本 |
| POST | `/api/tasks:validate` | 用两层校验试跑一个原始 task（调试用） |

模型清单与路由配置通过 `Storage` 的 `config` 表持久化，进程重启后由 `restore_config()` 恢复。

### `PUT /api/routes`

```jsonc
{
  "routes": [
    { "logical_model": "default", "candidates": ["openai/gpt-4o-mini", "deepseek/deepseek-chat"] }
  ],
  "retry_enabled": true,
  "max_retries": 3        // 0–10，需求默认 3
}
```

`candidates` 的顺序即主备顺序，**静态路由优先于动态路由**，顺序不会被动态打分改写。

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
  "logical_model": "", "provider": "openai", "model": "gpt-4o-mini", "api": "openai-completions",
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
# 控制台 + agent API（同一进程）
uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000

# 环境变量
export OPENAI_API_KEY=sk-...
export DEEPSEEK_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_GW_DB=/path/to/llm_gw.sqlite3   # 可选，默认 ./llm_gw.sqlite3
```

前端开发模式：

```bash
cd webapp && npm install && npm run dev    # http://localhost:5173，/api 代理到 8000
cd webapp && npm run build                 # 产物到 webapp/dist，由 FastAPI 同源托管
```

密钥只从环境变量读取（preset 约定的 `env_key`），不落库、不回显到控制台。
