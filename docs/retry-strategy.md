# 重试策略

## 1. 分层

重试发生在两个层面，各自独立：

| | 连接层 | 语义层 | 路由层 |
|---|---|---|---|
| 函数 | `retry_provider_request` | `retry_assistant_call` | `Router.execute` |
| 输入 | 异常对象 | `AssistantMessage` | 主路由失败的 `AssistantMessage` |
| 动作 | 退避重试同一请求 | 退避重试同一模型 | 降级到备用模型 |
| 判定 | `is_retryable_provider_error` | `is_retryable_assistant_error` | `is_retryable_assistant_error` |

三者不叠加使用：连接层负责"这一次 HTTP 请求要不要再发一遍"，语义层负责"这一次模型调用要不要再来一次"，路由层负责"换一个模型试试"。

**重试策略按 profile 取值**：`Router.policy_for(profile)` 返回 `RetryPolicy(enabled=profile.retry_enabled, max_retries=profile.max_retries)`；没有 profile（未配置任何 profile，走全局模型池）时用 router 的默认策略。因此同一个模型可以挂在"允许重试 3 次"与"不重试"两个 profile 下，行为不同。

## 2. 退避公式

```
delay(attempt) = min(base_delay_ms × 2^(attempt-1), max_delay_ms)
delay(attempt) ×= 1 + jitter_ratio × (2·random() - 1)     # ±25% 抖动
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | **per-profile** 可配（`profile.retry_enabled`） |
| `max_retries` | `3` | **per-profile** 可配（`profile.max_retries`，0–10），需求规定默认 3 |
| `base_delay_ms` | `500` | 退避基数 |
| `max_delay_ms` | `60000` | 单次退避上限 |
| `jitter_ratio` | `0.25` | 抖动比例；测试设为 0 以断言精确序列 |

要点：

- **初始调用不计入重试次数**。`max_retries=3` 表示最多 4 次调用。
- **先封顶再抖动**。若先抖动再封顶，抖动会被削平，实际退避序列不再是设计值。
- **抖动用于避免惊群**：同一批请求在同一时刻失败时，不加抖动会同时重试、再次把上游打垮。
- 退避序列（`base=500`，无抖动）：`500 → 1000 → 2000 → 4000 …`

### 供应商主动给出的延迟优先

`retry_provider_request` 优先采用供应商的 `retry-after-ms`（毫秒）或 `retry-after`（秒 / HTTP-date），解析失败才回退到指数退避。`max_retry_delay_ms` 可再对结果封顶。

## 3. 不可重试的错误立即失败

认证失败、配额耗尽、参数非法**立即抛出，不做无谓等待**。`retry_provider_request` 的结构保证了这一点：

```
while True:
    try:     return await fn()
    except CancelledError:  raise
    except Exception as exc:
        if attempt >= max_attempts or not is_retryable_provider_error(exc):
            raise                       # 立即失败
        attempt += 1
        delay = retry_after_ms(exc) or retry_delay_ms(policy, attempt)
        on_retry(attempt, delay, exc)
        await clock.sleep(delay)        # 走注入的 Clock
```

配额耗尽类错误排在可重试正则**之前**判定（见 [error-codes.md](./error-codes.md) 第 2 节），否则消息里的 `429` / `rate limit` 字样会把它误判成可重试。

## 4. 可中断

所有睡眠都走注入的 `Clock` 协议：

| 实现 | 行为 |
|---|---|
| `RealClock` | `asyncio.sleep` |
| `FakeClock` | 立即返回，把延迟追加到 `sleeps: list[float]` |

因此**重试测试零真实等待**，断言的是退避序列本身而不是"跑得快"：

```python
assert clock.sleeps == [500, 1000, 2000]
```

退避期间被取消（`is_cancelled()` 返回真）时，结果归一为 `stop_reason="aborted"` 的消息，调用方无需区分"取消发生在调用期间"还是"取消发生在退避期间"。

取消（`aborted`）**永不重试**。

## 5. 回调

`RetryCallbacks` 供落库与可观测性使用：

| 回调 | 时机 | 参数 |
|---|---|---|
| `on_retry_scheduled` | 每次退避睡眠**之前** | `attempt, max_attempts, delay_ms, error` |
| `on_retry_attempt_start` | 退避结束、重试调用**之前** | — |
| `on_retry_finished` | 循环结束时**一次** | `success, retries, error` |

回调可以是同步或异步函数，`_notify` 统一处理。

## 6. 已流式输出的处理

这是需求中唯一画了流程图的分支。**文字版**（对应需求文档的 `flowchart TD`）：

```
开始：流式请求
  └─ Gateway 调用 LLM 流式接口
       └─ 流是否正常完成？（收到 finish_reason / [DONE] / message_stop）
            ├─ 是 → 向客户端发送完成事件 / [DONE]
            │        └─ 记录日志、计费、结束
            └─ 否 → 中断来源？
                 ├─ 客户端断开
                 │    └─ 取消上游 LLM 请求
                 │         └─ 清理资源、释放并发槽
                 │              └─ 记录日志，结束
                 └─ 上游 LLM 中断
                      └─ Gateway 是否已向客户端发送过数据？
                           ├─ 否 → 是否可重试错误？（网络 / 5xx / 429）
                           │        ├─ 否 → 向客户端返回错误
                           │        │        └─ 记录日志、结束
                           │        └─ 是 → 是否有已执行的不可逆副作用？（下单、扣款、发邮件）
                           │                 ├─ 是 → 不能简单重试；走幂等 / 补偿 / 报错
                           │                 │        └─ 向客户端返回错误
                           │                 │             └─ 记录日志、结束
                           │                 └─ 否 → 有限重试上游
                           │                          └─ 回到「Gateway 调用 LLM 流式接口」
                           └─ 是 → 不能透明重试整个请求
                                    └─ 发送流内 error 事件，不发送 [DONE]
                                         └─ 关闭连接
                                              └─ 记录日志：request_id、已发送字节、最后 chunk、错误
                                                   └─ 结束；由客户端决定重试 / 接受部分内容
```

### 实现对应

| 流程图节点 | 实现 |
|---|---|
| "流是否正常完成" | `StreamAssembler.recorded_stop_reason` —— `finish_reason` / `[DONE]` / `message_stop` 任一即算完成 |
| "客户端断开 → 取消上游" | `GatewayService.stream_sse` 捕获 `CancelledError` → `stream.cancel()` → 取消生产者任务 → 落 `cancelled` 记录 |
| "是否已向客户端发送过数据" | `StreamingRetryGuard.can_retry` —— 只看**业务 delta**（`text_delta` / `thinking_delta` / `toolcall_delta`），`start` 不算 |
| "发送流内 error 事件，不发送 [DONE]" | `encode_event()` —— 只有 `done` 追加 `data: [DONE]` |
| "记录 request_id、已发送字节、最后 chunk、错误" | `StreamingRetryGuard.snapshot()` |
| "不可逆副作用" | 网关侧不执行副作用（那是 agent 的职责），因此网关的判定只到"已输出数据 → 不可透明重试"这一层；带副作用的 agent 需要自己用 `task_id` 做幂等 |

### `StreamingRetryGuard`

```python
guard = StreamingRetryGuard(request_id="...")
guard.record_delta("你")        # first_delta_sent=True, bytes_sent=3, last_chunk="你"
guard.can_retry                 # False —— 已经吐过字，重试会产出重复内容
guard.record_error("...")
guard.snapshot()                # {"request_id": ..., "first_delta_sent": True, "bytes_sent": 3, "last_chunk": "你", "error": "..."}
```

`Router.stream()` 干脆**不做跨模型降级**：一旦开始吐字再换模型重来会产出重复内容。降级决策只存在于非流式的 `Router.execute()`。

## 7. 降级（degrade）

`Router.execute()` 遍历 `[主路由] + [备用路由]`，按错误码的**处置决策**决定下一步：

```python
for index, model in enumerate(models):
    message = await self._attempt(model, task, decision.profile, trace)
    if message.stop_reason != "error":
        return message

    error_decision = decision_for(_code_from(message))
    if error_decision.disposition is ErrorDisposition.FAIL:
        return message                      # 报错：确定性失败，换模型也不会变好

    if index + 1 < len(models):
        trace.fallback = True               # 降级：换路由表中下一个模型
        trace.warnings.append(f"{code}: 已降级 ...；{error_decision.strategy}")
        continue
    return message                          # 没有下一个模型，只能返回错误
```

三种处置的差别：

| 处置 | 行为 |
|---|---|
| `retry` | 先在**同一模型**内有限重试（`max_retries`，指数退避），耗尽后按路由换模型 |
| `degrade` | 不重试当前模型，直接换路由表中下一个 |
| `fail` | 立即返回错误，不做任何后续动作 |

**认证失败（`AUTH_INVALID`）现在会降级**：需求「处理策略」列要求"如果路由表中还有下一个模型，就更换路由表中下一个模型，并报错，但不阻塞"。实现上"报错"体现为响应里的 `warnings` 与落库的 `disposition=degrade`，请求本身继续正常返回，不会被阻塞。

**内容拒答（`CONTENT_REFUSED`）也降级**：需求要求"更换模型重试，但是每个模型最多尝试一次"。候选链只有主备两个模型、且每个模型只尝试一次，因此"每模型最多一次"天然成立。

执行事实经 `ExecutionTrace` 回传，落库时体现为 `CallRecord.resilience` 的 `attempt` / `retry` / `fallback` / `disposition`，并同时写进 `requests.payload`，Trace 页的「弹性」分组可直接查看。

## 8. 配置入口

重试开关与最大次数是 **gwprofile 的字段**，在 Web 界面的「Profile」页配置：

```
GET  /api/profiles          →  [{..., "retry_enabled": true, "max_retries": 3}, ...]
POST /api/profiles          →  同结构，写库并立即对后续请求生效
PUT  /api/profiles/{name}   →  同结构，写库并立即对后续请求生效
```

task 里指定 `profile` 时用该 profile 的重试策略；未指定时用 `default` profile；一个 profile 都没配时用 router 默认策略。配置通过 `Storage` 的 `config` 表持久化，进程重启后由 `restore_config()` 恢复。

此外 `advanced.max_tool_rounds` 是**请求级护栏**（不属于重试策略）：工具调用轮数超限返回 `TOOL_ROUNDS_EXCEEDED`，处置为 `fail`，在调用上游之前生效。
