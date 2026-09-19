# 测试证据

> 本文件是 **v0.2.0** 的存档。相比 v0.1.0（256 例）新增 80 例：gwprofile 数据模型与解析
> （29）、profile 作用域路由与工具轮数护栏（24）、高级配置注入请求体（14）、
> Web profile API 与密钥脱敏（+9）、旧库迁移与密钥优先级（+4）。

## 1. 全量运行

```bash
$ .venv/bin/python -m pytest tests -p no:cacheprovider --cov=llm_gw --cov-report=term-missing
```

```
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
................................................                         [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.9-final-0 _______________

Name                                             Stmts   Miss  Cover   Missing
------------------------------------------------------------------------------
llm_gw/__init__.py                                   1      0   100%
llm_gw/adapter/__init__.py                           0      0   100%
llm_gw/adapter/base.py                             311     29    91%   146, 175, 178, 187, 204, 210, 212, 226, 267, 269, 271, 295, 307, 334, 359, 461, 486, 507-513, 552, 558-561
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
llm_gw/core/errors.py                              250     39    84%   146, 198-199, 219-221, 231-232, 240, 244, 248-252, 294-296, 299, 313-314, 325, 344, 348, 361, 373-374, 432, 440, 447, 449, 453, 456-457, 462-465, 499, 525
llm_gw/core/events.py                              155      7    95%   207, 244-245, 285, 292, 302, 315
llm_gw/core/json_utils.py                          191     14    93%   45, 141-142, 145, 184-185, 188, 197, 263-264, 267, 279, 289, 306
llm_gw/core/messages.py                            124      2    98%   192, 194
llm_gw/core/schema.py                              115      6    95%   111, 142, 144, 175, 214-215
llm_gw/core/telemetry.py                            56      0   100%
llm_gw/harness/__init__.py                           0      0   100%
llm_gw/harness/decisions.py                         15      2    87%   50-51
llm_gw/harness/query.py                             20      0   100%
llm_gw/harness/retry.py                            127     18    86%   97-98, 103-113, 135, 144, 146, 173, 200
llm_gw/harness/service.py                          108      6    94%   171-175, 207
llm_gw/harness/sse.py                               80      8    90%   97-103, 126
llm_gw/harness/storage.py                          133      7    95%   134, 161, 164, 168, 269, 334, 398
llm_gw/router/__init__.py                            0      0   100%
llm_gw/router/profile.py                            68      4    94%   79, 104, 140, 154
llm_gw/router/registry.py                           60      3    95%   75, 91, 111
llm_gw/router/router.py                             74      9    88%   71, 75-77, 98, 135-138
llm_gw/router/rules.py                              75      0   100%
llm_gw/runtime.py                                   48      1    98%   105
llm_gw/util/__init__.py                              0      0   100%
llm_gw/util/clock.py                                22      3    86%   31, 34-35
llm_gw/web/__init__.py                               0      0   100%
llm_gw/web/api_models.py                            93      1    99%   142
llm_gw/web/app.py                                  182     19    90%   88, 126, 129-130, 150, 161, 169, 209-210, 247, 255, 303, 305, 307, 309, 311, 313, 324, 331
------------------------------------------------------------------------------
TOTAL                                             2984    248    92%
336 passed in 1.79s
```

**336 passed，0 failed，92% 覆盖率。**

新增模块的覆盖率：`router/profile.py` 94%、`core/advanced.py` 91%、`router/rules.py` 100%、
`web/api_models.py` 99%。

## 2. 按文件分布

| 文件 | 用例数 | 本轮变化 |
|---|---|---|
| `tests/adapter/test_adapter_contract.py` | 19 | |
| `tests/adapter/test_advanced_config.py` | 14 | **新增** |
| `tests/adapter/test_streaming.py` | 22 | |
| `tests/adapter/test_structured_output.py` | 19 | |
| `tests/adapter/test_transform.py` | 7 | |
| `tests/core/test_errors.py` | 45 | |
| `tests/core/test_events.py` | 8 | |
| `tests/core/test_json_utils.py` | 15 | |
| `tests/core/test_messages.py` | 7 | |
| `tests/core/test_schema.py` | 15 | 字段改名同步 |
| `tests/core/test_telemetry.py` | 5 | 字段改名同步 |
| `tests/harness/test_observability.py` | 17 | +2（旧库迁移） |
| `tests/harness/test_service.py` | 11 | |
| `tests/router/test_profile.py` | 29 | **新增** |
| `tests/router/test_profile_routing.py` | 24 | **新增** |
| `tests/router/test_retry.py` | 21 | |
| `tests/router/test_router.py` | 17 | profile 作用域改造 |
| `tests/web/test_web_api.py` | 28 | +9（profile API + 密钥） |
| `tests/test_phase0_infra.py` | 4 | |
| `tests/test_runtime.py` | 9 | +2（密钥优先级） |
| **合计** | **336** | **+80** |

## 3. 需求要求的六类测试

需求第 137–144 行规定了至少六类测试。逐条对应：

| 需求类别 | 文件 | 覆盖内容 | 代表用例 |
|---|---|---|---|
| Adapter Contract Test | `tests/adapter/test_adapter_contract.py`、`test_advanced_config.py` | 每个 adapter 的**请求翻译**与**响应翻译**，由 `httpx.MockTransport` 驱动，不依赖真实供应商 | 逐协议断言请求体字段（`response_format`、`tool_choice`、`stream_options.include_usage`）、高级配置注入（`temperature` / `top_p` / `top_k` / `thinking`）、`extra_body` 覆盖 |
| router 测试 | `tests/router/test_router.py`、`test_profile_routing.py` | profile 作用域、能力匹配、静态顺序主备、fallback、候选拒绝原因 | `test_static_order_is_not_reordered_by_cost`、`test_static_order_skips_unknown_labels`、`test_profile_scopes_candidates`、`test_unknown_profile_fails_fast`、`test_execute_falls_back_to_backup_on_transient_error`、`test_decision_table_matches_requirement` |
| 重试测试 | `tests/router/test_retry.py`、`test_profile_routing.py` | 用 mocktransport 模拟超时与错误，验证重试次数与退避策略，**不真的 sleep** | `test_retries_transient_error_with_backoff`（`assert clock.sleeps == [500, 1000]`）、`test_non_retryable_error_fails_fast`（`assert clock.sleeps == []`）、`test_provider_request_respects_retry_after_header`（`assert clock.sleeps == [300]`）、per-profile 策略 |
| streaming 测试 | `tests/adapter/test_streaming.py` | SSE 事件序列正确性：delta 顺序、usage 事件、终态、错误中断行为 | delta 顺序、usage 事件位置、`test_second_terminal_push_is_rejected`、中途错误中断 |
| structured output 测试 | `tests/adapter/test_structured_output.py` | JSON 提取、Schema 校验、**修复尝试次数**、失败时的错误返回 | JSON 围栏剥离、修复次数上限、超限抛 `OUTPUT_SCHEMA_INVALID`、流式终校验 |
| 可观测性 | `tests/harness/test_observability.py` | trace / Metrics / Cost Ledger 是否正确记录每次调用的关键字段 | TTFT 口径、时间窗指标、trace 链路顺序、脱敏生效、`profile` 落库、旧库迁移 |

额外补充（计划中未强制、但覆盖了关键不变式）：

| 文件 | 覆盖内容 |
|---|---|
| `tests/router/test_profile.py` | 逗号分隔顺序解析（含全角逗号与顿号、去重保序）、模版判定矩阵、`AdvancedConfig` 范围校验 |
| `tests/router/test_profile_routing.py` | `TOOL_ROUNDS_EXCEEDED` 护栏（含边界值：等于上限放行、超一即拒）、模版值参与护栏判定 |
| `tests/harness/test_service.py` | 客户端断连取消上游、流内 error 不发 `[DONE]`、已流式输出后不盲目重生成 |
| `tests/core/test_events.py` | 单一终态不变式、`end` 后 push 丢弃 |
| `tests/web/test_web_api.py` | 模型 CRUD、**密钥只写不回显 / 缺省不修改 / 空串清除**、profile CRUD 往返、Dashboard、Trace 搜索、Chat SSE 代理 |
| `tests/test_runtime.py` | 组合根：lifespan 挂载、配置跨重启恢复、根挂载不吞 404、**密钥优先级（模型 > 环境变量）** |
| `tests/test_phase0_infra.py` | `FakeClock` 不等待、`sse_transport` 重放分片、`scripted_transport` 按序返回错误 |

## 4. "不能真的 sleep" 的证明

重试测试全部注入 `FakeClock`，断言的是**退避序列**而非耗时：

```python
async def test_retries_transient_error_with_backoff(clock):
    ...
    assert clock.sleeps == [500, 1000]
```

整个 `tests/router/test_retry.py`（21 个用例，含"重试 3 次""退避封顶""退避期间取消"）与全量 336 个用例一起在 **1.79 秒**内跑完——若存在真实退避等待，仅退避序列 `500+1000+2000` 就会超过 3.5 秒。

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

覆盖：五个页签渲染（含新增 Profile 页）、默认页、SSE 事件解析（含**事件被拆到两个网络分片**的场景）、`[DONE]` 不作为业务事件透出、`error` 终态仍交付、HTTP 错误抛出后端 `detail`。

构建产物同样验证过：

```bash
$ cd webapp && npm run build
dist/assets/index-vVIoStGo.js   167.44 kB │ gzip: 53.40 kB
✓ built in 274ms
```

## 6. 端到端验证（真实进程）

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

### 6.1 旧库迁移（v0.1.0 的库 → v0.2.0）

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

所有 adapter 测试由 `httpx.MockTransport` 驱动，重试测试由 `FakeClock` 驱动——**不需要任何真实供应商密钥**即可跑完全部 336 个用例。仅第 6 节的端到端验证会真的访问上游（因此那里预期收到 `AUTH_INVALID`）。
