# 错误码与重试决策

## 1. 错误码

对外稳定错误码定义在 `core/errors.py` 的 `ErrorCode`。**调用方按码程序化处理，不解析文案**——文案会随供应商变化，码不会。

| 错误码 | 阶段 | 含义 | 典型触发 |
|---|---|---|---|
| `AUTH_INVALID` | 认证 | 密钥无效 / 租户禁用 / 配额耗尽 | 401、403、`insufficient_quota`、`billing` |
| `AUTH_REQUIRED` | 网关入口 | agent 口令缺失或错误 | 调用 `/v1/tasks*` 未带 `Authorization: Bearer`，或口令不符 |
| `REQUEST_INVALID` | 请求验证 | 参数非法 / Schema 缺失 | 400、404、422 |
| `PROMPT_INVALID` | Prompt | 缺变量 / 超预算 | Prompt 模板渲染失败 |
| `ROUTE_NO_CANDIDATE` | 路由 | 无兼容模型 | 全部候选被能力过滤或不可用 |
| `TOOL_ROUNDS_EXCEEDED` | 请求验证 | 工具调用轮数超过 profile/模型配置的上限 | `Task.tool_rounds() > advanced.max_tool_rounds` |
| `QUEUE_REJECTED` | 排队 | 并发已满 / 截止时间不足 | 并发槽不足 |
| `CONN_FAILED` | 连接 | 网络抖动 / 短暂 5xx | 连接被拒、读超时、DNS 失败、408 |
| `RATE_LIMITED` | 首 Token 前 | 限流 | 429 |
| `UPSTREAM_OVERLOADED` | 首 Token 前 | 上游过载 | 5xx、`overloaded`、`high demand` |
| `STREAM_INTERRUPTED_AFTER_DATA` | 流中 | 已输出后中断 | 流式响应中途断开 |
| `OUTPUT_SCHEMA_INVALID` | 输出校验 | 输出未通过 JSON/Schema 校验且修复超限 | 修复次数超过 `max_attempts` |
| `CONTENT_REFUSED` | 内容安全 | 模型拒答 | `content_policy`、`safety_policy` |
| `CANCELLED` | 客户端 | 客户端取消 | 断连、`abort` |
| `UNKNOWN` | — | 未归类 | 兜底 |

## 2. 分类规则

`classify_error_code(exc)` 的判定顺序（**顺序即优先级**）：

```
1. GatewayError                → 直接取其 code
2. 内容拒答正则                 → CONTENT_REFUSED
3. 配额/账单正则（不可重试）     → AUTH_INVALID     ← 必须先于第 5 步
4. HTTP 状态码
     401 / 403                 → AUTH_INVALID
     404 / 422 / 其他 4xx      → REQUEST_INVALID
     408                       → CONN_FAILED
     429                       → RATE_LIMITED
     5xx                       → UPSTREAM_OVERLOADED
5. 传输层异常（无状态码）        → CONN_FAILED
6. 可重试文案正则               → UPSTREAM_OVERLOADED
7. 兜底                        → UNKNOWN
```

第 3 步必须排在第 6 步之前：配额耗尽的消息里常带 `429` 或 `rate limit` 字样，若先跑可重试正则就会被误判为"可以再试试"，白白重试到配额耗尽。

同理，`CONTENT_REFUSED` 正则刻意要求「拒答」与「内容/安全」语境**同时出现**——单独一个 `refused` 常见于连接错误（`ConnectError("refused")`），直接匹配会把网络故障误判成内容拒答。

## 3. 处置决策表（重试 / 降级 / 报错 三选一）

需求 Harness 层第 91-104 行的表落成 `core/errors.py` 的 `ERROR_DECISIONS: dict[ErrorCode, ErrorDecision]`。**数据放在错误码旁边**，避免错误码与处置分类两处漂移；`harness/decisions.py` 只提供判定函数。每个错误码在「重试 / 降级 / 报错」中**选一个**：

- **`retry`**：网关自行对**同一**模型有限重试（瞬时错误：连接抖动、限流、过载）；
- **`degrade`**：不重试当前模型，直接换路由表下一个（备用路由）；
- **`fail`**：直接返回错误，不做任何后续动作（请求非法、已流式输出后中断等）。

| 阶段 | 典型错误 | 是否适合重试 | 错误码 | 处置 | 网关行为 |
|---|---|---|---|---|---|
| 认证 | API Key 无效，租户禁用 | 否 | `AUTH_INVALID` | `degrade` | 换成路由表中下一个模型继续，并附 `warnings` 告警**不阻塞**；没有下一个模型则返回错误 |
| 认证（网关入口） | agent 口令缺失 / 错误 | 否 | `AUTH_REQUIRED` | `fail` | 直接返回 401。发生在选模型**之前**，没有"换下一个模型"这回事——调用方改正凭证后重新发起 |
| 请求验证 | 参数非法，Schema 缺失 | 否 | `REQUEST_INVALID` | `fail` | 直接返回错误，不重试 |
| 请求验证 | 工具调用轮数超上限 | 否 | `TOOL_ROUNDS_EXCEEDED` | `fail` | 在调用上游之前拒绝，避免为注定被拒的请求付费 |
| Prompt | 缺变量，超过预算 | 修正请求后重试 | `PROMPT_INVALID` | `degrade` | 网关不修改请求，按路由更换模型再试 |
| 路由 | 无兼容模型 | 配置变化后重试 | `ROUTE_NO_CANDIDATE` | `degrade` | 无兼容候选直接返回错误 |
| 排队 | 并发已满，截止时间不足 | 可拒绝或换池 | `QUEUE_REJECTED` | `degrade` | 拒绝或换池 |
| 连接 | 网络抖动，短暂 5xx | 有限重试 | `CONN_FAILED` | `retry` | 指数退避重试，耗尽后按路由换模型 |
| 首 Token 前 | 429，超时，过载 | 有限重试或 fall back | `RATE_LIMITED` / `UPSTREAM_OVERLOADED` | `retry` | 退避重试，耗尽后按路由换模型 |
| 已流式输出 | 流中断 | **不盲目重新生成** | `STREAM_INTERRUPTED_AFTER_DATA` | `fail` | 发流内 `error`，不发 `[DONE]`；不跨模型重生成 |
| 输出校验 | JSON/Schema 不符 | 有限修复 | `OUTPUT_SCHEMA_INVALID` | `degrade` | 网关不做同模型盲目重试，按路由换模型再试 |
| 内容安全 | 模型拒答 | 换模型重试，每模型最多一次 | `CONTENT_REFUSED` | `degrade` | 换下一个模型重试；每个模型恰好尝试一次（主备两个候选） |
| 客户端 | 取消 | 否 | `CANCELLED` | `fail` | 取消上游并释放槽位 |

判定函数：

| 函数 | 回答 |
|---|---|
| `decision_for(code)` | 该码对应的完整 `ErrorDecision`（未知码按 `FAIL`） |
| `disposition_for(code)` | 三选一中的哪一个：`retry` / `degrade` / `fail` |
| `should_auto_retry(code)` | 是否允许网关**自动重试同一模型** |
| `should_degrade(code)` | 是否应**直接切换备用路由**（不重试当前模型） |
| `describe_decision(code)` | 中文说明（即 `strategy`），供日志与响应 |

> `PROMPT_INVALID` / `ROUTE_NO_CANDIDATE` / `OUTPUT_SCHEMA_INVALID` 定为 `degrade`：
> 需求写的是"**修正请求后**重试""**配置变化后**重试"，前提是请求或配置被改变。
> 网关自身不修改请求，原样重试同一模型只会白耗一次配额，因此直接进入"按照路由
> 更换模型再重试"。

## 4. 两层可重试判定

网关在两个层面各自判定是否可重试，两者**规则不同、不能互相替代**：

| | 连接层 | 语义层 |
|---|---|---|
| 函数 | `is_retryable_provider_error(exc)` | `is_retryable_assistant_error(message)` |
| 输入 | 异常对象 | 已归一化的 `AssistantMessage` |
| 判定依据 | `x-should-retry` 头 → HTTP 状态 → 传输层异常类型 | 错误文案正则 |
| 使用方 | `retry_provider_request` | `retry_assistant_call`、`Router.execute` 的降级判断 |

连接层规则（移植 pi 的 `isRetryableProviderError`）：

1. `x-should-retry: true` / `false` 头**优先**；
2. 状态码 `408` / `409` / `429` / `5xx` → 可重试；
3. 无状态码的传输层异常（连接失败、超时）→ 可重试；
4. 其余 → 不可重试。

语义层规则（`is_retryable_assistant_error`）：

1. 非 `error` 终态 → 不可重试；
2. 命中**配额/账单正则** → 不可重试（**优先于第 3 步**）；
3. 命中**瞬时失败正则**（`overloaded` / `rate limit` / `connection refused` / `timed out` / `socket hang up` …）→ 可重试；
4. 其余 → 不可重试。

## 5. 错误消息格式

adapter 层统一以 `"<CODE>: <detail>"` 写错误消息：

```
UPSTREAM_OVERLOADED: provider (502): HTTP 502
RATE_LIMITED: provider (429): {"error":{"message":"Rate limit reached"}}
```

Harness 层据此**精确还原**错误码（`_error_code_from` 取冒号前的前缀查 `ErrorCode`），不必对文案做模糊匹配。前缀不是合法错误码时归为 `UNKNOWN`。

## 6. 错误归一化

不同 SDK 把 HTTP 状态与响应体塞在不同字段里，只读 `message` 会丢掉关键信息。`normalize_provider_error(exc)` 按优先级探测（移植 pi 的 `error-body.ts`）：

| 探测项 | 字段顺序 |
|---|---|
| HTTP 状态 | `response.status_code` → `statusCode` → `status` → `$metadata.httpStatusCode` → `$response.statusCode` |
| 响应体 | `response.text` → `body` → `error`（dict）→ `$response.body` |
| 请求 ID | `x-request-id` → `request-id` → `x-amzn-requestid` → `x-ms-request-id` → `anthropic-request-id` → `x-goog-request-id` → `cf-ray` |
| 重试提示 | `retry-after-ms` → `retry-after`（秒或 HTTP-date） |

错误体超过 `MAX_PROVIDER_ERROR_BODY_CHARS`（4000）会被截断，避免把整页 HTML 写进日志。

## 7. 脱敏

`redact_text` / `redact_mapping` 在 `CallRecord` 落库前兜底：

- **明文密钥形态**：`Bearer <token>`、`sk-…`、`AIza…`、`ghp_…`、`xox[baprs]-…`、JWT（三段 base64url）→ 替换为 `***`；
- **敏感键名**：`apikey` / `authorization` / `token` / `secret` / `password` / `credential` / `privatekey`（忽略大小写与 `-`/`_`）→ 整值掩码。

密钥本身不落在 `Model` 上（只从环境变量读取），因此模型配置可以自由落库与回显，脱敏是第二道防线而非唯一防线。
