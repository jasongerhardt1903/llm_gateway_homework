# 测试证据

> 本文件是 **v0.8.4** 的存档。v0.8.4 按更新后的 `需求文档.md` 落五条新需求：
> **流式路径也降级**（首 delta 前换模型）、**agent 口令网页可配**（env 优先）、
> **通讯原始日志**（`exchanges` 表 + `/api/exchanges`）、**控制台不再有 Chat 专属契约**
> （删 `/api/chat*`，Chat 页直连 `/v1/tasks:stream`）、**路由表拖拉拽**。
> 后端用例由 372 增至 **389**（`test_service.py` +5、`test_web_api.py` +12），
> 覆盖率 **93%**；前端用例由 15 增至 **30**（其中工具循环 6 例）。
> v0.8.3 是一次版本标记（无代码改动）：0.8.2 的 preset
> 换型号被库里遗留的 `config` 表 `models` 行整体覆盖，清掉该行并重启后才真正生效，
> 因此升补丁号以便从 `/api/meta` 直接分辨进程是否加载了新清单。用例数、覆盖率与
> 接口契约均与 v0.8.2 相同（后端 **372**、前端 15，覆盖率 92%）。
> v0.8.2 把 DeepSeek preset 的型号换成官方文档当前的
> `deepseek-flash` 与 `deepseek-v4-pro`（旧清单里的 `deepseek-chat` /
> `deepseek-reasoner` 已下线），能力表据官方文档如实声明（v4-pro 不支持视觉）；
> 用例数不变（后端 **372**、前端 15），覆盖率 92%——这是数据订正，没有新增接口。
> v0.8.1 给 v0.8.0 的模型清单查询加了进程内短时缓存
> （成功 300 秒 / 失败 30 秒，键含 `base_url`），后端用例由 367 增至 **372**
> （`tests/web/test_model_discovery.py` +5），覆盖率 92%；前端用例数不变（15）。
> v0.8.0 落实需求"模型管理层"第 1、2 条与"管理与交互层"
> 第 6 条：模型配置改为**向供应商实时查询**（可选模型清单 + 能力 + 高级配置项）、
> **高级配置项逐项可开关且互斥项自动互斥**、新增**模型连接测试**与**页面版本号/更新日志**；
> 后端用例由 351 增至 **367**（新增 `tests/web/test_model_discovery.py` 16 例），覆盖率 92%；
> 前端用例由 8 增至 **15**（`webapp/src/__tests__/smoke.test.jsx` +2、
> 新增 `model-catalog.test.js` 5 例）。
> v0.7.0 为 agent 接口（`/v1/tasks`、`/v1/tasks:stream`）
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
........................................................................ [ 18%]
........................................................................ [ 37%]
........................................................................ [ 55%]
........................................................................ [ 74%]
........................................................................ [ 93%]
..........................                                               [100%]
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.9-final-0 _______________

Name                                             Stmts   Miss  Cover   Missing
------------------------------------------------------------------------------
llm_gw/__init__.py                                   1      0   100%
llm_gw/adapter/__init__.py                           0      0   100%
llm_gw/adapter/base.py                             311     28    91%   146, 175, 178, 187, 204, 210, 212, 226, 267, 269, 271, 295, 307, 334, 359, 486, 507-513, 552, 558-561
llm_gw/adapter/discovery.py                        101      6    94%   231-232, 279-280, 293, 297
llm_gw/adapter/factory.py                           14      0   100%
llm_gw/adapter/presets/__init__.py                   0      0   100%
llm_gw/adapter/presets/anthropic.py                  8      0   100%
llm_gw/adapter/presets/deepseek.py                  10      0   100%
llm_gw/adapter/presets/openai.py                     8      0   100%
llm_gw/adapter/presets/registry.py                  39      2    95%   51, 65
llm_gw/adapter/protocols/__init__.py                 0      0   100%
llm_gw/adapter/protocols/anthropic_messages.py     181     20    89%   164, 175, 189-190, 192, 248-249, 256, 258-259, 295, 306, 319-325, 331
llm_gw/adapter/protocols/openai_compat.py          169     12    93%   133-134, 159, 203-205, 211, 236-237, 289, 293-294
llm_gw/adapter/structured.py                       134     25    81%   55-61, 128, 132, 135-137, 149, 151, 154, 164-165, 181-182, 232-233, 236, 239, 243, 268
llm_gw/adapter/transform.py                         80      7    91%   81, 117-121, 136
llm_gw/core/__init__.py                              0      0   100%
llm_gw/core/advanced.py                             35      3    91%   55, 77-78
llm_gw/core/errors.py                              252     29    88%   209, 261-262, 282-284, 294-295, 303, 307, 311-315, 358, 362, 376-377, 388, 407, 411, 424, 503, 510, 512, 516, 526, 588
llm_gw/core/events.py                              155      6    96%   207, 244-245, 292, 302, 315
llm_gw/core/json_utils.py                          191     14    93%   45, 141-142, 145, 184-185, 188, 197, 263-264, 267, 279, 289, 306
llm_gw/core/messages.py                            124      1    99%   192
llm_gw/core/schema.py                              115      6    95%   111, 142, 144, 175, 214-215
llm_gw/core/telemetry.py                            57      0   100%
llm_gw/harness/__init__.py                           0      0   100%
llm_gw/harness/decisions.py                         14      1    93%   53
llm_gw/harness/query.py                             20      0   100%
llm_gw/harness/retry.py                            128     18    86%   98-99, 104-114, 136, 145, 147, 174, 207
llm_gw/harness/service.py                          224      6    97%   113, 268, 439, 453-454, 486
llm_gw/harness/sse.py                               80      8    90%   97-103, 126
llm_gw/harness/storage.py                          172      9    95%   161, 188, 191, 195, 296, 361, 425, 635-636
llm_gw/router/__init__.py                            0      0   100%
llm_gw/router/profile.py                            68      4    94%   79, 104, 140, 154
llm_gw/router/registry.py                           60      3    95%   75, 91, 111
llm_gw/router/router.py                            121     13    89%   111, 116-118, 142, 147-148, 172, 185, 216, 250, 254-255
llm_gw/router/rules.py                              75      0   100%
llm_gw/runtime.py                                   50      1    98%   119
llm_gw/util/__init__.py                              0      0   100%
llm_gw/util/clock.py                                22      2    91%   34-35
llm_gw/web/__init__.py                               0      0   100%
llm_gw/web/api_models.py                            84      1    99%   142
llm_gw/web/app.py                                  237     22    91%   139, 175, 206, 209-210, 230, 241, 249, 290, 300, 317-318, 363-364, 378, 387, 399-400, 405, 413, 456, 463
------------------------------------------------------------------------------
TOTAL                                             3340    246    93%
389 passed in 2.49s
```

**389 passed，0 failed，93% 覆盖率。**

本轮改动的模块覆盖率：`harness/service.py` 97%（流式降级 + 口令网页配置 +
被校验挡下的通讯也落 exchanges）、`harness/storage.py` 95%（`exchanges` 表与四个查询方法）、
`web/app.py` 91%（`/api/settings*` 与 `/api/exchanges*`，并删除了 `/api/chat*`）、
`web/api_models.py` 99%、`runtime.py` 98%、`router/router.py` 89%。

`harness/service.py` 未覆盖的 6 行都是防御性分支：113（未注入存储时的
`load_agent_password` 提前返回）、268（候选链至少含一项时的兜底）、
439（口令未配置时直接放行的提前返回之外的同一条分支）、453-454（`HTTPException`
的构造参数元组断行）、486（未注入存储时的 `_record` 提前返回）。
`web/app.py` 新增端点里未覆盖的只有 290 与 300——未注入存储时的 503 分支，
与既有 `/api/traces` 的同类分支一致，不重复造用例。

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
| `tests/core/test_telemetry.py` | 5 | |
| `tests/harness/test_agent_auth.py` | 7 | v0.7.0：agent 接口口令鉴权 |
| `tests/harness/test_observability.py` | 17 | |
| `tests/harness/test_service.py` | 19 | **+5（v0.8.4：流式降级）** |
| `tests/router/test_profile.py` | 29 | |
| `tests/router/test_profile_routing.py` | 24 | |
| `tests/router/test_retry.py` | 21 | |
| `tests/router/test_router.py` | 20 | |
| `tests/web/test_model_discovery.py` | 21 | **+5（v0.8.1：清单缓存）**；v0.8.0 建此文件（16 例） |
| `tests/web/test_web_api.py` | 40 | **+12（v0.8.4：口令网页配置、通讯日志、Chat 直连）** |
| `tests/test_phase0_infra.py` | 4 | |
| `tests/test_runtime.py` | 11 | v0.7.0：agent API 与控制台同进程共存 |
| **合计** | **389** | **+17（v0.8.4）** |

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
| `tests/web/test_model_discovery.py` | v0.8.0 模型管理层：高级配置项按协议声明差异（OpenAI 不含 `top_k`）、思考模式互斥项、`auth_headers` 逐协议、上游模型清单优先于 preset 与降级、未知型号标记 `known=false` 且不编造能力、Provider 级查询复用已保存密钥、未知供应商 404、**连接测试的最小请求（`max_tokens=1` / `stream=False` / `Bearer`）**、401 → `AUTH_INVALID`、连不上 → `CONN_FAILED`、`/api/meta` 版本与更新日志；v0.8.1 加清单缓存——TTL 内只查一次上游、成功与失败两条 TTL 各自到期后重新查、`base_url` 不同不吃同一份缓存、返回副本；v0.8.2 在 `tests/adapter/test_adapter_contract.py` 里把"能力必须如实反映供应商限制"的样例从已下线的 `deepseek-reasoner`（不支持工具）换成 `deepseek-v4-pro`（不支持视觉） |
| `tests/test_runtime.py` | 组合根：lifespan 挂载、配置跨重启恢复、根挂载不吞 404、**agent API 与控制台同进程共存**、**密钥优先级（模型 > 环境变量）** |
| `tests/test_phase0_infra.py` | `FakeClock` 不等待、`sse_transport` 重放分片、`scripted_transport` 按序返回错误 |

## 4. "不能真的 sleep" 的证明

重试测试全部注入 `FakeClock`，断言的是**退避序列**而非耗时：

```python
async def test_retries_transient_error_with_backoff(clock):
    ...
    assert clock.sleeps == [500, 1000]
```

整个 `tests/router/test_retry.py`（21 个用例，含"重试 3 次""退避封顶""退避期间取消"）与当时全量 342 个用例一起在 **1.32 秒**内跑完——若存在真实退避等待，仅退避序列 `500+1000+2000` 就会超过 3.5 秒。

窗口类指标同理：`Storage(now=...)` 的时间来自注入函数，`tests/harness/test_observability.py` 用 `now.value - 120` 精确构造"2 分钟前的记录"，不依赖 `time.sleep`。

v0.8.1 的清单缓存沿用同一套约定：`ModelCatalogCache` 的时钟可注入，
`tests/web/test_model_discovery.py` 用 `FakeClock` 把时间直接推到 TTL 之后
（`await clock.sleep((CACHE_TTL_OK + 1) * 1000)`，FakeClock 的时间随 sleep 推进），
因此"缓存过期"是被**断言**出来的，而不是等 5 分钟等出来的。

v0.8.2 只改 preset 数据，因此把原来那条"能力必须如实反映供应商限制"的用例换了
主体：`deepseek-reasoner` 已下线，其"不支持 function calling"的事实不再存在，
改由官方文档明确写出的视觉能力差异承担
（`test_deepseek_v4_pro_declares_no_vision_support` 断言 `v4-pro` 为 `False`、
`flash` 为 `True`）。**没有为了凑数而新增用例**——预设数据本身不适合断言价格数字，
那只会让文档一改就要改测试。

## 5. 前端冒烟

```bash
$ cd webapp && npm test
```

```
 RUN  v2.1.9 webapp

 ✓ src/__tests__/model-catalog.test.js (5 tests) 2ms
 ✓ src/__tests__/smoke.test.jsx (25 tests) 25ms

 Test Files  2 passed (2)
      Tests  30 passed (30)
```

覆盖：五个页签渲染（含 Profile 页）、默认页、SSE 事件解析（含**事件被拆到两个网络分片**的场景）、`[DONE]` 不作为业务事件透出、`error` 终态仍交付、HTTP 错误抛出后端 `detail`；以及 v0.4.0 新增的任务瀑布图三例——按 `run_id` 分组（空值归入「未标记任务」、组内时间正序且不改动入参）、条宽相对全局最长调用与 TTFT 占比（含 `total_ms=0` 的最小宽度与除零保护）、分组渲染的汇总文案与终态配色。

v0.5.0 的 UI 重做**没有改动测试文件**：瀑布图的 `.waterfall-bar` / `.waterfall-ok|bad|warn`
与 `.row-selected` 类名在 Tailwind 组件层里被原样保留，因此这三例继续作为"语义契约"生效。

v0.8.0 新增 7 例（前 8 例**断言未做任何修改**，继续通过）：

| 用例 | 断言要点 |
|---|---|
| `smoke.test.jsx`「侧边栏提供版本号与更新日志入口」 | 静态渲染即出现「版本」「更新日志」（版本号本身来自 `/api/meta`，静态渲染时还没有，故只验证入口） |
| `smoke.test.jsx`「协议下拉的值是 adapter 的协议 ID」 | 页面里必须出现 `value="openai-completions"` / `value="anthropic-messages"` |
| `model-catalog.test.js` × 5 | 见下 |

`model-catalog.test.js` 覆盖 v0.8.0 新抽出的纯函数（无 DOM，可直接断言）：

| 用例 | 断言要点 |
|---|---|
| `initial_enabled` | 已配置的项（含值为 `0`）视为已勾选，`null`/缺省视为未勾选 |
| `toggle_item` 清空 | 取消勾选时把输入框清空，避免看不见的值被提交（`0` 是合法取值，不能当"未配置"） |
| `build_advanced` | 未勾选的数值项在提交体里是 `null`；`thinking_mode` 这类选择项不受勾选状态影响 |
| `toggle_choice` 互斥 | 选中一个值即覆盖同字段的另一个值；再点一次回到 `"default"` |
| 兜底清单结构 | catalog 拉取失败时用的 `DEFAULT_ADVANCED_ITEMS` 结构完整（每项有 `key`/`label`/`kind`） |

构建产物同样验证过：

```bash
$ cd webapp && npm run build
dist/index.html                   0.40 kB │ gzip:   0.29 kB
dist/assets/index-TeWsNTx2.css   21.40 kB │ gzip:   5.27 kB
dist/assets/index-CqsliWXv.js   679.71 kB │ gzip: 203.08 kB
✓ built in 2.04s
```

v0.8.4 新增 9 例（`smoke.test.jsx` 10 → 19；既有 10 例断言未做修改）：

| 用例 | 断言要点 |
|---|---|
| 「api 暴露 streamTask，且不再暴露旧的 streamChat」 | Chat 直连 agent 接口后，控制台的 `streamChat` 必须消失——留着它等于留着一条已 404 的路径 |
| 「Chat 页提供口令输入并提示口令来源」 | Chat 页要有填 agent 口令的地方（直连 `/v1/tasks:stream` 需要 Bearer），且能看出口令来自 env / console / none |
| 「请求落到 /v1/tasks:stream，并按需带上 Bearer 与 x-trace-id」 | 用假 `fetch` 断言 URL、鉴权头、trace 头，以及请求体是 agent 的 `Task` schema（`input.messages`），确保没有偷偷回到 `/api/chat/stream` |
| 「展示口令来源与只写不回显 / 环境变量优先的说明」 | 设置页静态渲染即出现口令来源、环境变量名与"环境变量优先"的说明 |
| 「静态模式下展示编辑器、路由表名称与自上而下执行说明」 | 拖拉拽编辑器只在 `route_mode === "static"` 时出现，并显示路由表名称（profile 名）与执行方向 |
| 「动态模式下不出现顺序编辑器」 | `dynamic` 模式不给手工顺序（由网关按能力/健康度挑选） |
| 「`reorder` 把第 from 项移动到第 to 位，同位原样返回」 | 列表内部拖拽排序的纯函数：前移 / 后移 / 原地不动 / 越界不动 |
| 「按 task_id 两级分组，组内按 flow_index 升序并记录最后一次时间」 | 通讯日志的第一级是 task、第二级是每次通讯流程 |
| 「`classify_payload` 区分 JSON / SSE / 纯文本」 | raw 模式与渲染模式的切换依据：JSON 美化、SSE 逐帧、其余回退原文 |

v0.8.4 的工具循环部分再增 6 例（`smoke.test.jsx` 19 → 25，前端合计 24 → 30）：

| 用例 | 断言要点 |
|---|---|
| 「页面提供工具开关、轮数上限说明与同一会话复用的 task_id」 | 静态渲染即出现工具开关、`最多 N 轮` 的上限文案、`task_id` 与「新会话」——循环的三要素在页面上可见 |
| 「`parse_tools` 只做结构校验：合法 / 空 / 非 JSON / 非数组 / 缺 name」 | 五种输入各自的判定：空串是"不用工具"而非错误，其余给可读的中文错误（真正的 schema 校验留给网关 422，前端不重复实现） |
| 「`run_local_tool` 执行本地假工具，未知工具给出可读说明」 | `get_time` 返回 ISO 8601、`echo` 回显且缺参不炸、未知工具返回提示而不是抛错 |
| 「`assistant_content` 拼出文本 + tool_call 块，空文本时不带空 text 块」 | 回灌历史的 assistant 轮：空文本不产生空 `text` 块，缺 `id` 补空串（`tool_result` 要靠它配对） |
| 「`tool_message` 的 tool_call_id 与发起调用一致」 | `tool_call_id` 必须与发起调用相同，否则会被适配层的成对性归一化当孤儿结果丢掉 |
| 「done 帧里的工具调用能被识读（真实形状：`message.content[]` 的 `toolCall` 块）」 | **按实测原文固化**：`done.message` 是 `asdict` 展开的原始 `AssistantMessage`，工具调用在 `content[]` 里、块类型是驼峰 `toolCall`，顶层没有 `tool_calls`；纯文本回复与缺 `message` 两种情况都返回空数组（前者若误判会空转一轮） |

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

### 6.6 模型发现、连接测试与清单缓存（v0.8.0 / v0.8.1 / v0.8.2）

这一节必须用**真实进程**验证：新增的三个端点分别依赖"后端进程能出网访问供应商"和
"静态产物里真的挂了新页面"，两者都不是单元测试能证明的。

```bash
$ ./run.sh          # 127.0.0.1:8000，README 推荐的启动方式
```

| 请求 | 结果 |
|---|---|
| `GET /api/meta` | `200`，`{"version":"0.8.0","changelog":"# 版本更新说明\n\n..."}`——版本号与更新日志同源返回 |
| `GET /api/providers/openai/models` | `200`，`source="preset"`（上游 401 降级），`error` 如实带回 `https://api.openai.com/v1/models 返回 HTTP 401`；`advanced.items` 为 `temperature / top_p / max_tool_rounds / thinking_mode`，**不含 `top_k`** |
| `GET /api/providers/anthropic/models` | `200`，`base_url` 为 `https://api.anthropic.com/v1`，上游回 401 `x-api-key header is required` → 同样降级为 `source="preset"` |
| `GET /api/providers/nope/models` | `404` |
| `POST /api/models:test`（deepseek，走环境变量密钥） | `200`，`{"ok":false,"code":"AUTH_INVALID","message":"provider (401): Authentication Fails (governor)","latency_ms":92}` |
| `POST /api/models:test`（缺 `base_url`） | `422` |

三条关键证据：

1. **两家供应商都返回 401 而不是 404**，说明请求 URL 与鉴权头拼装正确（Anthropic 的
   404 与 401 语义不同，若路径写错会是 404），且失败时**不编造模型清单**——降级用的是
   preset 内置清单，`error` 字段把真实原因原样交给页面展示。
2. **连接测试的 401 被归类为 `AUTH_INVALID` 而不是 `CONN_FAILED`**：本机 DeepSeek 密钥
   已失效，`latency_ms=92` 说明请求确实往返了一次上游。这同时证明了"真实最小对话"这条
   路径是通的（`ping` + `max_tokens=1`），而不是只做了 TCP 探测。
3. **`/api/models:test` 缺字段返回 422，上游失败返回 200 + `ok=false`**：HTTP 状态码只
   表达"请求本身是否合法"，模型通不通由 body 里的 `ok` 表达——否则前端无法区分
   "表单填错"和"密钥填错"。

浏览器侧用真实 UI 复测（v0.7.0 时这一项曾是 FAIL，原因是当时只选了供应商没填模型名称，
按钮的 `disabled` 守卫导致点击无效——属操作前提不满足，非缺陷）：

| 观察项 | 结果 |
|---|---|
| 打开 `http://127.0.0.1:8000`，左侧导航落在「模型定义」 | PASS |
| 供应商选 `DeepSeek（deepseek）` 后，页面上出现「未能从供应商拉取模型清单，已回退内置清单：…返回 HTTP 401…」 | PASS |
| 协议下拉显示 `openai-completions` | PASS |
| 填写模型名称 `deepseek-chat` 后点「测试连接」，出现结果横幅「连接失败：AUTH_INVALID：provider (401): Authentication Fails (governor)」 | **PASS（上一轮的 FAIL 已消除）** |
| 浏览器控制台无 JS 报错 | PASS |

**v0.8.2 的真实进程验证**：preset 换型号后，用干净库起一个临时实例，确认清单与能力
按官方文档加载（本机密钥失效，上游 401 → 降级到 preset，正好把 preset 内容露出来）：

```bash
$ LLM_GW_DB=/tmp/fresh.sqlite3 .venv/bin/python -m uvicorn \
    llm_gw.runtime:create_runtime_app --factory --port 8011
$ curl -s localhost:8011/api/models | ...   # 只列 deepseek 部分
  deepseek   deepseek-flash               ctx=  1000000 max=  384000
  deepseek   deepseek-v4-pro              ctx=  1000000 max=  384000

$ curl -s localhost:8011/api/providers/deepseek/models
  source: preset
  error : https://api.deepseek.com/v1/models 返回 HTTP 401：Authentication Fails (governor)
  deepseek-flash       known=True  vision=True  tools=True reasoning=True
  deepseek-v4-pro      known=True  vision=False tools=True reasoning=True
```

**这里暴露了一个必须在文档里说清的坑**：`restore_config` 在 config 表非空时**整体替换**
注册表（`llm_gw/web/app.py` 的 `registry.models = [...]`），因此**用户保存过模型之后，
preset 的更新不会再出现在界面上**。本机实测（`llm_gw.sqlite3` 里存着 9 月 19 日保存的
5 个模型）在升级到 0.8.2 并重启后，`/api/models` 仍然是旧清单：

```
openai     gpt-4o-mini                  ctx=   128000 max=   16384
openai     gpt-4o                       ctx=   128000 max=   16384
deepseek   deepseek-chat                ctx=    64000 max=    8192   ← 已下线，来自数据库
anthropic  claude-3-5-haiku-20241022    ctx=   200000 max=    8192
deepseek   deepseek-flash               ctx=        0 max=  202512   ← 用户自建，覆盖了 preset
```

这是"用户配置优先于 preset 默认值"的必然结果，不是缺陷，但**升级 preset 后必须清掉
`config` 表里的 `models` 行（或删库重来）才会看到新清单**。注意 profile 里若引用了
被清掉的标签，需要一并调整。

清掉该行后重启（数据库先备份为 `llm_gw.sqlite3.bak-<时间戳>`），真实进程回到 preset 清单，
说明 preset 内容与"清库即生效"这条路径都是通的：

```bash
$ curl -s localhost:8000/api/models          # 6 个模型，deepseek 为新型号
  openai     gpt-4o-mini                  ctx=   128000 max=   16384
  openai     gpt-4o                       ctx=   128000 max=   16384
  deepseek   deepseek-flash               ctx=  1000000 max=  384000
  deepseek   deepseek-v4-pro              ctx=  1000000 max=  384000
  anthropic  claude-3-5-haiku-20241022    ctx=   200000 max=    8192
  anthropic  claude-3-5-sonnet-20241022   ctx=   200000 max=    8192

$ curl -s localhost:8000/api/profiles        # profile 配置未被牵连
  t1  → ['deepseek/Deepseek-flash-real', 'deepseek/Deepseek-flash', 'deepseek/deepseek-flash']
  poc → ['openai/gpt-4o-mini', 'openai/gpt-4o', 'deepseek/deepseek-flash']
```

两个 profile 里只有 `deepseek/deepseek-flash` 是被真正引用的标签，它在 preset 里继续存在
（值已更新为新型号参数）；`t1` 里的 `deepseek/Deepseek-flash-real` 与 `deepseek/Deepseek-flash`
在清库前后都是悬空标签（库里从未有过这两个 id），属于历史遗留，与本次变更无关。

**清单缓存（v0.8.1）同样用真实进程验证**——缓存是"进程内状态"，单元测试证明不了
它在真实服务里真的生效。重启服务后连续请求，看耗时落差：

```bash
$ for i in 1 2 3; do
    curl -s -o /dev/null -w "第 $i 次: %{time_total}s\n" \
      http://127.0.0.1:8000/api/providers/deepseek/models
  done
第 1 次: 0.116720s   # 真的出网打了上游（收到 401 再降级）
第 2 次: 0.001002s   # 命中缓存
第 3 次: 0.001191s
```

再换供应商，验证缓存键里带了 `provider`（否则会串味）：

```bash
$ for p in openai anthropic openai; do
    curl -s -o /dev/null -w "$p: %{time_total}s\n" \
      http://127.0.0.1:8000/api/providers/$p/models
  done
openai:    0.981856s   # 首次，出网
anthropic: 0.950450s   # 换供应商 → 未命中，出网
openai:    0.000873s   # 回到 openai → 命中
```

首次约 0.1~1 秒（一个上游往返）对后续约 1 毫秒，差三个数量级——这是缓存生效最直接
的证据；而"换供应商就变慢、换回来又快"证明键是按供应商分开的。

> 顺带修掉一个 v0.7.0 遗留缺陷：页面「协议」下拉的选项值写的是 `openai` / `anthropic`，
> 但 adapter 认识的协议 ID 是 `openai-completions` / `anthropic-messages`。由于选供应商
> 时会用 preset 的正确值覆盖，只要用户不去动这个下拉就不会暴露；一旦手动选一次，模型
> （含"测试连接"）就会以"未知的 API 协议"失败。已改为协议 ID，并加
> `smoke.test.jsx`「协议下拉的值是 adapter 的协议 ID」把它固化下来。

### 6.7 口令网页配置、Chat 直连、通讯原始日志（v0.8.4）

本轮五条需求里有四条改的是**装配层事实**（依赖挂载、路由是否存在、落库字段），
单元测试只能证明函数行为，证明不了"真实进程里这个端点真的在、旧的真的没了"。
因此照旧起一个真实 uvicorn：

```bash
$ LLM_GW_DB=/tmp/v084c.sqlite3 .venv/bin/python -m uvicorn \
    llm_gw.runtime:create_runtime_app --factory --port 8051
```

| 请求 | 结果 |
|---|---|
| `GET /health` | `200`，`{"status":"ok"}` |
| `GET /api/meta` | `version="0.8.4"`，`changelog` 含 `## 0.8.4` 小节 |
| `POST /api/chat/stream` | **`404`**——控制台的 Chat 专属契约（`ChatRequest`）已删除 |
| `GET /api/settings`（未配口令） | `{"agent_password_set":false,"agent_password_source":"none","env_key":"LLM_GW_AGENT_PASSWORD"}` |
| `PUT /api/settings/agent-password`（`{"password":"s3cret"}`） | `{"agent_password_set":true,"agent_password_source":"console",...}` |
| 同上（`{"password":null}`） | 回到 `agent_password_source":"none"`——清除路径可用 |
| `POST /v1/tasks:stream`（无 `Authorization`） | `401`，`{"detail":{"code":"AUTH_REQUIRED","message":"agent 接口口令无效"}}` |
| `POST /v1/tasks`（`Bearer s3cret`，body `{bad`） | `400`，`REQUEST_INVALID` + `malformed JSON at line 1 column 2: ...` |
| `POST /v1/tasks:stream`（`Bearer s3cret`，`messages: []`） | `422`，`REQUEST_INVALID` + `input.messages: List should have at least 1 item ...` |
| `POST /v1/tasks:stream`（`Bearer s3cret`，合法 Task） | `200`，SSE 正常吐出（本机密钥失效，故为 `event: error`） |

两条关键证据：

1. **`/api/chat/stream` 返回 404 而不是 401/422**：这条路由是**真的不存在**，而不是
   "存在但拒绝"——需求 R1 要的就是控制台不再持有 Chat 专属契约，Chat 页按 agent 的
   `Task` schema 自己调 `/v1/tasks:stream`。页面能通，靠的是同一进程里挂着的 agent 路由。
2. **口令未配时不设防、配了就用，且 env 优先**：`GET /api/settings` 只回"是否已配 /
   来源 / 环境变量名"，**不回显口令本身**；`source` 从 `none` → `console` 的变化说明
   写库生效（重启后由 `load_agent_password` 恢复）。env 优先这一支由
   `tests/web/test_web_api.py::test_env_password_wins_over_console` 固化，不在真实进程里
   反复改环境变量。

**通讯原始日志**（R4）也在同一进程里验证——这是本轮唯一新增的落库表，必须看到真数据：

```bash
$ curl -s "localhost:8051/api/exchanges" | ...
ui-2         flow=1 /v1/tasks:stream stream=True  error    REQUEST_INVALID  model=None
             flow=1 /v1/tasks        stream=False error    REQUEST_INVALID  model=None
ui-1         flow=1 /v1/tasks:stream stream=True  error    AUTH_INVALID     model=deepseek/deepseek-flash
flow-demo    flow=2 /v1/tasks        stream=False error    AUTH_INVALID     model=None
flow-demo    flow=1 /v1/tasks        stream=False error    AUTH_INVALID     model=None
```

同一 `task_id` 连发两次 → `flow_index` 依次为 `1`、`2`，这就是"按 task id / 每次通讯流程
两级组合"的后端依据（前端 Trace 页据此折叠分组）。流式那次则完整留下了**双向原文**：

```
task_id  : ui-1            trace_id : chatui-7        endpoint : /v1/tasks:stream  stream : true
request_raw : {"task_id":"ui-1","input":{"messages":[{"role":"user","content":"hi"}]}}
response_raw: event: error\ndata: {"type": "error", ... "error_message": "AUTH_INVALID: provider (401): ..."}\n\n
status   : error           error_code : AUTH_INVALID          duration_ms : 422
meta     : {"model":"deepseek/deepseek-flash","profile":"",
            "degraded_from":"openai/gpt-4o-mini","degraded_to":"deepseek/deepseek-flash"}
```

四点值得记下：

- `response_raw` 是**实际发出的 SSE 帧原文**（带 `event:` / `data:` 行），不是渲染后的
  结果——页面里的 raw data 模式直接显示它，易读模式再解析成"逐帧 + JSON 美化"。
- 本机 OpenAI 密钥失效，主路由 `gpt-4o-mini` 收到 `AUTH_INVALID`，**在首个 delta 之前**
  就降级到了 `deepseek/deepseek-flash`（R2）；`meta` 把 `degraded_from` / `degraded_to`
  如实记下，而 `requests` 表里那次调用的 `model` 也是 `deepseek-flash` 而非主路由——
  两张表在"这次到底用了谁"上口径一致。
- **被两层校验挡下的通讯也有一条记录**（最前两行）：`ui-2` 因 `messages: []` 得 422，
  `task_id` 从原文里抠出来照常归组；`{bad` 那条连 JSON 都不合法，`task_id` 为空串：

  ```
  task_id: 'ui-2' | endpoint: /v1/tasks:stream | status: error
    request_raw : {"task_id":"ui-2","input":{"messages":[]}}
    response_raw: {"detail": {"code": "REQUEST_INVALID", "message": "task does not match schema: ..."}}
  task_id: ''     | endpoint: /v1/tasks        | status: error
    request_raw : {bad
    response_raw: {"detail": {"code": "REQUEST_INVALID", "message": "malformed JSON at line 1 column 2: ..."}}
  ```

  这两条**一次模型调用都没有**，`requests` 表里没有任何行；而 agent 最常踩的恰恰是
  schema 错误，"我到底发了什么"只有通讯日志看得到，所以不能因为"没进路由"就不记。
- 两张表的字段口径由此显现：`requests` 答的是"这次调用落到哪个模型、花了多少钱"，
  `exchanges` 答的是"agent 发来什么字节、网关回了什么字节"——**双向原始报文只有后者有**，
  一次通讯也未必对应一次模型调用，因此不能合成一张表。

非流式路径的 `response_raw` 是网关对外回的 JSON 原文，`warnings` 一并落库；**状态列不是
照抄 HTTP 码**——模型调用失败时 HTTP 仍是 `200`（状态码只表达"请求本身合法"），日志里记的
是 `error` + `AUTH_INVALID`，否则页面上一片绿色而正文全是错误：

```bash
$ curl -s "localhost:8051/api/exchanges" | ...   # flow-demo 那两条
endpoint=/v1/tasks stream=False status=error error_code=AUTH_INVALID
response_raw: {"task_id": "flow-demo", "stop_reason": "error", "terminal": "error", "text": "",
               "error_message": "AUTH_INVALID: ...",
               "warnings": ["AUTH_INVALID: 已降级 openai/gpt-4o-mini → deepseek/deepseek-flash；…"]}
```

> 接口形状提醒：`GET /api/exchanges` 返回的是**裸数组**（`/api/traces` 亦然），不是
> `{"items": [...]}` 信封；`task_id` 与 `q` 同时给出时以 `task_id` 为准。

### 6.8 Chat 页编排多轮 agent 工具循环（v0.8.4）

「网关不跑工具循环」是刻意的设计（`Task.tool_rounds()` 的 docstring：上限只能以"拒绝超限请求"
的方式表达），所以"收到 `tool_call` → 执行工具 → 把 `tool_result` 塞回 `messages` → 再调一次"
必须由调用方实现。Chat 页演示的正是这一步，用本地假上游（`127.0.0.1:9099`：收到 `role: tool`
的消息就回文本，否则回一个 `get_time` 调用）跑通两轮，两轮**共用同一个 `task_id`**：

```bash
$ .venv/bin/python /tmp/verify_tool_loop.py        # 临时脚本，指向 8050 的副本进程
注册假模型：HTTP 200
--- 第 1 轮：HTTP 200
  toolcall_start: {"index": 0, "id": "call_abc", "name": "get_time"}
  toolcall_end:   {"index": 0, "id": "call_abc", "name": "get_time", "arguments": {}}
  done:           {"reason": "tool_use", "message": {"toolCalls": [{"id": "call_abc", ...}]}}
--- 第 2 轮（回填工具结果）：HTTP 200
  text_delta: {"delta": "工具返回了，"}
  text_delta: {"delta": "时间已拿到。"}
  done:       {"reason": "stop", "message": {"toolCalls": []}}
  最终文本：'工具返回了，时间已拿到。'
--- 通讯日志（task_id=chat-tool-1）：3 条
  flow_index=1 status=done endpoint=/v1/tasks:stream
  flow_index=2 status=done endpoint=/v1/tasks:stream
  flow_index=3 status=done endpoint=/v1/tasks:stream
```

（脚本里的 `calls_from_done` 与页面同款：从 `done.message.content[]` 筛 `type == "toolCall"`。
条数比轮数多 1 是因为此前那次调 `done` 帧时踩了 `KeyError` 的试跑也落了一条记录——
这本身就是 R4 "每次通讯都留一条"的旁证。）

同一循环在**真实浏览器**里也走了一遍（网关 8050 托管刚构建的控制台，勾选「带上 tools」、
Profile 选「全局模型池」、输入"现在几点？"发送）：

| 观测点 | 结果 |
|---|---|
| assistant 气泡个数 | **2 个**（= 循环跑了两轮：先要求调工具，拿到结果后再作答） |
| 第 1 个气泡 | 带「1 次工具调用」徽标，正文有 `get_time({}) → 2026-09-21T13:05:48.221Z`，终态 `done` |
| 第 2 个气泡 | 无工具调用徽标，终态 `done` |
| 页面 / 浏览器控制台报错 | 无 |

**一个真实的坑**：`done.message` 是网关 `asdict` 展开的原始 `AssistantMessage`，工具调用在
`content[]` 里、块类型是驼峰 `toolCall`；第一版页面按 `message.tool_calls` 去取，结果一个都拿不到，
循环在第一轮就静默结束（表现是"模型说要调工具，页面却没动作"）。现在这段形状写进了
`docs/interface.md` 的「`done` / `cancelled` 里的 `message` 形状」，并有用例按实测原文固化。

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

所有 adapter 与模型发现测试均由 `httpx.MockTransport` 驱动，重试与缓存过期测试由 `FakeClock` 驱动——**不需要任何真实供应商密钥**即可跑完全部 389 个用例。仅第 6 节的端到端验证会真的访问上游（6.1 预期收到 `AUTH_INVALID`；6.2 用本地假上游，同样不需要真实密钥；6.5 只验证鉴权层，请求在选模型之前就被拒绝；6.6 会真的访问供应商的 `/models` 与 `/chat/completions`，预期收到 401 并降级；6.7 同理会真的打一次上游并收到 401，因此**也不需要有效密钥**；6.8 用本地假上游，同样不需要真实密钥）。

> 复现 v0.8.2 的 preset 验证时，记得用干净库（`LLM_GW_DB=/tmp/fresh.sqlite3`）：用默认的
> `llm_gw.sqlite3` 会被里面已保存的模型清单覆盖，看到的仍是旧型号，详见第 6.6 节。
