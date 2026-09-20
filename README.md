# LLM Gateway

统一的大模型网关：为后端 agent 提供一致的 LLM 接入接口，并在内部完成
**路由、降级、重试、流式、可观测**。当前版本 **0.7.0**（见 [CHANGELOG.md](CHANGELOG.md)）。

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
| `LLM_GW_AGENT_PASSWORD` | 空（不强制） | agent 接口（`/v1/tasks*`）口令。设置后要求 `Authorization: Bearer <password>`；未设置则放行 |
| `OPENAI_API_KEY` 等 | 空 | 上游密钥，回退路径 |

## 控制台用法

打开 `http://127.0.0.1:8000`，左侧导航五个入口：

1. **模型定义** —— 增删改模型：供应商、实际模型 ID、base URL、上下文窗口、能力、成本；
   以及**高级配置项**（`temperature` / `top_p` / `top_k` / 工具调用轮数 / 思考模式）、
   模型 **Tag** 与 **API Key**（只写不回显，留空表示不修改，勾选"清除"才清空）。
   高级配置项留空 = 请求时**不发送该参数**，交由供应商默认。
2. **Profile** —— 编组模型并配置路由。每个 profile 包含：
   - 模型清单，每个模型可勾选 **"本模型配置优先于模版"**；
   - 可选的**统一高级配置模版**（启用后，未勾选"优先"的模型一律用模版值）；
   - **路由模式**：`dynamic`（动态打分）或 `static`（静态，用逗号分隔写死优先顺序）；
   - 重试策略（是否重试、最大次数）。
3. **Chat** —— 选一个 Profile（留空走全局模型池）后对话，实时消费 SSE 流；
   `thinking_delta` 折叠展示，`error` / `cancelled` 终态标红且**不会**收到 `[DONE]`。
4. **Dashboard** —— 聚合指标：QPS、p50/p99 延迟、错误率、成本、模型健康。
5. **Trace** —— 按关键字（trace_id / call_id / 模型 / prompt 名 / 错误信息）搜索，
   点开某条按 8 个维度查看整条链路（关联 / Prompt / 路由 / 用量 / 延迟 / 弹性 / 结果 / 错误 / 成本）。

## 测试

```bash
# 后端
.venv/bin/python -m pytest tests -q --cov=llm_gw

# 前端
cd webapp
npm install
npm test          # vitest
npm run build     # 产物到 webapp/dist，由后端静态托管
```

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
