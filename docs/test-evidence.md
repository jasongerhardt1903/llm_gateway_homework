# 测试证据

## 1. 全量运行

```bash
$ python3 -m pytest tests -p no:cacheprovider --cov=llm_gw --cov-report=term-missing
```

```
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 84%]
........................................                                 [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.9-final-0 _______________

Name                                             Stmts   Miss  Cover   Missing
------------------------------------------------------------------------------
llm_gw/__init__.py                                   1      0   100%
llm_gw/adapter/__init__.py                           0      0   100%
llm_gw/adapter/base.py                             294     29    90%   143, 172, 175, 184, 201, 207, 209, 223, 264, 266, 268, 292, 304, 331, 356, 430, 455, 476-482, 521, 527-530
llm_gw/adapter/factory.py                           14      0   100%
llm_gw/adapter/presets/__init__.py                   0      0   100%
llm_gw/adapter/presets/anthropic.py                  8      0   100%
llm_gw/adapter/presets/deepseek.py                   8      0   100%
llm_gw/adapter/presets/openai.py                     8      0   100%
llm_gw/adapter/presets/registry.py                  39      4    90%   47, 51, 65, 69
llm_gw/adapter/protocols/__init__.py                 0      0   100%
llm_gw/adapter/protocols/anthropic_messages.py     177     20    89%   138, 149, 163-164, 166, 222-223, 230, 232-233, 269, 280, 293-299, 305
llm_gw/adapter/protocols/openai_compat.py          167     12    93%   121-122, 147, 191-193, 199, 224-225, 277, 281-282
llm_gw/adapter/structured.py                       134     25    81%   55-61, 128, 132, 135-137, 149, 151, 154, 164-165, 181-182, 232-233, 236, 239, 243, 268
llm_gw/adapter/transform.py                         80      7    91%   81, 117-121, 136
llm_gw/core/__init__.py                              0      0   100%
llm_gw/core/errors.py                              249     39    84%   140, 192-193, 213-215, 225-226, 234, 238, 242-246, 288-290, 293, 307-308, 319, 338, 342, 355, 367-368, 426, 434, 441, 443, 447, 450-451, 456-459, 493, 519
llm_gw/core/events.py                              155      7    95%   207, 244-245, 285, 292, 302, 315
llm_gw/core/json_utils.py                          191     14    93%   45, 141-142, 145, 184-185, 188, 197, 263-264, 267, 279, 289, 306
llm_gw/core/messages.py                            120      2    98%   182, 184
llm_gw/core/schema.py                              113      6    95%   111, 142, 144, 166, 205-206
llm_gw/core/telemetry.py                            56      0   100%
llm_gw/harness/__init__.py                           0      0   100%
llm_gw/harness/decisions.py                         15      2    87%   50-51
llm_gw/harness/query.py                             20      0   100%
llm_gw/harness/retry.py                            127     18    86%   97-98, 103-113, 135, 144, 146, 173, 200
llm_gw/harness/service.py                          108      6    94%   169-173, 205
llm_gw/harness/sse.py                               80      8    90%   97-103, 126
llm_gw/harness/storage.py                          126      7    94%   132, 145, 148, 152, 253, 318, 382
llm_gw/router/__init__.py                            0      0   100%
llm_gw/router/registry.py                           57      3    95%   71, 80, 100
llm_gw/router/router.py                             62      9    85%   57, 61-63, 83, 108-111
llm_gw/router/rules.py                              61      0   100%
llm_gw/runtime.py                                   45      4    91%   91-94
llm_gw/util/__init__.py                              0      0   100%
llm_gw/util/clock.py                                22      3    86%   31, 34-35
llm_gw/web/__init__.py                               0      0   100%
llm_gw/web/api_models.py                            55      0   100%
llm_gw/web/app.py                                  156     15    90%   83, 126, 137, 145, 185-186, 228, 275, 277, 279, 281, 283, 285, 296, 303
------------------------------------------------------------------------------
TOTAL                                             2748    240    91%
256 passed in 1.78s
```

**256 passed，0 failed，91% 覆盖率。**

## 2. 按文件分布

| 文件 | 用例数 |
|---|---|
| `tests/adapter/test_adapter_contract.py` | 19 |
| `tests/adapter/test_streaming.py` | 22 |
| `tests/adapter/test_structured_output.py` | 19 |
| `tests/adapter/test_transform.py` | 7 |
| `tests/core/test_errors.py` | 45 |
| `tests/core/test_events.py` | 8 |
| `tests/core/test_json_utils.py` | 15 |
| `tests/core/test_messages.py` | 7 |
| `tests/core/test_schema.py` | 15 |
| `tests/core/test_telemetry.py` | 5 |
| `tests/router/test_retry.py` | 21 |
| `tests/router/test_router.py` | 17 |
| `tests/harness/test_observability.py` | 15 |
| `tests/harness/test_service.py` | 11 |
| `tests/web/test_web_api.py` | 19 |
| `tests/test_phase0_infra.py` | 4 |
| `tests/test_runtime.py` | 7 |
| **合计** | **256** |

## 3. 需求要求的六类测试

需求第 137–144 行规定了至少六类测试。逐条对应：

| 需求类别 | 文件 | 覆盖内容 | 代表用例 |
|---|---|---|---|
| Adapter Contract Test | `tests/adapter/test_adapter_contract.py` | 每个 adapter 的**请求翻译**与**响应翻译**，由 `httpx.MockTransport` 驱动，不依赖真实供应商 | 逐协议断言请求体字段（`response_format`、`tool_choice`、`stream_options.include_usage`）、响应翻译为统一消息 |
| router 测试 | `tests/router/test_router.py` | 能力匹配、优先级、fallback 顺序、候选拒绝原因 | `test_static_order_is_not_reordered_by_cost`、`test_missing_tools_capability_is_rejected_with_reason`、`test_execute_falls_back_to_backup_on_transient_error`、`test_execute_does_not_fallback_on_non_retryable_error`、`test_decision_table_matches_requirement` |
| 重试测试 | `tests/router/test_retry.py` | 用 mocktransport 模拟超时与错误，验证重试次数与退避策略，**不真的 sleep** | `test_retries_transient_error_with_backoff`（`assert clock.sleeps == [500, 1000]`）、`test_non_retryable_error_fails_fast`（`assert clock.sleeps == []`）、`test_provider_request_respects_retry_after_header`（`assert clock.sleeps == [300]`） |
| streaming 测试 | `tests/adapter/test_streaming.py` | SSE 事件序列正确性：delta 顺序、usage 事件、终态、错误中断行为 | delta 顺序、usage 事件位置、`test_second_terminal_push_is_rejected`、中途错误中断 |
| structured output 测试 | `tests/adapter/test_structured_output.py` | JSON 提取、Schema 校验、**修复尝试次数**、失败时的错误返回 | JSON 围栏剥离、修复次数上限、超限抛 `OUTPUT_SCHEMA_INVALID`、流式终校验 |
| 可观测性 | `tests/harness/test_observability.py` | trace / Metrics / Cost Ledger 是否正确记录每次调用的关键字段 | TTFT 口径、时间窗指标、trace 链路顺序、脱敏生效 |

额外补充（计划中未强制、但覆盖了关键不变式）：

| 文件 | 覆盖内容 |
|---|---|
| `tests/harness/test_service.py` | 客户端断连取消上游、流内 error 不发 `[DONE]`、已流式输出后不盲目重生成 |
| `tests/core/test_events.py` | 单一终态不变式、`end` 后 push 丢弃 |
| `tests/web/test_web_api.py` | 模型 CRUD、路由配置往返、Dashboard、Trace 搜索、Chat SSE 代理 |
| `tests/test_runtime.py` | 组合根：lifespan 挂载、配置跨重启恢复、根挂载不吞 404 |
| `tests/test_phase0_infra.py` | `FakeClock` 不等待、`sse_transport` 重放分片、`scripted_transport` 按序返回错误 |

## 4. "不能真的 sleep" 的证明

重试测试全部注入 `FakeClock`，断言的是**退避序列**而非耗时：

```python
async def test_retries_transient_error_with_backoff(clock):
    ...
    assert clock.sleeps == [500, 1000]
```

整个 `tests/router/test_retry.py`（21 个用例，含"重试 3 次""退避封顶""退避期间取消"）与全量 256 个用例一起在 **1.78 秒**内跑完——若存在真实退避等待，仅退避序列 `500+1000+2000` 就会超过 3.5 秒。

窗口类指标同理：`Storage(now=...)` 的时间来自注入函数，`tests/harness/test_observability.py` 用 `now.value - 120` 精确构造"2 分钟前的记录"，不依赖 `time.sleep`。

## 5. 前端冒烟

```bash
$ cd webapp && npm test
```

```
 RUN  v2.1.9 webapp

 ✓ src/__tests__/smoke.test.jsx (5 tests) 7ms

 Test Files  1 passed (1)
      Tests  5 passed (5)
```

覆盖：四个页签渲染、默认页、SSE 事件解析（含**事件被拆到两个网络分片**的场景）、`[DONE]` 不作为业务事件透出、`error` 终态仍交付、HTTP 错误抛出后端 `detail`。

## 6. 端到端验证（真实进程）

启动真实服务并验证控制台与可观测性链路：

```bash
$ LLM_GW_DB=/tmp/gw_e2e.sqlite3 \
  python3 -m uvicorn llm_gw.runtime:create_runtime_app --factory --port 8011
```

| 验证项 | 命令 | 结果 |
|---|---|---|
| 前端托管 | `curl -o /dev/null -w "%{http_code}" /` | `200`（返回构建产物 `index.html`） |
| 供应商下拉数据源 | `curl /api/providers` | 3 个供应商：`openai` / `deepseek` / `anthropic` |
| 模型清单 | `curl /api/models` | 6 个 preset 模型 |
| 保存路由配置 | `PUT /api/routes`（`max_retries=5`） | `200`，`GET` 回读一致 |
| **配置跨重启持久化** | 重启进程后 `GET /api/routes` | 仍为 `max_retries=5`；新建的模型 `local-faux` 也在清单中 |
| Chat SSE 代理 | `POST /api/chat/stream` | 返回 `event: error` 终态 |
| **错误终态不发 `[DONE]`** | 对同一响应 `grep -c DONE` | `0` |
| Trace 落库 | `GET /api/traces?limit=3` | 2 条记录，`ts` / `terminal=error` / `error_code=UPSTREAM_OVERLOADED` / `total_ms` / `ttft_ms` 齐全 |
| 关键字搜索 | `GET /api/traces?q=local-faux` | 命中 2 条 |

## 7. 复现方式

```bash
# 后端（含覆盖率）
python3 -m pytest tests -q --cov=llm_gw

# 前端
cd webapp && npm install && npm test

# 起服
uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000
```

所有 adapter 测试由 `httpx.MockTransport` 驱动，重试测试由 `FakeClock` 驱动——**不需要任何真实供应商密钥或网络访问**即可跑完全部 256 个用例。
