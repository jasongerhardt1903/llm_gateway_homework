import { useEffect, useRef, useState } from "react";
import { Send, Square } from "lucide-react";
import { getSettings, listProfiles, streamTask } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import { Field, FormRow, Input, Select, Textarea } from "../components/ui/field.jsx";

/** localStorage 里记住页面口令的键名；口令不写进 URL，避免进浏览器历史/代理日志。 */
const PASSWORD_KEY = "llm_gw_agent_password";

/** 口令来源的展示文案，与后端 ``agent_password_source`` 一一对应。 */
const SOURCE_LABEL = { env: "环境变量", console: "网页配置", none: "未配置" };
const SOURCE_TONE = { env: "accent", console: "ok", none: "neutral" };

/**
 * Chat 页：模仿一个简单的后端 agent Loop，直连网关的 agent 接口。
 *
 * 需求"管理与交互层"第 7 条要求 Chat 按**后端 agent 需要遵守的 schema**与网关
 * 沟通，因此这里不再走控制台自己的 ChatRequest（该契约已删除），而是按 Task
 * schema 组装请求体，POST 到 ``/v1/tasks:stream``：自己生成 ``task_id`` 与
 * ``x-trace-id``，把 profile 放进 ``profile``，system / max_tokens / temperature
 * 收进 ``input``。
 *
 * 因为直连 agent 接口，若网关配了口令就必须带 Bearer；页面因此提供一个口令
 * 输入框（用 localStorage 记住，不进 URL），并按 ``/api/settings`` 如实提示
 * 口令来自 env / console / none。
 *
 * 渲染规则直接对应后端的终态约定：
 * - ``text_delta`` 增量追加到气泡；
 * - ``thinking_delta`` 单独折叠展示（推理模型的思考不混进正文）；
 * - ``error`` / ``cancelled`` 终态在气泡下方标红，且**不会**收到 ``[DONE]``，
 *   所以正常结束与异常结束在 UI 上是可区分的。
 */
export default function ChatPage() {
  const [profiles, setProfiles] = useState([]);
  const [profile, setProfile] = useState("");
  const [system, setSystem] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [temperature, setTemperature] = useState("");
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // 口令状态：从 localStorage 恢复（服务端静态渲染时没有 localStorage，兜底空串）。
  const [password, setPassword] = useState(() => read_stored_password());
  const [settings, setSettings] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    listProfiles()
      .then((profs) => {
        setProfiles(profs);
        setProfile((current) => current || profs[0]?.name || "");
      })
      .catch((err) => setError(err.message));
    getSettings()
      .then(setSettings)
      .catch(() => setSettings(null));
  }, []);

  // 口令只写不回显：本地记住是为了省去每次刷新重填，网关不会把口令读回来。
  useEffect(() => {
    if (typeof localStorage === "undefined") return;
    if (password) localStorage.setItem(PASSWORD_KEY, password);
    else localStorage.removeItem(PASSWORD_KEY);
  }, [password]);

  /** 就地更新最后一条（助手）气泡，避免每次 delta 都重建整个列表。 */
  const patch_last = (patch) =>
    setTurns((prev) => {
      if (!prev.length) return prev;
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;

    const history = turns.map((turn) => ({ role: turn.role, content: turn.text }));
    setTurns((prev) => [
      ...prev,
      { role: "user", text },
      { role: "assistant", text: "", thinking: "", terminal: null, error: "" },
    ]);
    setInput("");
    setBusy(true);
    setError("");

    const controller = new AbortController();
    abortRef.current = controller;
    let answer = "";
    let thinking = "";

    // agent Task schema：task_id 必填且非空，input.messages 至少一条。
    const body = {
      task_id: random_id("chat"),
      profile: profile || null,
      input: {
        messages: [...history, { role: "user", content: text }],
        system: system || null,
        stream: true,
      },
      metadata: {},
    };
    if (maxTokens !== "") body.input.max_tokens = Number(maxTokens);
    if (temperature !== "") body.input.temperature = Number(temperature);

    try {
      await streamTask(body, {
        password,
        traceId: random_id("trace"),
        signal: controller.signal,
        onEvent: (event) => {
          const payload = safe_parse(event.data);
          if (!payload) return;
          if (payload.type === "text_delta") {
            answer += payload.delta ?? "";
            patch_last({ text: answer });
          } else if (payload.type === "thinking_delta") {
            thinking += payload.delta ?? "";
            patch_last({ thinking });
          } else if (payload.type === "done") {
            patch_last({ terminal: "done" });
          } else if (payload.type === "error") {
            patch_last({
              terminal: "error",
              error: payload.error_message ?? payload.error?.error_message ?? "上游错误",
            });
          } else if (payload.type === "cancelled") {
            patch_last({ terminal: "cancelled", error: "已取消" });
          }
        },
      });
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message);
      patch_last({
        terminal: "cancelled",
        error: err.name === "AbortError" ? "已取消" : err.message,
      });
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  const source = settings?.agent_password_source ?? null;

  return (
    <div>
      <PageHeader
        title="Chat"
        description="按 agent schema 直连网关 /v1/tasks:stream 的流式对话，用于验证 SSE 链路与终态语义。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>连接配置</CardTitle>
          <CardDescription>
            口令来源：<Badge tone={SOURCE_TONE[source] ?? "neutral"}>{SOURCE_LABEL[source] ?? "—"}</Badge>
            {settings?.env_key && (
              <span className="ml-2 text-faint">环境变量 {settings.env_key}</span>
            )}
          </CardDescription>
        </CardHeader>
        <FormRow className="items-end">
          <Field
            label="Agent 口令"
            className="min-w-[240px] flex-1"
            hint={
              source === "env"
                ? "口令由环境变量提供，此处填写不生效。"
                : "网关配了口令时必填，浏览器本地记住，不写入 URL。"
            }
          >
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="未配置口令时留空即可"
            />
          </Field>
        </FormRow>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>请求参数</CardTitle>
          <CardDescription>profile 决定本次请求在哪些模型里路由</CardDescription>
        </CardHeader>
        <FormRow>
          <Field label="Profile" className="w-[220px]">
            <Select value={profile} onChange={(e) => setProfile(e.target.value)}>
              <option value="">全局模型池</option>
              {profiles.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.display_name || item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="System Prompt" className="min-w-[240px] flex-1">
            <Input
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              placeholder="可选"
            />
          </Field>
          <Field label="max_tokens" className="w-[150px]">
            <Input
              type="number"
              min="1"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              placeholder="可选"
            />
          </Field>
          <Field label="temperature" className="w-[150px]">
            <Input
              type="number"
              step="0.1"
              min="0"
              value={temperature}
              onChange={(e) => setTemperature(e.target.value)}
              placeholder="可选"
            />
          </Field>
        </FormRow>
      </Card>

      <div className="chat-log my-4">
        {turns.length === 0 && <p className="text-sm text-muted">输入一句话开始对话。</p>}
        {turns.map((turn, index) => (
          <div key={index} className={`bubble bubble-${turn.role}`}>
            <div className="mb-1 flex items-center gap-2">
              <span className="text-xs uppercase tracking-wider text-muted">{turn.role}</span>
              {turn.terminal && <TerminalBadge terminal={turn.terminal} />}
            </div>
            {turn.thinking && <pre className="thinking">{turn.thinking}</pre>}
            <div className="text-sm text-fg">
              {turn.text || (busy && index === turns.length - 1 ? "…" : "")}
            </div>
            {turn.error && <div className="mt-2 text-sm text-[#ffb4ae]">{turn.error}</div>}
          </div>
        ))}
      </div>

      <Textarea
        rows="3"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
        }}
        placeholder="输入消息，⌘/Ctrl + Enter 发送"
      />
      <div className="mt-3 flex items-center gap-2">
        <Button onClick={send} disabled={busy}>
          <Send size={14} />
          发送
        </Button>
        <Button variant="danger" onClick={stop} disabled={!busy}>
          <Square size={13} />
          中断
        </Button>
      </div>
    </div>
  );
}

/** 终态徽标：done / error / cancelled 三态配色与 Trace 页保持一致。 */
function TerminalBadge({ terminal }) {
  const tone = terminal === "done" ? "ok" : terminal === "error" ? "bad" : "warn";
  return <Badge tone={tone}>{terminal}</Badge>;
}

/** 读取本地记住的口令；非浏览器环境（静态渲染）返回空串。 */
function read_stored_password() {
  if (typeof localStorage === "undefined") return "";
  return localStorage.getItem(PASSWORD_KEY) ?? "";
}

/** 生成 ``chat-xxx`` / ``trace-xxx`` 这类短随机 ID（task_id 必填且非空）。 */
function random_id(prefix) {
  const uuid = globalThis.crypto?.randomUUID?.();
  const raw = uuid ? uuid.replace(/-/g, "").slice(0, 12) : Math.random().toString(36).slice(2, 14);
  return `${prefix}-${raw}`;
}

function safe_parse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}