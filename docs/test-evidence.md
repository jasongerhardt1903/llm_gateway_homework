# 测试证据

> 本文件是 **v0.8.8** 的存档。v0.8.8 修一组「profile 里配了好几个模型，降级看起来没生效」
> 的问题：候选链此前**只试前两名**（profile 里第 3、第 4 个模型永远用不到）、非流式降级后
> 记录的 `model` 写成主路由、`degraded_from` / `degraded_to` 从不落库、路由决策不可见。
> 现在**每次尝试各落一条记录**（共享 `trace_id`，`attempt_index` 标位次、`degraded_from`
> 标降级来源，并带路由决策快照 `route`）。后端用例由 454 增至 **457**（`test_router.py` +2、
> `test_service.py` +1）；`scripts/verify.py` 由 96 项增至 **109 项**；前端 30 增至
> **33 passed**；接口契约调用记录**新增** `route` / `attempt_index` / `degraded_from` /
> `disposition` 四个字段，其余不变。详见第 6.13 节。
>
> 下面是 v0.8.7 的内容存档。v0.8.7 补齐上一版如实记下的两个空档：
> **路由键**（请求顶层 `model` 字段，此前发 `{"model": ...}` 会被 422 挡下）与
> **`CallRecord.output_valid`**（此前有列却无人写入、恒为 `None`）。后端用例由 434 增至
> **454**（`test_schema.py` +9、`test_profile_routing.py` +6、`test_service.py` +5）；
> `scripts/verify.py` 由 84 项增至 **96 项**；接口契约**新增** task 顶层字段 `model` 与
> 响应/记录字段 `output_valid`，其余不变。详见第 6.12 节。
>
> 下面是 v0.8.6 的内容存档。v0.8.6 补齐验收清单的三处缺口：
> **`response_format` 别名**（此前按 OpenAI 字段名发请求会被 422 挡下）、
> **按模型独立限流**（令牌桶，超限 429 + `Retry-After`）、
> **提示词版本管理**（`prompts` 表 + `{{变量}}` 渲染 + `prompt` 版本引用 + `/api/prompts*`）。
> 后端用例由 390 增至 **434**（`test_prompts.py` +13、`test_ratelimit.py` +11、
> `test_web_api.py` +5、`test_schema.py` +3）；接口契约**新增**两个 task 字段
> （`input.response_format`、顶层 `prompt`）与四个控制台路由，其余不变。
>
> 下面是 v0.8.5 的内容存档。v0.8.5 修一个缺陷：`_persist_models` 落库时复用了**对外**
> payload（`api_key` 恒为 `None`），每次保存模型都会把密钥抹掉，重启后模型全部变"未配置"。
> 落库改走 `model_to_stored_payload()`，后端用例由 389 增至 **390**
> （`test_web_api.py` +1），覆盖率 93%；前端 30 例不变，接口契约无变化。
>
> 下面是 v0.8.4 的内容存档。v0.8.4 按更新后的 `需求文档.md` 落五条新需求：
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
........................................................................ [ 15%]
........................................................................ [ 31%]
........................................................................ [ 47%]
........................................................................ [ 63%]
........................................................................ [ 79%]
........................................................................ [ 95%]
......................                                                   [100%]
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
llm_gw/adapter/protocols/anthropic_messages.py     184     20    89%   169, 180, 194-195, 197, 253-254, 261, 263-264, 300, 311, 324-330, 336
llm_gw/adapter/protocols/openai_compat.py          171     12    93%   136-137, 162, 206-208, 214, 239-240, 292, 296-297
llm_gw/adapter/structured.py                       134     25    81%   55-61, 128, 132, 135-137, 149, 151, 154, 164-165, 181-182, 232-233, 236, 239, 243, 268
llm_gw/adapter/transform.py                         80      7    91%   81, 117-121, 136
llm_gw/core/__init__.py                              0      0   100%
llm_gw/core/advanced.py                             35      3    91%   55, 77-78
llm_gw/core/errors.py                              252     29    88%   209, 261-262, 282-284, 294-295, 303, 307, 311-315, 358, 362, 376-377, 388, 407, 411, 424, 503, 510, 512, 516, 526, 588
llm_gw/core/events.py                              155      6    96%   207, 244-245, 292, 302, 315
llm_gw/core/json_utils.py                          191     14    93%   45, 141-142, 145, 184-185, 188, 197, 263-264, 267, 279, 289, 306
llm_gw/core/messages.py                            124      1    99%   192
llm_gw/core/schema.py                              194     10    95%   128, 184, 186, 260, 288, 299-301, 381-382
llm_gw/core/telemetry.py                            57      0   100%
llm_gw/harness/__init__.py                           0      0   100%
llm_gw/harness/decisions.py                         14      1    93%   53
llm_gw/harness/prompts.py                           50      2    96%   119-120
llm_gw/harness/query.py                             20      0   100%
llm_gw/harness/ratelimit.py                         75      3    96%   157-160
llm_gw/harness/retry.py                            128     18    86%   98-99, 104-114, 136, 145, 147, 174, 207
llm_gw/harness/service.py                          266      7    97%   132, 202, 333, 521, 535-536, 568
llm_gw/harness/sse.py                               80      8    90%   97-103, 126
llm_gw/harness/storage.py                          200      9    96%   174, 201, 204, 208, 309, 374, 438, 718-719
llm_gw/router/__init__.py                            0      0   100%
llm_gw/router/profile.py                            68      4    94%   79, 104, 140, 154
llm_gw/router/registry.py                           66      3    95%   87, 103, 123
llm_gw/router/router.py                            121     12    90%   111, 116-118, 142, 147-148, 185, 216, 250, 254-255
llm_gw/router/rules.py                              85      0   100%
llm_gw/runtime.py                                   51      1    98%   122
llm_gw/util/__init__.py                              0      0   100%
llm_gw/util/clock.py                                22      2    91%   34-35
llm_gw/web/__init__.py                               0      0   100%
llm_gw/web/api_models.py                            93      1    99%   145
llm_gw/web/app.py                                  261     22    92%   142, 178, 209, 212-213, 233, 244, 252, 327, 337, 354-355, 407-408, 422, 431, 443-444, 455, 464, 507, 514
------------------------------------------------------------------------------
TOTAL                                             3669    256    93%
```

**454 passed，0 failed，93% 覆盖率。**

本轮改动的模块覆盖率：`router/rules.py` **100%**（`model` 点名的四条分支：命中、
不存在、越界、能力不匹配）、`core/schema.py` 95%（`Task.model` + `output_validity`
及其 schema 子集校验器）、`harness/service.py` 97%（`_output_validity` 与两处落点）、
`router/registry.py` 95%（`find()` 的标签/裸 id 两条路径）。

v0.8.7 新增代码里未覆盖的都是**防御性分支**：`core/schema.py` 381-382 是
`_matches_schema` 里"schema 本身不是对象"的兜底（返回 True，即放宽），
没有正常路径会走到；`registry.py` 87 / 103 / 123 与 `service.py` 521 / 535-536
是既有分支与 `UNKNOWN` 归类，与本次改动无关。

v0.8.6 新增代码里未覆盖的都是**防御性分支**：`prompts.py` 119-120 与
`storage.py` 718-719（库里 `variables` / `meta` 列不是合法 JSON 时的回落）、
`ratelimit.py` 157-160（`reset()`，供配置变更后清桶，当前无调用方）、
`service.py` 202（`check_rate_limit` 在"主路由无候选"时的提前返回——该情形应由路由
如实报 `ROUTE_NO_CANDIDATE`，不重复造用例）、521 与 535-536（`_event_code` /
`_error_code_from` 对无法识别的错误的 `UNKNOWN` 归类）。
`web/app.py` 507 与 514（未构建前端产物时的 `_mount_frontend` 提前返回与 index 路由）
是既有分支，与本次改动无关。

## 2. 按文件分布

| 文件 | 用例数 | 本轮变化 |
|---|---|---|
| `tests/adapter/test_adapter_contract.py` | 23 | **+4（v0.8.6：json_object 透传、json_schema 归一化、deepseek 不降级、anthropic 只回 JSON 的 system 指令）** |
| `tests/adapter/test_advanced_config.py` | 14 | |
| `tests/adapter/test_streaming.py` | 22 | |
| `tests/adapter/test_structured_output.py` | 19 | |
| `tests/adapter/test_transform.py` | 7 | |
| `tests/core/test_errors.py` | 45 | |
| `tests/core/test_events.py` | 8 | |
| `tests/core/test_json_utils.py` | 15 | |
| `tests/core/test_messages.py` | 7 | |
| `tests/core/test_schema.py` | 33 | **+9（v0.8.7：`model` 顶层字段、`output_validity` 三分语义与 schema 子集校验）**；+6（v0.8.6：`response_format` 归一化）、+3（v0.8.6：`PromptRef`） |
| `tests/core/test_telemetry.py` | 5 | |
| `tests/harness/test_agent_auth.py` | 7 | v0.7.0：agent 接口口令鉴权 |
| `tests/harness/test_observability.py` | 17 | |
| `tests/harness/test_prompts.py` | 13 | **v0.8.6 新建：模板渲染、存储多版本、422 `PROMPT_INVALID`、版本落库** |
| `tests/harness/test_ratelimit.py` | 11 | **v0.8.6 新建：令牌桶算法与 429 出口** |
| `tests/harness/test_service.py` | 27 | **+1（v0.8.8：同 `trace_id` 的每次尝试都带路由快照）**；另两条既有降级用例按新语义改为「两条记录 + 按 `attempt_index` 取数」；**+5（v0.8.7：`output_valid` 落库与响应体、含失败/未请求/流式三种情形）**；**+2（v0.8.6：`response_format` 别名经接口可达）**；**+5（v0.8.4：流式降级）** |
| `tests/router/test_profile.py` | 29 | |
| `tests/router/test_profile_routing.py` | 30 | **+6（v0.8.7：`model` 点名排首位、裸 id 唯一匹配、不砍备用、越界如实报错、能力不匹配）** |
| `tests/router/test_retry.py` | 21 | |
| `tests/router/test_router.py` | 22 | **+2（v0.8.8：整条候选链依次试 + `on_attempt` 回调逐次上报）** |
| `tests/web/test_model_discovery.py` | 21 | **+5（v0.8.1：清单缓存）**；v0.8.0 建此文件（16 例） |
| `tests/web/test_web_api.py` | 46 | **+5（v0.8.6：`/api/prompts*`）**、**+12（v0.8.4：口令网页配置、通讯日志、Chat 直连）**、**+1（v0.8.5：密钥持久化）** |
| `tests/test_phase0_infra.py` | 4 | |
| `tests/test_runtime.py` | 11 | v0.7.0：agent API 与控制台同进程共存 |
| **合计** | **457** | **+3（v0.8.8：router +2、service +1）**；**+20（v0.8.7：schema +9、profile 路由 +6、service +5）**；**+44（v0.8.6：别名 +4、限流 +11、模板 +13、web +5、schema +9、service +2）** |

## 3. 需求要求的六类测试

需求第 137–144 行规定了至少六类测试。逐条对应：

| 需求类别 | 文件 | 覆盖内容 | 代表用例 |
|---|---|---|---|
| Adapter Contract Test | `tests/adapter/test_adapter_contract.py`、`test_advanced_config.py` | 每个 adapter 的**请求翻译**与**响应翻译**，由 `httpx.MockTransport` 驱动，不依赖真实供应商 | 逐协议断言请求体字段（`response_format`、`tool_choice`、`stream_options.include_usage`）、高级配置注入（`temperature` / `top_p` / `top_k` / `thinking`）、`extra_body` 覆盖 |
| router 测试 | `tests/router/test_router.py`、`test_profile_routing.py` | profile 作用域、能力匹配、静态顺序主备、**错误处置三选一（重试 / 降级 / 报错）**、候选拒绝原因 | `test_static_order_is_not_reordered_by_cost`、`test_profile_scopes_candidates`、`test_unknown_profile_fails_fast`、`test_decision_table_matches_requirement`、`test_execute_falls_back_to_backup_on_transient_error`（`trace.attempts == 5` / `trace.retries == 3`）、`test_execute_degrades_on_auth_failure_with_warning`、`test_execute_degrades_on_content_refusal_once_per_model`、`test_execute_does_not_degrade_on_request_invalid`、`test_execute_retries_then_degrades_after_max_retries`、`test_execute_tries_the_whole_candidate_chain_in_order`（profile 里 3 个模型全部被调用，`trace.attempt_index == 3`）、`test_execute_reports_every_attempt_to_the_callback`（回调逐次上报位次与降级来源） |
| 重试测试 | `tests/router/test_retry.py`、`test_profile_routing.py` | 用 mocktransport 模拟超时与错误，验证重试次数与退避策略，**不真的 sleep** | `test_retries_transient_error_with_backoff`（`assert clock.sleeps == [500, 1000]`）、`test_non_retryable_error_fails_fast`（`assert clock.sleeps == []`）、`test_provider_request_respects_retry_after_header`（`assert clock.sleeps == [300]`）、per-profile 策略 |
| streaming 测试 | `tests/adapter/test_streaming.py` | SSE 事件序列正确性：delta 顺序、usage 事件、终态、错误中断行为 | delta 顺序、usage 事件位置、`test_second_terminal_push_is_rejected`、中途错误中断 |
| structured output 测试 | `tests/adapter/test_structured_output.py` | JSON 提取、Schema 校验、**修复尝试次数**、失败时的错误返回 | JSON 围栏剥离、修复次数上限、超限抛 `OUTPUT_SCHEMA_INVALID`、流式终校验 |
| 可观测性 | `tests/harness/test_observability.py`、`test_service.py` | trace / Metrics / Cost Ledger 是否正确记录每次调用的关键字段，含**处置与降级事实落库** | TTFT 口径、时间窗指标、trace 链路顺序、脱敏生效、`profile` 落库、旧库迁移、`test_auth_failure_degrades_and_records_resilience`（**两条**记录：首条 `error_code=AUTH_INVALID` / `disposition=degrade`、第二条 `degraded_from=openai/gpt-4o-mini` / `fallback=1`）、`test_each_attempt_shares_trace_id_and_carries_route_snapshot`、`test_successful_call_records_no_disposition`、`test_post_task_response_carries_warnings` |

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

### 6.9 模型密钥跨重启持久化（v0.8.5）

0.8.4 及之前，`_persist_models` 落库时复用了**对外** payload（`api_key` 恒为 `None`），于是
**每次保存模型都会把密钥抹掉**，重启后 `/api/models` 六个模型全变"未配置"、调用回落到可能
已失效的环境变量密钥并 401。0.8.5 把落库改走 `model_to_stored_payload()`。

用干净库（`/tmp/v085.sqlite3`、端口 8052）验证，顺序是"建 → 杀进程 → 重启 → 读"：

```bash
# 1) 建一个带密钥的模型
$ curl -s -X POST localhost:8052/api/models -d '{..., "id":"key-probe", "api_key":"sk-persist-probe"}'
{"id":"key-probe", ..., "api_key":null, "api_key_set":true}      # 响应仍不回显密钥

# 2) 直接看落库的 config 表
$ sqlite3 /tmp/v085.sqlite3 "select value from config where key='models'"
[{"id": "key-probe", "api_key": "sk-persist-probe", "api_key_set": true}]   # 密钥确实进库了

# 3) 杀掉进程，用同一个库重启
$ LLM_GW_DB=/tmp/v085.sqlite3 uvicorn llm_gw.runtime:create_runtime_app --factory --port 8052
$ curl -s localhost:8052/api/models | ...                        # key-probe 那一条
{"id": "key-probe", "api_key": null, "api_key_set": true}        # 重启后仍是"已配置"
$ curl -s localhost:8052/api/models | grep -c 'sk-persist-probe'
0                                                                # 任何响应都不带明文密钥
```

三个点值得记下：

- **「只写不回显」管的是响应，不是持久化**：响应里 `api_key` 恒为 `null`（第 1、3 步），
  库里必须是明文（第 2 步）——两件事分开走，契约与落库各用一份 payload 工厂。
- **`api_key_set` 跨重启为 `true` 才是真的修好了**：只断言"POST 能存进去"是查不出这个缺陷的，
  因为内存注册表当时是对的，坏只坏在落库那一步、下一次启动才显形。
- 升级后**旧库里那份"无密钥"清单仍在**，需要在控制台重新填一次密钥；此后不会再被
  保存动作抹掉。

### 6.10 别名、提示词版本管理、按模型限流（v0.8.6）

用干净库起真进程（`LLM_GW_DB=/tmp/v086b.sqlite3`、端口 8054、
`LLM_GW_RATE_LIMIT_RPM=3 LLM_GW_RATE_LIMIT_BURST=3`），全程 `curl` 打真实 HTTP 入口。

**（a）`response_format` 别名**——验收口径按 OpenAI 的字段名发请求，因此这条必须先过：

```bash
$ curl -s -X POST localhost:8054/api/tasks:validate -d '{…,"response_format":{"type":"json_object"}}'
200 {"valid":true}
$ curl -s -X POST localhost:8054/api/tasks:validate -d '{…,"response_format":{"type":"json_schema",
      "json_schema":{"name":"s","schema":{"type":"object","properties":{"a":{"type":"string"}},"required":["a"]}}}}'
200 {"valid":true}
$ curl -s -X POST localhost:8054/api/tasks:validate -d '{…,"response_format":{"type":"xml"}}'
422 {"detail":{"code":"REQUEST_INVALID","message":"task does not match schema:\n  - input: Value error, 不支持的 response_format.type: 'xml'（可选 json_object / json_schema / text）"}}
```

两种 OpenAI 形态都被收下，且非法枚举给的是**可读原因**而不是一句"字段不对"。

**（b）提示词模板：存储 + 变量替换 + 版本引用**

```bash
$ curl -s -X POST localhost:8054/api/prompts -d '{"name":"summarize","version":"v1",
      "body":"你是{{persona}}。请用不超过 {{limit}} 字总结：\n{{article}}"}'
{"name":"summarize","version":"v1","variables":["article","limit","persona"],"created_at":1790080357.29496}

$ curl -s localhost:8054/api/prompts/summarize
summarize ['v2','v1']  created_at= [1790080357.402328, 1790080357.29496]
```

`variables` 是**从正文抽出来的**（调用方提交什么不采信），`created_at` 是落库那一刻的真实
时间戳——这正是 6.10 之前 `save_prompt` 直接返回入参、`created_at` 恒为 `0.0` 的那个缺陷。

引用模板：缺版本时**解析后才确定落到哪一版**，因此调用记录里记的是实际渲染的那一版：

```bash
$ curl -s -X POST localhost:8054/v1/tasks -H 'x-trace-id: ev-tpl-ok' -d '{…,"prompt":
      {"name":"summarize","variables":{"persona":"助手","limit":"20","article":"一段文章"}},…}'
$ curl -s localhost:8054/api/traces/ev-tpl-ok
deepseek-flash openai-completions | prompt: summarize v2 | sha: 4e002dc97b36 | total_ms: 157.4 | err: AUTH_INVALID
```

**（c）模板引用失败 → 422 `PROMPT_INVALID`**（不是 429、不是 5xx：这是请求本身不合法）

```bash
$ curl -s -X POST localhost:8054/v1/tasks -d '{"task_id":"ev-pm-1","prompt":{"name":"nope"},…}'
422 {"detail":{"code":"PROMPT_INVALID","message":"模板 nope 不存在"}}
$ curl -s -X POST localhost:8054/v1/tasks -d '{"task_id":"ev-pm-2","prompt":{"name":"summarize","version":"v1"},…}'
422 {"detail":{"code":"PROMPT_INVALID","message":"模板 summarize@v1 缺少变量: persona, limit, article"}}
```

**（d）按模型独立限流**（RPM=3，突发 3）

```bash
$ for i in 1 2 3 4 5 6; do curl -s -o out -w "%{http_code} " -X POST localhost:8054/v1/tasks \
      -H "x-trace-id: ev-rl-$i" -d '{…,"profile":"oa",…}'; done
200 200 200 429 429 429

# 第 4 次的两样东西都要看：状态码与响应头
HTTP/1.1 429
retry-after: 18
{"detail":{"code":"RATE_LIMITED","message":"模型 openai/gpt-4o-mini 已触达本地限流（3 RPM），请在 18 秒后重试"}}

# 换另一个模型，桶是独立的
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8054/v1/tasks -d '{…,"profile":"ds",…}'
200
```

**（e）被拒的请求也留痕**（否则页面上看不到"被打回"的流量）：

```bash
$ curl -s 'localhost:8054/api/exchanges?task_id=ev-rl-4'
[('error','RATE_LIMITED')]
$ curl -s 'localhost:8054/api/exchanges?task_id=ev-pm-1'
[('error','PROMPT_INVALID')]
```

**（f）两个模型都真的打出去了**（各带独立的 trace-id，互不污染）：

```bash
$ curl -s -X POST localhost:8054/v1/tasks -H 'x-trace-id: ev-final-oa' -d '{…,"profile":"oa",…}'
HTTP 200  terminal=error  AUTH_INVALID: AUTH_INVALID: provider (401): {"error":{"message":"You didn't provide an API key.…"}}
$ curl -s -X POST localhost:8054/v1/tasks -H 'x-trace-id: ev-final-ds' -d '{…,"profile":"ds",…}'
HTTP 200  terminal=error  AUTH_INVALID: AUTH_INVALID: provider (401): Authentication Fails (governor)
```

两条都走到了上游、被上游以 401 打回——**这是"没有真实密钥"的诚实结论，不是"网关没接通"**：
适配器选对了协议、请求体翻译成功、错误被归一成 `AUTH_INVALID` 并落库。
要看到"成功的那一次"，用不访问真实上游的用例（第 4 节 `test_adapter_contract.py`、
`test_structured_output.py`，以及 6.2 的本地假上游）。

三段值得记下的取舍：

- **校验 → 模板解析 → 限流**的顺序是刻意的。(c) 那两条本该 422 的请求若先撞限流，
  调用方会收到 429 并以为"等 18 秒重发同样的请求就能成功"——那是错的。
- **流式入口的限流必须在建流之前判**：`StreamingResponse` 一返回，HTTP 状态码就固定 200 了，
  再想表达 429 只能塞进 SSE 事件里，那不叫"超限返回 429"。
- **429 不进重试**：重试只会让桶更空，还会把"该等多久"藏起来。`Retry-After` 由桶的空缺量与
  补充速率算出（第 4 次 18 秒 ≈ 缺 1 个令牌 ÷ 3/60 个每秒），不是写死的常数。

### 6.11 独立验证脚本 `scripts/verify.py`（v0.8.7）

第 6 节前几小节是**一次性的**实机验证（起服务、敲 curl、把输出抄下来存档）。它们能证明
当时确实跑通了，但别人复现要靠重敲一遍命令。`scripts/verify.py` 把这些动作固化成脚本：
自起**本地假上游**（一个进程同时提供 `openai-completions` 与 `anthropic-messages` 两套协议，
按真实协议逐块 flush 回包、usage 分两处上报）→ 起**真实网关进程**指向它 → 真实 HTTP 请求
逐项断言，**109 项**，退出码即结论。

```console
$ .venv/bin/python scripts/verify.py
假上游：http://127.0.0.1:54882/v1
网关（不限额）：http://127.0.0.1:54883

1. 统一抽象层（协议翻译）
  PASS  [verify-oa] 同一份 task 换 profile 即可调用
  PASS  [verify-an] 同一份 task 换 profile 即可调用
  PASS  OpenAI 协议：密钥翻成 Authorization: Bearer   — authorization=Bearer sk-verify-openai
  PASS  Anthropic 协议：密钥翻成 x-api-key（同一次调用，调用方无感）   — x-api-key=sk-verify-anthropic
  PASS  Anthropic 协议：带上 anthropic-version 头
  PASS  请求体结构：OpenAI 把 system 放进 messages[0]
  PASS  请求体结构：Anthropic 把同一个 system 提到顶层字段
  PASS  Anthropic 必填字段 max_tokens 被补齐

1b. 按 model 字段路由（点名即换适配器）
  PASS  model=openai/gpt-4o-mini 被路由到 openai-completions   — terminal=done api=openai-completions model=gpt-4o-mini
  PASS  model=openai/gpt-4o-mini 的上游请求真的打到了 /chat/completions
  PASS  model=anthropic/claude-3-5-haiku-20241022 被路由到 anthropic-messages   — terminal=done api=anthropic-messages model=claude-3-5-haiku-20241022
  PASS  model=anthropic/claude-3-5-haiku-20241022 的上游请求真的打到了 /messages
  PASS  裸 id（唯一匹配）也能点名
  PASS  点名不存在的模型 → 如实 ROUTE_NO_CANDIDATE   — ROUTE_NO_CANDIDATE: model openai/ghost 不存在（请用 provider/id 形态或唯一的 id）
  PASS  点名 profile 池子外的模型（verify-oa 里只有 openai） → 如实 ROUTE_NO_CANDIDATE   — ...不在 profile verify-oa 的候选池里

2. 流式输出（stream=true，SSE 逐块）
  PASS  [verify-oa] HTTP 200 且 content-type 是 text/event-stream
  PASS  [verify-oa] 收到多个 text_delta（真逐块，不是一次性回包）   — 4 个 delta
  PASS  [verify-oa] 拼出的正文与上游一致   — '你好，这是流式的分片输出。'
  PASS  [verify-oa] 终态是 done
  PASS  [verify-oa] done 之后有 data: [DONE]
  PASS  [verify-oa] 只出现一个终态事件
  PASS  [verify-an] ...（同上六项，另一套协议）

3. 结构化输出（response_format）
  PASS  response_format 别名 json_object 被接受   — HTTP 200
  PASS  response_format 别名 json_schema 被接受   — HTTP 200
  PASS  非法 response_format.type 被 422 挡下并给出可读原因
  PASS  [verify-oa] json_object：正文是合法 JSON   — '{"city": "上海", "score": 0.93}'
  PASS  [verify-an] json_object：正文是合法 JSON   — '{"city": "上海", "score": 0.93}'
  PASS  [verify-oa / verify-an] 兑现与否被判定并落库：output_valid=True   — 响应=True 记录=True
  PASS  OpenAI 协议：约束走请求体里的 response_format
  PASS  Anthropic 协议：没有 response_format 字段，约束改写进 system   — 'You must reply with a single valid JSON value and nothing el'
  PASS  json_schema：返回正文是合法 JSON 且与 schema 对齐   — {'city': '北京', 'score': 0.71}
  PASS  json_schema：schema 被翻译成上游认的形态   — {"type": "json_schema", "json_schema": {...}}
  PASS  能力表如实的模型不会被硬塞 schema 请求（如实报 ROUTE_NO_CANDIDATE）   — ROUTE_NO_CANDIDATE: 全部候选被拒
  PASS  json_schema：结构对得上 schema 才算兑现（output_valid=True）   — True
  PASS  上游没回 JSON 时：调用成功但如实标记 output_valid=False   — terminal=done 响应=False 记录=False text='ok'
  PASS  未请求结构化输出时 output_valid 为 null（不把『没要求』记成『不合格』）   — 响应=None 记录=None

4. 提示词版本管理（模板存储 / 变量替换 / 版本引用）
  PASS  模板 v1 / v2 存储成功
  PASS  两个版本都在库里，新的在前   — ['v2', 'v1']
  PASS  不存在的模板 → 422 PROMPT_INVALID
  PASS  漏给变量 → 422 PROMPT_INVALID   — 模板 summarize@v1 缺少变量: persona, limit, article
  PASS  [verify-oa] 模板渲染进 system：变量已替换
  PASS  [verify-oa] 模板当背景、调用方 system 当即时指令（两者都在）
  PASS  [verify-oa] 调用记录记下实际引用的版本 summarize@v2   — summarize@v2
  PASS  [verify-oa] 记录带 prompt 指纹
  PASS  ...（[verify-an] 同上五项）
  PASS  显式指定 version=v1 时渲染的是 v1 正文
  PASS  记录里的版本随引用变成 v1

5. 可观测性（Token 分类统计 + 首 Token 延迟）
  PASS  [verify-oa] 记录里 model / provider / api 三项齐全   — openai/gpt-4o-mini api=openai-completions
  PASS  [verify-oa] input_tokens 分类统计正确   — 1234
  PASS  [verify-oa] output_tokens 分类统计正确   — 56
  PASS  [verify-oa] 缓存与思考类 token 也各有独立字段（无则为 0/None，不混进 input）
  PASS  [verify-oa] total_tokens = input + output   — 1290
  PASS  [verify-oa] 成本按费率算出来了   — 0.0002187
  PASS  [verify-oa] 首 Token 延迟被单独测出来（≥ 上游首个分片的等待）   — ttft_ms=127.5
  PASS  [verify-oa] 总延迟 ≥ 首 Token 延迟   — total_ms=197.5, ttft_ms=127.5
  PASS  [verify-oa] 流式分片数被记下来（字段确实来自流式路径）   — 9
  PASS  ...（[verify-an] 同上九项，ttft_ms=130.5 / total_ms=195.5）
  PASS  Dashboard 聚合可用

两个模型调用均可正常工作
  PASS  [verify-oa → openai/gpt-4o-mini] 非流式调用成功并返回正文   — terminal=done text='两个模型都可用。'
  PASS  [verify-oa] usage 回传给调用方   — {'input': 1234, 'output': 56, 'total_tokens': 1290, 'cost': 0.0002187}
  PASS  [verify-an → anthropic/claude-3-5-haiku-20241022] 非流式调用成功并返回正文
  PASS  [verify-an] usage 回传给调用方   — {... 'cost': 0.0012112000000000002}

6a. 韧性（指数退避重试）
  PASS  上游连续两次 500 后最终返回 200   — HTTP 200
  PASS  正文来自第 3 次尝试   — 重试之后成功。
  PASS  调用记录 attempt=3（首次 + 2 次重试）   — 3
  PASS  重试计数与 attempt 一致   — 2
  PASS  退避是真等待（≥ 500ms + 1000ms 的量级）   — 1.66s
  PASS  持续失败时不会无限重试（封顶 3 次重试后如实报错）   — terminal=error attempt=4 code=UPSTREAM_OVERLOADED

网关（RPM=3）：http://127.0.0.1:54840

6b. 韧性（按模型独立限流，RPM=3）
  PASS  前 3 个请求放行、第 4 个超限   — [200, 200, 200, 429]
  PASS  超限返回 429 + Retry-After 响应头   — retry-after=20
  PASS  429 正文带稳定错误码 RATE_LIMITED   — {'code': 'RATE_LIMITED', 'message': '模型 openai/gpt-4o-mini 已触达本地限流（3 RPM），请在 20 秒后重试'}
  PASS  另一个模型有自己的桶（A 被限住不影响 B）   — HTTP 200
  PASS  被限流的请求也在通讯日志里留痕   — [('error', 'RATE_LIMITED')]

------------------------------------------------------------------------
结果：全部 96 项通过。
```

三处刻意的设计（都是为了让证据"可复现"而不是"看起来通过"）：

- **真进程、真 HTTP、真 SSE**：网关是 `uvicorn` 子进程，请求走真实 socket，流式验证读的是
  字节流的 `event:` / `data:` 帧。全程不 mock 网关内部，避免"测试通过但代码没跑"。
- **假上游不是"随便回个 200"**：它按两套协议各自的结构回包（OpenAI 的 `choices[].delta`、
  Anthropic 的 `content_block_delta`），usage 也按各自的位置上报，首个分片前先等
  `FIRST_CHUNK_DELAY_S=0.12` 秒——因此第 5 节的 `ttft_ms=127.5` 是**真的被测出来**的，
  而不是恒等于 0 的占位值。
- **限流要另起一个网关**：`policy_from_env` 在进程启动时读环境变量，所以脚本用第二个实例
  （`RPM=3`）验限流，第一个保持不限额——否则限流会把前面所有功能项也一起限住。

### 6.12 两处此前的空档已补齐（v0.8.7）

第 6.11 节首版跑完后如实记下两个空档，v0.8.7 都已接线，脚本的断言也随之增加：

- **路由键**：此前只能靠 `profile`（"池子"）侧面路由，请求里的顶层 `model` 字段不被
  schema 接受。现已实现「**profile 定池子、model 定点名**」——点名只把该模型提到首位、
  其余候选仍作备用，且不允许点名 profile 池子外的模型。`scripts/verify.py` 新增 §1b
  逐项验证：`model=openai/gpt-4o-mini` 与 `model=anthropic/claude-3-5-haiku-20241022`
  分别被路由到两套适配器，且上游请求**真的**打到各自的端点（`/chat/completions`、
  `/messages`）——这正是需求"根据请求中的 `model` 字段动态路由到对应适配器"的端到端证据。
- **`CallRecord.output_valid`**：此前 schema 与库里都有列却无人写入、恒为 `None`。现已
  在每次调用结束时判定并落库，且是**三分语义**（`None` = 未请求/失败取消，`True` = 兑现，
  `False` = 请求了没兑现）。脚本 §3 的三条断言分别覆盖这三种取值，响应体与落库记录都核对。

### 6.13 降级到底有没有生效（v0.8.8）

起因是一句很具体的反馈：**「profile 里配了 4 个模型，但感觉降级没实现」**。实测后确认
降级逻辑本身一直在跑，坏的是三件让人看不见、也走不远的事：

| 现象 | 根因 | 修法 |
|---|---|---|
| profile 里 4 个模型，永远只试 2 个 | 每次尝试只取 `[primary, backup]` | 改按 `Decision.candidates` **整条**候选链依次往下试 |
| Trace 里只有一条记录，"降级成功"看不出发生 | 一次请求只落最后一条 `CallRecord` | **每次尝试各落一条**，共享 `trace_id`、用 `attempt_index` 标位次 |
| 降级成功，记录的 `model` 却是主路由 | `complete()` 落库时没传实际模型 | 按每次尝试的**实际**模型落库 |
| 看不到"为什么选它 / 为什么没降级" | 决策依据没落库 | 调用记录新增 `route` 快照（`reason` / `candidates` / `rejected`） |

端到端证据（脚本 §6c，新增 13 项断言）：给假上游加「**按路径恒失败**」（`fail_paths`
+ 可配 `fail_status`），把 OpenAI 那条路（`/chat/completions`）打成 401 → 落到
`AUTH_INVALID` → 处置是 **DEGRADE（直接换模型，不重试同一个）**；Anthropic 那条路
（`/messages`）保持正常，降级的终点就是它。路由表是 4 个模型的**静态**顺序，关掉重试好
让"每个模型恰好被调用一次"可以被精确断言。

```console
6c. 韧性（候选链降级：4 个模型依次尝试）
  PASS  前三个模型都不可用时，请求最终仍然成功（降级到第 4 个）   — terminal=done text='降级之后才成功。'
  PASS  候选链上 4 个模型**依次都试过**   — ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'claude-3-5-haiku-20241022']
  PASS  每次尝试各落一条调用记录（4 次尝试 = 4 条记录）   — 4 条：['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'claude-3-5-haiku-20241022']
  PASS  4 条记录共享同一个 trace_id（一次请求就是一条链路）   — {'verify-degrade-chain'}
  PASS  位次 attempt_index 从 1 连续到 4   — [1, 2, 3, 4]
  PASS  失败的那几条如实记下错误码与处置（不再只剩一条『成功』记录）   — error_code=AUTH_INVALID disposition=degrade
  PASS  首跳没有『从谁降级而来』   — ''
  PASS  后续每条都标出『从哪个模型降级而来』，降级方向可读   — ['openai/gpt-4o-mini', 'openai/gpt-4o', 'openai/gpt-4.1-mini']
  PASS  整条链路都被标为 fallback（降级不是隐形的）   — [True, True, True, True]
  PASS  最终成功的记录写着真正出力的那个模型（不再写成主路由）   — model=claude-3-5-haiku-20241022 api=anthropic-messages error_code=None
  PASS  每条记录都带路由决策快照：候选链 4 个标签   — ['openai/gpt-4o-mini', 'openai/gpt-4o', 'openai/gpt-4.1-mini', 'anthropic/claude-3-5-haiku-20241022']
  PASS  路由决策快照给出依据（为什么是这几个模型、这个顺序）   — '命中 profile verify-degrade 的静态路由；候选链 openai/gpt-4o-mini → openai/gpt-4o → openai/gpt-4.1-mini → anthropic/claude-3-5-haiku-20241022'
  PASS  关掉重试后每个模型恰好被调用一次（降级 = 换模型，不是原地重打）   — [('gpt-4o-mini', 1), ('gpt-4o', 1), ('gpt-4.1-mini', 1), ('claude-3-5-haiku-20241022', 1)]
```

单元层同一件事各有一例：`test_execute_tries_the_whole_candidate_chain_in_order`（3 个模型
全部被调用、`trace.attempt_index == 3`）、`test_execute_reports_every_attempt_to_the_callback`
（回调逐次上报位次与降级来源）、`test_each_attempt_shares_trace_id_and_carries_route_snapshot`
（同 `trace_id` 下两条记录、候选链快照一致）。

## 7. 复现方式

```bash
# 后端（含覆盖率）
.venv/bin/python -m pytest tests -q --cov=llm_gw

# 端到端验证脚本（第 6.11 节，109 项断言，不需要任何真实密钥）
.venv/bin/python scripts/verify.py

# 前端
cd webapp && npm install && npm test

# 起服（推荐脚本：自动激活 venv 并加载 .env）
./run.sh
.venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000
```

所有 adapter 与模型发现测试均由 `httpx.MockTransport` 驱动，重试、限流与缓存过期测试由 `FakeClock` 驱动——**不需要任何真实供应商密钥**即可跑完全部 457 个用例。仅第 6 节的端到端验证会真的访问上游（6.1 预期收到 `AUTH_INVALID`；6.2 用本地假上游，同样不需要真实密钥；6.5 只验证鉴权层，请求在选模型之前就被拒绝；6.6 会真的访问供应商的 `/models` 与 `/chat/completions`，预期收到 401 并降级；6.7 同理会真的打一次上游并收到 401，因此**也不需要有效密钥**；6.8 用本地假上游，同样不需要真实密钥；6.9 用干净库起停两次，全程不访问任何上游；6.10 的模板与限流两段不访问上游，两个模型那一段会真的打出去并收到 401——**因此同样不需要有效密钥**；6.11 的验证脚本用本地假上游，全流程**一次上游都不访问，也不需要任何真实密钥**）。

> 复现 v0.8.2 的 preset 验证时，记得用干净库（`LLM_GW_DB=/tmp/fresh.sqlite3`）：用默认的
> `llm_gw.sqlite3` 会被里面已保存的模型清单覆盖，看到的仍是旧型号，详见第 6.6 节。
