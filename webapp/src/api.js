/**
 * 控制台 API 客户端。
 *
 * 统一走相对路径 ``/api``：开发期由 Vite 代理到 8000，生产期由 FastAPI
 * 同源托管，因此不需要区分环境的 base URL。
 */

const BASE = "/api";

/**
 * agent 接口前缀。
 *
 * Chat 页**不再**经控制台转发：需求"管理与交互层"第 7 条要求 Chat 模仿后端
 * agent Loop，直接按 agent 的 schema 与网关沟通，因此走 ``/v1`` 而不是 ``/api``。
 * 控制台自身的 ``/api/*`` 也不受 agent 口令保护，两者天然分开。
 */
const AGENT_BASE = "/v1";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await read_error(response));
  }
  return response.json();
}

/** 后端的错误体既可能是 FastAPI 的 ``{detail}``，也可能是纯文本，这里都兜住。 */
async function read_error(response) {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text);
    const detail = parsed.detail ?? parsed;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") return detail.message ?? JSON.stringify(detail);
  } catch {
    /* 非 JSON，直接用原文 */
  }
  return text || `HTTP ${response.status}`;
}

export const listProviders = () => request("/providers");
/**
 * 供应商可选模型与其能力（需求"模型管理层"第 1 条）。
 *
 * 返回 ``{source, error, models, advanced}``：``source`` 为 ``upstream`` 表示
 * 清单来自供应商实时查询，``preset`` 表示上游不可用、已退回内置清单，此时
 * ``error`` 会说明原因，界面需要如实提示而不是假装一切正常。
 */
export const listProviderModels = (provider) =>
  request(`/providers/${encodeURIComponent(provider)}/models`);
export const listModels = () => request("/models");
export const createModel = (payload) =>
  request("/models", { method: "POST", body: JSON.stringify(payload) });
export const updateModel = (provider, modelId, payload) =>
  request(`/models/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteModel = (provider, modelId) =>
  request(`/models/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });

/**
 * 模型连接测试（需求"模型管理层"第 2 条）。
 *
 * 请求体与新增/编辑模型一致，因此尚未保存的模型也能先测再存。上游失败属于
 * 测试结论而非控制台故障，后端因此返回 200 + ``ok=false``；这里只把请求体
 * 本身不合法（422）当作异常抛出。
 */
export const testModel = (payload) =>
  request("/models:test", { method: "POST", body: JSON.stringify(payload) });

/** 当前版本号与更新日志（需求"管理与交互层"第 6 条）。 */
export const getMeta = () => request("/meta");

// gwprofile（需求第 31 行）
export const listProfiles = () => request("/profiles");
export const createProfile = (payload) =>
  request("/profiles", { method: "POST", body: JSON.stringify(payload) });
export const updateProfile = (name, payload) =>
  request(`/profiles/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteProfile = (name) =>
  request(`/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });

export const getDashboard = () => request("/dashboard");

/** 网关设置：只回"口令从哪来"，**不回口令本身**（需求 Harness 层第 1 条）。 */
export const getSettings = () => request("/settings");

/**
 * 写入/清除 agent 接口口令（需求 Harness 层第 1 条）。
 *
 * ``password`` 传空串表示清除；接口永不回显口令，因此只把返回值当作新的来源状态。
 */
export const setAgentPassword = (password) =>
  request("/settings/agent-password", {
    method: "PUT",
    body: JSON.stringify({ password }),
  });

/**
 * 通讯原始日志查询（需求 Harness 层第 3 条）。
 *
 * ``q`` 由后端做原文 LIKE 检索；``taskId`` 按 task 过滤；``limit`` 默认 100。
 */
export const listExchanges = ({ q = "", taskId = "", limit = 100 } = {}) => {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (taskId) params.set("task_id", taskId);
  if (limit) params.set("limit", String(limit));
  return request(`/exchanges?${params.toString()}`);
};
export const getExchange = (exchangeId) =>
  request(`/exchanges/${encodeURIComponent(exchangeId)}`);

export const searchTraces = (keyword = "", limit = 50) =>
  request(`/traces?q=${encodeURIComponent(keyword)}&limit=${limit}`);
export const getTrace = (traceId) => request(`/traces/${encodeURIComponent(traceId)}`);

/**
 * 直连 agent 的 ``/v1/tasks:stream`` 消费 SSE 流（需求"管理与交互层"第 7 条）。
 *
 * ``payload`` 必须是 agent 的 Task schema（``task_id`` / ``input.messages`` …），
 * 由 Chat 页按后端契约组装。用 fetch + ReadableStream 而不是 EventSource：
 * EventSource 只支持 GET，而任务请求需要 POST。
 *
 * 终态约定与后端一致——收到 ``[DONE]`` 视为正常结束，收到 ``error`` /
 * ``cancelled`` 事件视为异常结束（后端不会为它们发 ``[DONE]``）。
 *
 * 若网关配了口令，必须带 ``Authorization: Bearer <password>``，否则 401
 * （``AUTH_REQUIRED``）；``traceId`` 会作为 ``x-trace-id`` 头传给网关，落库成
 * 本次通讯的 trace_id。
 */
export async function streamTask(payload, { password, traceId, onEvent, signal } = {}) {
  const headers = { "content-type": "application/json" };
  if (password) headers.Authorization = `Bearer ${password}`;
  if (traceId) headers["x-trace-id"] = traceId;

  const response = await fetch(`${AGENT_BASE}/tasks:stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    throw new Error(await read_error(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = false;

  while (!done) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });

    // SSE 以空行分隔事件；一个事件可能跨多个网络分片，因此按 buffer 累积。
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parse_frame(raw);
      if (event) {
        if (event.data === "[DONE]") {
          done = true;
        } else {
          onEvent?.(event);
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

/**
 * 解析单个 SSE 帧；只取控制台需要的 ``event`` 与 ``data`` 字段。
 *
 * 导出是为了让 Trace 页的"渲染后易读模式"复用同一套解析规则，避免两处各写
 * 一份分帧逻辑后行为漂移。
 */
export function parse_frame(raw) {
  let name = null;
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const index = line.indexOf(":");
    const field = index === -1 ? line : line.slice(0, index);
    let value = index === -1 ? "" : line.slice(index + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") name = value;
    else if (field === "data") dataLines.push(value);
  }
  if (!dataLines.length) return null;
  return { event: name, data: dataLines.join("\n") };
}
