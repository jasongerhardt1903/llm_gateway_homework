/**
 * 控制台 API 客户端。
 *
 * 统一走相对路径 ``/api``：开发期由 Vite 代理到 8000，生产期由 FastAPI
 * 同源托管，因此不需要区分环境的 base URL。
 */

const BASE = "/api";

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

export const searchTraces = (keyword = "", limit = 50) =>
  request(`/traces?q=${encodeURIComponent(keyword)}&limit=${limit}`);
export const getTrace = (traceId) => request(`/traces/${encodeURIComponent(traceId)}`);

/**
 * 消费 Chat 的 SSE 流。
 *
 * 用 fetch + ReadableStream 而不是 EventSource：EventSource 只支持 GET，
 * 而对话内容需要 POST。终态约定与后端一致——收到 ``[DONE]`` 视为正常结束，
 * 收到 ``error`` / ``cancelled`` 事件视为异常结束（后端不会为它们发 [DONE]）。
 */
export async function streamChat(payload, { onEvent, signal } = {}) {
  const response = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "content-type": "application/json" },
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

/** 解析单个 SSE 帧；只取控制台需要的 ``event`` 与 ``data`` 字段。 */
function parse_frame(raw) {
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
