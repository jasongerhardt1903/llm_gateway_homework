# LLM Gateway

统一的大模型网关：为后端 agent 提供一致的 LLM 接入接口，并在内部完成
**路由、降级、重试、流式、可观测**。当前版本 **0.8.8**（见 [CHANGELOG.md](CHANGELOG.md)）。

架构上分五层，依赖方向单向（左依赖右）：

```
LLM ← adapter ← 路由层 ← Harness 层 ← service
                    ↑
                 gwprofile 层
```

- **core**：消息、任务 schema、错误模型、遥测记录、高级配置（纯数据，无 I/O）。
- **adapter**：按协议（openai / anthropic）调用上游，SSE 解析、错误归一化。
- **router**：`gwprofile` 定作用域 → 静态优先 → 动态筛选 → 主备。
- **harness**：对外 HTTP + SSE，两层校验、单一终态、错误处置三选一（重试 / 降级 / 报错）、落库可观测。
- **web / webapp**：面向人的控制台（模型定义 / Profile / Chat / Dashboard / Trace）。

细节见 [docs/architecture.md](docs/architecture.md)。

## 运行

### 1. 创建虚拟环境

```bash
cd LLM_GW
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

> 机器上若装了多个 Python，请始终用 `.venv/bin/python`，不要用裸 `python3` / `uvicorn`，
> 否则可能撞上没有依赖的解释器（`ModuleNotFoundError: uvicorn`）。

### 2. 配置密钥（可选，也可稍后在控制台里填）

密钥按以下优先级生效：

1. **Web 控制台在模型上配置的 `api_key`**（写入 SQLite，界面只回显"已配置/未配置"，不回显明文）；
2. 环境变量 —— 供应商 preset 约定的变量名，如 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY`。

两种方式任选其一，环境变量可写进 `.env`（该文件已被 `.gitignore` 排除）：

```bash
cp /dev/null .env   # 若不存在
cat >> .env <<'EOF'
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
EOF
```

### 3. 启动

```bash
./run.sh                      # 默认 http://127.0.0.1:8000
./run.sh --port 9000          # 覆盖端口（其余参数原样透传给 uvicorn）
./run.sh --reload             # 开发模式，改代码自动重启
LLM_GW_HOST=0.0.0.0 ./run.sh  # 局域网可访问
```

等价的手工命令：

```bash
.venv/bin/python -m uvicorn llm_gw.runtime:create_runtime_app --factory --port 8000
```

`run.sh` 会自动激活虚拟环境、加载 `.env`，并提示访问地址。

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_GW_DB` | `./llm_gw.sqlite3` | SQLite 数据库路径 |
| `LLM_GW_HOST` | `127.0.0.1` | 监听地址（`0.0.0.0` 供局域网访问） |
| `LLM_GW_PORT` | `8000` | 监听端口（也可用 `--port` 覆盖） |
| `LLM_GW_AGENT_PASSWORD` | 空（不强制） | agent 接口（`/v1/tasks*`）口令。设置后要求 `Authorization: Bearer <password>`；未设置则放行。**优先于**控制台网页上配置的口令 |
| `LLM_GW_RATE_LIMIT_RPM` | 空（不限额） | 按模型独立限流的每分钟请求数；`<=0` 或未设 = 不限额。超限返回 `429` + `Retry-After` |
| `LLM_GW_RATE_LIMIT_BURST` | `ceil(rpm)` | 令牌桶容量（允许的瞬时突发） |
| `OPENAI_API_KEY` 等 | 空 | 上游密钥，回退路径 |

## 控制台用法

打开 `http://127.0.0.1:8000`，左侧导航六个入口：

1. **模型定义** —— 增删改模型：供应商、实际模型 ID、base URL、上下文窗口、能力、成本；
   以及**高级配置项**（`temperature` / `top_p` / `top_k` / 工具调用轮数 / 思考模式）、
   模型 **Tag** 与 **API Key**（只写不回显，留空表示不修改，勾选"清除"才清空）。
   高级配置项留空 = 请求时**不发送该参数**，交由供应商默认。
2. **Profile** —— 编组模型并配置路由。每个 profile 包含：
   - 模型清单，每个模型可勾选 **"本模型配置优先于模版"**；
   - 可选的**统一高级配置模版**（启用后，未勾选"优先"的模型一律用模版值）；
   - **路由模式**：`dynamic`（动态打分）或 `static`（静态）。静态模式下的**路由表用拖拉拽
     配置**：左侧是已选模型，拖到右侧组成路由链，右侧内部可上下拖拽排序，**执行自上而下**；
     profile 名即路由表名；
   - 重试策略（是否重试、最大次数）。
3. **Chat** —— 选一个 Profile（留空走全局模型池）后对话。Chat 页本就是"一个简单的后端
   agent Loop"，因此**直接按 agent 的 `Task` schema 调 `/v1/tasks:stream`**，不再经过控制台
   转发；页面上填 agent 口令（`Authorization: Bearer`），实时消费 SSE 流；
   `thinking_delta` 折叠展示，`error` / `cancelled` 终态标红且**不会**收到 `[DONE]`。
   勾选**「带上 tools」**即可看到**多轮工具调用循环**：网关不跑这个循环，页面收到
   `tool_call` 后本地执行内置假工具（`get_time` / `echo`）、把 `tool_result` 塞回
   `messages` 再调一次，直到模型不再要工具（最多 4 轮）；循环里的几次通讯共用同一个
   `task_id`，可在 Trace 的通讯日志里按 `flow_index` 看到完整序列。
4. **Dashboard** —— 聚合指标：QPS、p50/p99 延迟、错误率、成本、模型健康。
5. **Trace** —— 三种切法：按关键字（trace_id / call_id / 模型 / prompt 名 / 错误信息）搜索
   调用记录，点开某条按 8 个维度查看整条链路；任务瀑布；以及**通讯原始日志**——按
   `task_id` / 每次通讯流程两级折叠组织，每个字段一列，展开后可切换 **raw data 模式**
   与**渲染后易读模式**（JSON 美化 / SSE 逐帧）。
6. **设置** —— 配置 agent 接口口令（落 `config` 表）或清除；页面如实提示当前口令来源
   （`env` / `console` / `none`）与对应的环境变量名。**环境变量优先于网页配置**。

## API 用法（curl）

启动后 agent 通过 `POST /v1/tasks`（非流式）或 `POST /v1/tasks:stream`（流式）提交统一的
`Task`。若配了 `LLM_GW_AGENT_PASSWORD`，请给所有 `/v1/tasks*` 请求带上
`-H "Authorization: Bearer $LLM_GW_AGENT_PASSWORD"`（下例用变量 `$AUTH` 代替，未配口令时可整行删除）。

完整 schema 见 [docs/interface.md](docs/interface.md) 第 1 节。

### 0. 按 `model` 字段点名路由（换模型即换适配器）

`profile` 定候选池、`model` 在池内点名。点名 `anthropic/...` 时网关自动切到
`anthropic-messages` 适配器（把密钥翻成 `x-api-key`、system 提到顶层、补 `max_tokens`），
点名 `openai/...` 时走 `openai-completions`——调用方写的是同一份 `Task`，无感。

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "content-type: application/json" $AUTH \
  -d '{
    "task_id": "demo-model-route",
    "profile": "default",
    "model": "anthropic/claude-3-5-haiku-20241022",
    "input": {"messages": [{"role": "user", "content": "你好"}], "stream": false}
  }'
```

点名只把该模型提到首位，**其余候选仍作备用**；点名 profile 池子外的模型会如实返回
`ROUTE_NO_CANDIDATE`（而不是悄悄绕过 profile 用它）。裸 `id`（如 `gpt-4o-mini`）
仅在唯一匹配时认。

### 1. 流式输出（`stream=true`，SSE 逐块返回）

```bash
curl -N -X POST http://127.0.0.1:8000/v1/tasks:stream \
  -H "content-type: application/json" $AUTH \
  -d '{
    "task_id": "demo-stream",
    "profile": "default",
    "input": {
      "messages": [{"role": "user", "content": "用三句话介绍你自己"}],
      "stream": true
    }
  }'
```

响应是 `text/event-stream`：先 `event: start`，随后若干 `event: text_delta`，
最后 `event: done` + `data: [DONE]`。错误/取消的终态**不发** `[DONE]`——客户端据此区分
"正常结束"与"异常结束"。事件清单见 [docs/interface.md](docs/interface.md) 第 3 节。

### 2. 结构化输出（`response_format` 约束合法 JSON）

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "content-type: application/json" $AUTH \
  -d '{
    "task_id": "demo-json-schema",
    "input": {
      "messages": [{"role": "user", "content": "北京今天天气如何？"}],
      "response_format": {
        "type": "json_schema",
        "json_schema": {
          "name": "weather",
          "strict": true,
          "schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "temp_c": {"type": "number"}},
            "required": ["city", "temp_c"],
            "additionalProperties": false
          }
        }
      }
    }
  }'
```

`response_format` 是 OpenAI 形态的别名，网关会把它归一化到 `input.response_schema`
（也可直接写 `response_schema`，两者不能同时给）。只要求"返回合法 JSON"、不限结构时用：

```bash
  -d '{"task_id":"demo-json-object","input":{"messages":[{"role":"user","content":"给三个水果名"}],
       "response_format":{"type":"json_object"}}}'
```

响应体与调用记录里都会带 `output_valid`，如实说明**上游是否真的兑现了约束**：
`true` = 兑现；`false` = 调用成功但正文不是合法 JSON / 不合 schema；`null` = 未请求结构化
输出，或调用失败/取消（没有可判定的输出）。语义见 [docs/interface.md](docs/interface.md) 第 1 节。

### 3. 提示词模板引用（存储 / 变量替换 / 版本引用）

先存模板（控制台 API，键为 `name + version`；`variables` 由正文的 `{{占位符}}` 自动推导）：

```bash
curl -X POST http://127.0.0.1:8000/api/prompts \
  -H "content-type: application/json" \
  -d '{
    "name": "summarize",
    "version": "v1",
    "body": "你是 {{persona}}。用不超过 {{limit}} 个字总结下面这段内容：\n{{article}}"
  }'

curl http://127.0.0.1:8000/api/prompts/summarize     # 查该 name 的全部版本
```

再在调用里引用它（`version` 省略则取最新版）：

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "content-type: application/json" $AUTH \
  -d '{
    "task_id": "demo-prompt",
    "input": { "messages": [{"role": "user", "content": "开始吧"}] },
    "prompt": {
      "name": "summarize",
      "version": "v1",
      "variables": {"persona": "严谨的编辑", "limit": "30", "article": "……"}
    }
  }'
```

模板会被渲染进 `input.system`。模板不存在、或变量缺一个/多一个，都返回
**422 `PROMPT_INVALID`**——变量名拼错是模板最常见的故障，静默忽略只会让模型收到带空洞的提示。

### 4. 可观测数据（Token 分类统计 + 延迟含首 Token 延迟）

每次调用（含流式）都落一条记录，非流式调用的响应体里就带用量：

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "content-type: application/json" $AUTH \
  -d '{"task_id":"demo-obs","input":{"messages":[{"role":"user","content":"hi"}]}}' \
  | python3 -m json.tool
```

```jsonc
{
  "task_id": "demo-obs",
  "terminal": "done",
  "text": "……",
  "usage": { "input": 12, "output": 9, "total_tokens": 21, "cost": 0.0000234 },
  "warnings": []
}
```

更细的记录（缓存读/写 token、推理 token、`ttft_ms` / `generation_ms` / `total_ms`、
`attempt` / `retry` / `fallback`、prompt 名与版本）可从控制台查：

```bash
curl "http://127.0.0.1:8000/api/traces?q=demo-obs"
curl "http://127.0.0.1:8000/api/dashboard"      # 聚合 QPS / p50 / p99 / 错误率 / 成本
```

`ttft_ms` 以**第一个有业务意义的 delta** 为准，不把 `start` 事件算作首 token
（否则会系统性低估）。字段口径见 [docs/interface.md](docs/interface.md) 第 4 节的
`GET /api/traces/{trace_id}`。

### 5. 重试（指数退避，最多 3 次）

上游返回可重试错误（连接失败 / 429 / 5xx 过载）时，网关按 `gwprofile` 的重试策略做
**指数退避**重试，最多 3 次；重试次数落进调用记录的 `attempt` / `retry` 字段。

```bash
curl -X POST http://127.0.0.1:8000/api/profiles \
  -H "content-type: application/json" \
  -d '{"name":"retry-demo","models":[{"label":"deepseek/deepseek-flash"}],
       "retry_enabled":true,"max_retries":3}'
```

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "content-type: application/json" $AUTH \
  -d '{"task_id":"demo-retry","profile":"retry-demo",
       "input":{"messages":[{"role":"user","content":"hi"}]}}'
```

随后 `curl "http://127.0.0.1:8000/api/traces?q=demo-retry"` 即可看到 `attempt` / `retry` 与
是否降级（`fallback`）。错误码与处置决策表见 [docs/error-codes.md](docs/error-codes.md)。

### 6. 限流（按模型独立，超限 429）

默认不限额（本地开发不该被绊住）。限额通过环境变量开启，**粒度是模型**：

```bash
LLM_GW_RATE_LIMIT_RPM=3 LLM_GW_RATE_LIMIT_BURST=3 ./run.sh
```

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -w "%{http_code} " -X POST http://127.0.0.1:8000/v1/tasks \
    -H "content-type: application/json" $AUTH \
    -d "{\"task_id\":\"demo-rl-$i\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}}"
done; echo
# 200 200 200 429
```

第 4 次返回 `429`，响应体 `detail.code = RATE_LIMITED`，响应头带 `Retry-After: <秒>`。
桶按模型标签各自持有，A 被限住不影响 B；流式端点在**建流之前**判定，因此也是实打实的 429。

### 7. 两个模型都能用

给同一个任务换 `profile` 或换模型标签，即可验证两套协议（OpenAI / DeepSeek 走
`openai-completions`，Anthropic 走 `anthropic-messages`）都通：

```bash
for label in openai/gpt-4o-mini deepseek/deepseek-flash; do
  curl -X POST http://127.0.0.1:8000/api/profiles \
    -H "content-type: application/json" \
    -d "{\"name\":\"one-$label\",\"models\":[{\"label\":\"$label\"}]}"
done
```

之后用 `"profile": "one-openai/gpt-4o-mini"`（或另一条）发 `/v1/tasks` 即可。

## 测试

```bash
# 后端
.venv/bin/python -m pytest tests -q --cov=llm_gw

# 端到端验证脚本（验收清单六大功能点 + 候选链降级，109 项断言）
.venv/bin/python scripts/verify.py

# 前端
cd webapp
npm install
npm test          # vitest
npm run build     # 产物到 webapp/dist，由后端静态托管
```

`scripts/verify.py` 自己起一个**本地假上游**（同一进程同时提供 `openai-completions` 与
`anthropic-messages` 两套协议，按真实协议逐块回包），再起真正的网关进程指向它，然后用
真实 HTTP 请求验证统一抽象层、流式、结构化输出、模板版本管理、可观测性、重试与限流，
以及**候选链降级**（坏模型一路换到能用的那个，每次尝试各落一条调用记录）。
**不需要任何真实密钥，可离线复现**：全部断言通过时打印 `结果：全部 109 项通过。`，
有失败项时逐条打印 `FAIL 名称 — 实测值` 并以非零码退出，可直接当 CI 门禁用。
要打真实供应商，把控制台里的 `base_url` 换成官方地址即可，会走完全相同的代码路径。

前端开发模式（热更新，`http://localhost:5173`）：

```bash
cd webapp && npm run dev
```

## 文档

| 文件 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 五层架构与 gwprofile 层 |
| [docs/interface.md](docs/interface.md) | task schema 与 `/api/*` 契约 |
| [docs/error-codes.md](docs/error-codes.md) | 稳定错误码与处置决策表（重试 / 降级 / 报错） |
| [docs/retry-strategy.md](docs/retry-strategy.md) | 重试、退避、降级与 per-profile 策略 |
| [docs/test-evidence.md](docs/test-evidence.md) | 测试证据存档 |
| [CHANGELOG.md](CHANGELOG.md) | 版本更新说明 |
