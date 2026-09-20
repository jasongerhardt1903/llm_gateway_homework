# 测试证据

> 本文件是 **v0.7.0** 的存档。v0.7.0 为 agent 接口（`/v1/tasks`、`/v1/tasks:stream`）
> 加上简单口令鉴权，并新增 `AUTH_REQUIRED` 错误码；后端用例由 343 增至 **351**
> （`tests/harness/test_agent_auth.py` +7、`tests/test_runtime.py` +1），覆盖率 92%。
> v0.6.0 把 agent API（`/health`、`/v1/tasks`、`/v1/tasks:stream`）
> 挂进运行时组合根，后端用例由 342 增至 **343**（`tests/test_runtime.py` +1），覆盖率 92%。
> v0.5.0 为**纯前端**改动（控制台整体重做：Tailwind CSS v4 + shadcn 风格组件原语 +
> TanStack Table + Recharts + lucide-react），后端用例数不变（342），前端冒烟仍为 8 例
> 且**断言未做任何修改**（改造前已通过，改造后继续通过）。
> v0.4.0 为**纯前端**改动（Trace 页任务视图瀑布图），前端冒烟由 5 例增至 8 例
> （`webapp/src/__tests__/smoke.test.jsx` +3）。
> v0.3.0 相比 v0.2.0（336 例）新增 6 例：错误处置三选一
> 决策表与降级链（`tests/router/test_router.py` +3）、认证降级落库与非阻塞告警
> （`tests/harness/test_service.py` +3）。v0.2.0 相比 v0.1.0（256 例）新增 80 例：
> gwprofile 数据模型与解析（29）、profile 作用域路由与工具轮数护栏（24）、
> 高级配置注入请求体（14）、Web profile API 与密钥脱敏（+9）、旧库迁移与
> 密钥优先级（+4）。

## 1. 全量运行

```bash
$ .venv/bin/python -m pytest tests -p no:cacheprovider --cov=llm_gw --cov-report=term-missing
```

```
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 63%]
........................................................................ [ 84%]
......................................................                   [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.9-final-0 _______________

Name                                             Stmts   Miss  Cover   Missing
------------------------------------------------------------------------------
llm_gw/__init__.py                                   1      0   100%
llm_gw/adapter/__init__.py                           0      0   100%
llm_gw/adapter/base.py                             311     28    91%   146, 175, 178, 187, 204, 210, 212, 226, 267, 269, 271, 295, 307, 334, 359, 486, 507-513, 552, 558-561
llm_gw/adapter/factory.py                           14      0   100%
llm_gw/adapter/presets/__init__.py                   0      0   100%
llm_gw/adapter/presets/anthropic.py                  8      0   100%
llm_gw/adapter/presets/deepseek.py                   8      0   100%
llm_gw/adapter/presets/openai.py                     8      0   100%
llm_gw/adapter/presets/registry.py                  39      3    92%   51, 65, 69
llm_gw/adapter/protocols/__init__.py                 0      0   100%
llm_gw/adapter/protocols/anthropic_messages.py     181     20    89%   164, 175, 189-190, 192, 248-249, 256, 258-259, 295, 306, 319-325, 331
llm_gw/adapter/protocols/openai_compat.py          169     12    93%   133-134, 159, 203-205, 211, 236-237, 289, 293-294
llm_gw/adapter/structured.py                       134     25    81%   55-61, 128, 132, 135-137, 149, 151, 154, 164-165, 181-182, 232-233, 236, 239, 243, 268
llm_gw/adapter/transform.py                         80      7    91%   81, 117-121, 136
llm_gw/core/__init__.py                              0      0   100%
llm_gw/core/advanced.py                             35      3    91%   55, 77-78
llm_gw/core/errors.py                              251     38    85%   200, 252-253, 273-275, 285-286, 294, 298, 302-306, 348-350, 353, 367-368, 379, 398, 402, 415, 427-428, 494, 501, 503, 507, 510-511, 516-519, 553, 579
llm_gw/core/events.py                              155      7    95%   207, 244-245, 285, 292, 302, 315
llm_gw/core/json_utils.py                          191     14    93%   45, 141-142, 145, 184-185, 188, 197, 263-264, 267, 279, 289, 306
llm_gw/core/messages.py                            124      2    98%   192, 194
llm_gw/core/schema.py                              115      6    95%   111, 142, 144, 175, 214-215
llm_gw/core/telemetry.py                            57      0   100%
llm_gw/harness/__init__.py                           0      0   100%
llm_gw/harness/decisions.py                         14      1    93%   53
llm_gw/harness/query.py                             20      0   100%
llm_gw/harness/retry.py                            128     18    86%   98-99, 104-114, 136, 145, 147, 174, 207
llm_gw/harness/service.py                          115      6    95%   193-197, 229
llm_gw/harness/sse.py                               80      8    90%   97-103, 126
llm_gw/harness/storage.py                          133      7    95%   134, 161, 164, 168, 269, 334, 398
llm_gw/router/__init__.py                            0      0   100%
llm_gw/router/profile.py                            68      4    94%   79, 104, 140, 154
llm_gw/router/registry.py                           60      3    95%   75, 91, 111
llm_gw/router/router.py                            120     13    89%   106, 110-112, 136, 141-142, 166, 179, 210, 244, 248-249
llm_gw/router/rules.py                              75      0   100%
llm_gw/runtime.py                                   49      1    98%   108
llm_gw/util/__init__.py                              0      0   100%
llm_gw/util/clock.py                                22      3    86%   31, 34-35
llm_gw/web/__init__.py                               0      0   100%
llm_gw/web/api_models.py                            93      1    99%   142
llm_gw/web/app.py                                  182     19    90%   88, 126, 129-130, 150, 161, 169, 209-210, 247, 255, 303, 305, 307, 309, 311, 313, 324, 331
------------------------------------------------------------------------------
TOTAL                                             3040    249    92%
343 passed in 1.41s
```

**343 passed，0 failed，92% 覆盖率。**

本轮改动的模块覆盖率：`core/telemetry.py` 100%、`harness/decisions.py` 93%、
`router/router.py` 89%、`harness/service.py` 95%、`core/errors.py` 85%。

## 2. 按文件分布

| 文件 | 用例数 | 本轮变化 |
|---|---|---|
| `tests/adapter/test_adapter_contract.py` | 19 | |
| `tests/adapter/test_advanced_config.py` | 14 | |
| `tests/adapter/test_streaming.py` | 22 | |
| `tests/adapter/test_structured_output.py` | 19 | |
| `tests/adapter/test_transform.py` | 7 | |
| `tests/core/test_errors.py` | 45 | |
| `tests/core/test_events.py` | 8 | |
| `tests/core/test_json_utils.py` | 15 | |
| `tests/core/test_messages.py` | 7 | |
| `tests/core/test_schema.py` | 15 | |
| `tests/core/test_telemetry.py` | 5 | +`disposition` 断言 |
| `tests/harness/test_observability.py` | 17 | +`disposition` 断言 |
| `tests/harness/test_service.py` | 14 | **+3（认证降级落库 / warnings / 成功无处置）** |
| `tests/router/test_profile.py` | 29 | |
| `tests/router/test_profile_routing.py` | 24 | |
| `tests/router/test_retry.py` | 21 | |
| `tests/router/test_router.py` | 20 | **+3（三选一决策表与降级链）** |
| `tests/web/test_web_api.py` | 28 | |
| `tests/test_phase0_infra.py` | 4 | |
| `tests/test_runtime.py` | 10 | **+1（agent API 与控制台同进程共存）** |
| **合计** | **343** | **+1** |

## 3. 需求要求的六类测试

需求第 137–144 行规定了至少六类测试。逐条对应：

| 需求类别 | 文件 | 覆盖内容 | 代表用例 |
|---|---|---|---|
| Adapter Contract Test | `tests/adapter/test_adapter_contract.py`、`test_advanced_config.py` | 每个 adapter 的**请求翻译**与**响应翻译**，由 `httpx.MockTransport` 驱动，不依赖真实供应商 | 逐协议断言请求体字段（`response_format`、`tool_choice`、`stream_options.include_usage`）、高级配置注入（`temperature` / `top_p` / `top_k` / `thinking`）、`extra_body` 覆盖 |
| router 测试 | `tests/router/test_router.py`、`test_profile_routing.py` | profile 作用域、能力匹配、静态顺序主备、**错误处置三选一（重试 / 降级 / 报错）**、候选拒绝原因 | `test_static_order_is_not_reordered_by_cost`、`test_profile_scopes_candidates`、`test_unknown_profile_fails_fast`、`test_decision_table_matches_requirement`、`test_execute_falls_back_to_backup_on_transient_error`（`trace.attempts == 5` / `trace.retries == 3`）、`test_execute_degrades_on_auth_failure_with_warning`、`test_execute_degrades_on_content_refusal_once_per_model`、`test_execute_does_not_degrade_on_request_invalid`、`test_execute_retries_then_degrades_after_max_retries` |
| 重试测试 | `tests/router/test_retry.py`、`test_profile_routing.py` | 用 mocktransport 模拟超时与错误，验证重试次数与退避策略，**不真的 sleep** | `test_retries_transient_error_with_backoff`（`assert clock.sleeps == [500, 1000]`）、`test_non_retryable_error_fails_fast`（`assert clock.sleeps == []`）、`test_provider_request_respects_retry_after_header`（`assert clock.sleeps == [300]`）、per-profile 策略 |
| streaming 测试 | `tests/adapter/test_streaming.py` | SSE 事件序列正确性：delta 顺序、usage 事件、终态、错误中断行为 | delta 顺序、usage 事件位置、`test_second_terminal_push_is_rejected`、中途错误中断 |
| structured output 测试 | `tests/adapter/test_structured_output.py` | JSON 提取、Schema 校验、**修复尝试次数**、失败时的错误返回 | JSON 围栏剥离、修复次数上限、超限抛 `OUTPUT_SCHEMA_INVALID`、流式终校验 |
| 可观测性 | `tests/harness/test_observability.py`、`test_service.py` | trace / Metrics / Cost Ledger 是否正确记录每次调用的关键字段，含**处置与降级事实落库** | TTFT 口径、时间窗指标、trace 链路顺序、脱敏生效、`profile` 落库、旧库迁移、`test_auth_failure_degrades_and_records_resilience`（`attempt == 2` / `fallback == 1` / `disposition == "degrade"`）、`test_successful_call_records_no_disposition`、`test_post_task_response_carries_warnings` |

额外补充（计划中未强制、但覆盖了关键不变式）：

| 文件 | 覆盖内容 |
|---|---|
| `tests/router/test_profile.py` | 逗号分隔顺序解析（含全角逗号与顿号、去重保序）、模版判定矩阵、`AdvancedConfig` 范围校验 |
| `tests/router/test_profile_routing.py` | `TOOL_ROUNDS_EXCEEDED` 护栏（含边界值：等于上限放行、超一即拒）、模版值参与护栏判定 |
| `tests/harness/test_service.py` | 客户端断连取消上游、流内 error 不发 `[DONE]`、已流式输出后不盲目重生成、认证失败降级不阻塞且落库、`warnings` 随响应返回 |
| `tests/core/test_events.py` | 单一终态不变式、`end` 后 push 丢弃 |
| `tests/web/test_web_api.py` | 模型 CRUD、**密钥只写不回显 / 缺省不修改 / 空串清除**、profile CRUD 往返、Dashboard、Trace 搜索、Chat SSE 代理 |
| `tests/test_runtime.py` | 组合根：lifespan 挂载、配置跨重启恢复、根挂载不吞 404、**agent API 与控制台同进程共存**、**密钥优先级（模型 > 环境变量）** |
| `tests/test_phase0_infra.py` | `FakeClock` 不等待、`sse_transport` 重放分片、`scripted_transport` 按序返回错误 |

## 4. "不能真的 sleep" 的证明

重试测试全部注入 `FakeClock`，断言的是**退避序列**而非耗时：

```python
async def test_retries_transient_error_with_backoff(clock):
    ...
    assert clock.sleeps == [500, 1000]
```

整个 `tests/router/test_retry.py`（21 个用例，含"重试 3 次""退避封顶""退避期间取消"）与全量 342 个用例一起在 **1.32 秒**内跑完——若存在真实退避等待，仅退避序列 `500+1000+2000` 就会超过 3.5 秒。

窗口类指标同理：`Storage(now=...)` 的时间来自注入函数，`tests/harness/test_observability.py` 用 `now.value - 120` 精确构造"2 分钟前的记录"，不依赖 `time.sleep`。

## 5. 前端冒烟

```bash
$ cd webapp && npm test
```

```
 RUN  v2.1.9 webapp

 ✓ src/__tests__/smoke.test.jsx (8 tests) 8ms

 Test Files  1 passed (1)
      Tests  8 passed (8)
```

覆盖：五个页签渲染（含 Profile 页）、默认页、SSE 事件解析（含**事件被拆到两个网络分片**的场景）、`[DONE]` 不作为业务事件透出、`error` 终态仍交付、HTTP 错误抛出后端 `detail`；以及 v0.4.0 新增的任务瀑布图三例——按 `run_id` 分组（空值归入「未标记任务」、组内时间正序且不改动入参）、条宽相对全局最长调用与 TTFT 占比（含 `total_ms=0` 的最小宽度与除零保护）、分组渲染的汇总文案与终态配色。

v0.5.0 的 UI 重做**没有改动测试文件**：瀑布图的 `.waterfall-bar` / `.waterfall-ok|bad|warn`
与 `.row-selected` 类名在 Tailwind 组件层里被原样保留，因此这三例继续作为"语义契约"生效。

构建产物同样验证过：

```bash
$ cd webapp && npm run build
dist/index.html                   0.40 kB │ gzip:   0.29 kB
dist/assets/index-Dgw8S1gS.css   20.63 kB │ gzip:   5.10 kB
dist/assets/index-DzaioPZR.js   658.09 kB │ gzip: 195.64 kB
✓ built in 2.05s
```

> 体积增长来自 Recharts / TanStack Table / lucide-react；后端仍以同源方式托管
> `webapp/dist`，未新增任何运行时服务。

## 6. 端到端验证（真实进程）

### 6.1 v0.2.0 存档

用 `LLM_GW_DB=/tmp/gw_e2e_v3.sqlite3` 起真实服务（端口 8012），逐项验证：

| 验证项 | 命令 | 结果 |
|---|---|---|
| 前端托管 | `GET /` | `200`（返回构建产物 `index.html`） |
| 供应商下拉数据源 | `GET /api/providers` | `['openai', 'deepseek', 'anthropic']` |
| 模型清单 | `GET /api/models` | 6 个 preset 模型 |
| **密钥不回显** | `GET /api/models` 检查 `api_key` | 全部为 `null`；`api_key_set` 字段存在 |
| 建 dynamic profile | `POST /api/profiles` | `200` |
| 建 static profile | `POST /api/profiles`（`static_order` 为列表） | `200`，回读顺序与提交一致 |
| 非法 `route_mode` | `POST /api/profiles`（`route_mode=random`） | `422` |
| **profile 定作用域** | `POST /api/chat`（`profile=fast`） | 路由到 `openai/gpt-4o-mini`，因未配密钥返回 `AUTH_INVALID`（而非 `ROUTE_NO_CANDIDATE`） |
| **不存在的 profile 快速失败** | `POST /api/chat`（`profile=no-such`） | `ROUTE_NO_CANDIDATE: profile no-such 不存在` |
| **profile 落库** | `GET /api/traces?limit=5` | 记录里 `profile='fast'`、`model='gpt-4o-mini'` |
| Trace 不含密钥 | 对 Trace 响应体 grep `api_key` | 未出现 |
| **配置跨重启持久化** | 重启进程后 `GET /api/profiles` | `[('fast', 2, 'dynamic', []), ('prod', 0, 'static', ['deepseek/deepseek-chat', 'openai/gpt-4o-mini'])]` |
| 历史记录跨重启可查 | 重启后 `GET /api/traces?limit=5` | 2 条仍在 |
| 删除 profile | `DELETE /api/profiles/prod` | `200`；重复删除 `404` |
| 错误终态不发 `[DONE]` | 对 SSE 响应 `grep -c DONE` | `0` |
| 服务端无异常 | `grep -ic traceback` | `0` |

### 6.2 v0.3.0 新增验证：错误处置三选一

起两个进程：一个**假上游**（`/tmp/gw_stub_upstream.py`，OpenAI 兼容的最小实现，
密钥含 `good-key` 返回 200、否则返回 401），一个真实网关
（`LLM_GW_DB=/tmp/gw_e2e_v3.sqlite3`，端口 8037）。

注册两个模型都指向假上游，只有密钥不同；建静态 profile
`static_order = [openai/stub-bad, openai/stub-good]`。这样"主模型认证失败"是
**确定性**的，不依赖真实供应商。

| 验证项 | 命令 | 结果 |
|---|---|---|
| 认证失败**不阻塞** | `POST /api/chat`（`profile=degrade-demo`） | `terminal=done`、`stop_reason=stop`，`text` 为**备用模型**的回答 |
| **降级事实落库** | `GET /api/traces?limit=5` | `attempt=2`、`retry=0`、`fallback=1`、`disposition='degrade'`、`model='stub-bad'` |
| **非阻塞告警随响应返回** | `POST /v1/tasks`（同一 profile） | `stop_reason=stop`、`terminal=done`，`warnings` 含 `"AUTH_INVALID: 已降级 openai/stub-bad → openai/stub-good；更换路由表中下一个模型并告警，不阻塞；没有下一个模型则返回错误。"` |
| **请求验证不降级** | `POST /api/chat`（`messages` 类型非法） | `422`；`/api/tasks:validate` 返回 `{"code":"REQUEST_INVALID", ...}` |
| 无多余降级记录 | 库中 `SELECT COUNT(*) FROM requests` | 仍为 `1`——非法请求在进入路由前即被拒绝，未产生任何 `fallback` 记录 |
| 成功调用无处置 | 该次成功记录的 `disposition` | 落库的降级记录 `disposition='degrade'`；`tests/harness/test_service.py` 另断言成功完成时为 `""` |

> **v0.6.0 已修复**：agent API 现已挂载进 `llm_gw.runtime.create_runtime_app`——组合根先
> `include_router(agent_router(service))` 再 `mount("/", console)`，因此 `/health` 与
> `/v1/tasks*` 在真实进程里可达（见第 6.4 节）。上面 `warnings` 验证当时用的临时入口
> （`/tmp/gw_e2e_runtime.py`）已不再需要，该脚本未纳入仓库。

### 6.3 旧库迁移（v0.1.0 的库 → v0.2.0）

用 v0.1.0 的 `requests` 表结构（路由维度列名 `logical_model`，payload 里也是该键）生成
`/tmp/gw_legacy3.sqlite3`，再用 v0.2.0 启动：

| 验证项 | 结果 |
|---|---|
| 迁移前列名 | `['ts', 'logical_model', 'provider', 'model']` |
| 迁移后列名 | `['ts', 'profile', 'provider', 'model']`（自动 `ALTER TABLE ... RENAME COLUMN`） |
| 历史记录可查 | `GET /api/traces?limit=5` → 1 条，`call_id=legacy-c1`、`model=gpt-4o-mini` |
| 链路详情可查 | `GET /api/traces/t-legacy` → `200` |
| 服务端异常 | `grep -ic traceback` → `0` |

> 已知局限（记录在案，未在本次修复）：迁移只改**列名**，不改历史行 payload JSON 里的键。
> 因此 v0.1.0 期间产生的旧记录在 Trace 页里 Profile 一栏显示为空（payload 里仍是
> `logical_model`），不影响查询与展示其余字段。

> 已知局限（v0.3.0 期间发现，**未在本次修复**）：模型的 `api_key` 只存在于内存注册表，
> 不随 `config` 表持久化——`web/app.py` 的 `_persist_models` 用的是
> `model_to_payload(model)`，而该函数刻意把 `api_key` 置为 `None`（"只写不回显"），
> 于是密钥写库时也一并被抹掉，进程重启后模型密钥为空、回退到环境变量。
> 这与 CHANGELOG / README 里"密钥写入 SQLite"的表述不符，属于 v0.2.0 遗留缺陷，
> 与本次错误处置改造无关，因此只记录不改动。

### 6.4 agent API 与控制台同进程（v0.6.0）

v0.6.0 把 agent 路由挂回运行时组合根——`include_router(agent_router(service))` 必须
先于 `mount("/", console)`，否则会被兜底挂载吃掉。用真实 uvicorn 进程验证：

```bash
$ LLM_GW_DB=/tmp/gw_verify.sqlite3 \
    .venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory \
    --host 127.0.0.1 --port 8010
```

| 请求 | 结果 |
|---|---|
| `GET /health` | `200`，body `{"status":"ok"}` |
| `POST /v1/tasks`（body `not-json`） | `400`，`{"detail":{"code":"REQUEST_INVALID","message":"malformed JSON at line 1 column 1: Expecting value"}}` |
| `GET /api/models` | `200`（控制台侧不受影响） |
| `GET /api/providers` | `200` |
| `GET /api/dashboard` | `200` |

`/v1/tasks` 返回 `400` 而非 `404` 是关键证据：请求确实进了 agent 路由，没有被
`mount("/")` 吞掉。同一语义在 `tests/test_runtime.py::test_agent_api_is_mounted_alongside_console`
里用 `ASGITransport` 固化（同时覆盖 `/v1/tasks:stream` 与"控制台 `/api/*` 仍为 200"）。

### 6.5 agent 接口口令鉴权（v0.7.0）

用真实 uvicorn 进程验证鉴权，而不是只看单元测试——鉴权是"挂依赖"这类
装配层事实，静态分析看不出来：

```bash
$ LLM_GW_AGENT_PASSWORD=verify-pass LLM_GW_DB=/tmp/gw_auth_verify.sqlite3 \
    .venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory \
    --host 127.0.0.1 --port 8011
```

| 请求 | 结果 |
|---|---|
| `POST /v1/tasks`（无凭证） | `401`，`{"detail":{"code":"AUTH_REQUIRED","message":"agent 接口口令无效"}}` |
| `POST /v1/tasks`（`Bearer wrong`） | `401`，同上 |
| `POST /v1/tasks`（`Bearer verify-pass`，body `not-json`） | `400`，`{"detail":{"code":"REQUEST_INVALID",...}}` |
| `POST /v1/tasks:stream`（无凭证） | `401`，`AUTH_REQUIRED` |
| `GET /health`（无凭证） | `200`，`{"status":"ok"}` |
| `GET /api/models`（无凭证） | `200`（控制台不在保护范围） |
| 401 响应头 | `www-authenticate: Bearer` |

关键证据有两条：① 正确口令下非法 JSON 得到 **400** 而非 401，说明凭证校验通过后请求
确实进入了业务逻辑，而不是被"一律拒绝"蒙混过去；② `/health` 与控制台 `/api/models`
在配置了口令后仍为 `200`，说明豁免范围正确——否则探活会误判服务不可用、页面会打不开。

同一语义在 `tests/harness/test_agent_auth.py`（7 例，含"未配置口令时放行"）与
`tests/test_runtime.py::test_runtime_guards_agent_api_but_not_console` 里固化。

## 7. 复现方式

```bash
# 后端（含覆盖率）
.venv/bin/python -m pytest tests -q --cov=llm_gw

# 前端
cd webapp && npm install && npm test

# 起服（推荐脚本：自动激活 venv 并加载 .env）
./run.sh
.venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000
```

所有 adapter 测试由 `httpx.MockTransport` 驱动，重试测试由 `FakeClock` 驱动——**不需要任何真实供应商密钥**即可跑完全部 351 个用例。仅第 6 节的端到端验证会真的访问上游（6.1 预期收到 `AUTH_INVALID`；6.2 用本地假上游，同样不需要真实密钥；6.5 只验证鉴权层，请求在选模型之前就被拒绝，不触达上游）。
