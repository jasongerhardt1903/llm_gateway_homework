import { useEffect, useRef, useState } from "react";
import { Plus, Send, Square } from "lucide-react";
import { getSettings, listProfiles, streamTask } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import { CheckboxField, Field, FormRow, Input, Select, Textarea } from "../components/ui/field.jsx";

/** localStorage 里记住页面口令的键名；口令不写进 URL，避免进浏览器历史/代理日志。 */
const PASSWORD_KEY = "llm_gw_agent_password";

/** 口令来源的展示文案，与后端 ``agent_password_source`` 一一对应。 */
const SOURCE_LABEL = { env: "环境变量", console: "网页配置", none: "未配置" };
const SOURCE_TONE = { env: "accent", console: "ok", none: "neutral" };

/**
 * 前端这一侧的工具轮数上限。
 *
 * 网关的 ``max_tool_rounds`` 护栏来自 profile 的高级配置模版，没配 profile 时没人
 * 兜底；页面扮演的是 agent，止损也得自己带一个，否则模型可以无限要求调工具。
 */
export const MAX_TOOL_ROUNDS = 4;

/** 默认工具定义：两个本地假工具，让多轮 agent 开箱即可跑通。 */
export const DEFAULT_TOOLS = JSON.stringify(
  [
    {
      name: "get_time",
      description: "返回当前本地时间（ISO 8601）",
      parameters: { type: "object", properties: {}, required: [] },
    },
    {
      name: "echo",
      description: "原样回显 text 参数",
      parameters: {
        type: "object",
        properties: { text: { type: "string", description: "要回显的文本" } },
        required: ["text"],
      },
    },
  ],
  null,
  2
);

/**
 * 解析页面上的 tools JSON。
 *
 * 只做"是不是 ToolSpec 数组"这一层结构校验——真正的 schema 校验在网关（422），
 * 前端重复实现只会让两处规则漂移。
 */
export function parse_tools(text) {
  const trimmed = (text ?? "").trim();
  if (!trimmed) return { tools: [], error: "" };
  let parsed;
  try {
    parsed = JSON.parse(trimmed);
  } catch (err) {
    return { tools: [], error: `工具定义不是合法 JSON：${err.message}` };
  }
  if (!Array.isArray(parsed)) return { tools: [], error: "工具定义必须是数组（ToolSpec 列表）" };
  const bad = parsed.findIndex((item) => !item || typeof item.name !== "string" || !item.name);
  if (bad !== -1) return { tools: [], error: `第 ${bad + 1} 个工具缺少 name` };
  return { tools: parsed, error: "" };
}

/**
 * 执行一个本地假工具。
 *
 * Chat 页扮演的是"简单的后端 agent Loop"，工具的**实现**当然也在 agent 这一侧：
 * 网关不执行工具，只把 ``tool_call`` 吐出来。真实项目里这里换成自己的函数调用。
 */
export function run_local_tool(name, args = {}) {
  if (name === "get_time") return new Date().toISOString();
  if (name === "echo") return String(args.text ?? "");
  return `未知工具 ${name}：控制台只内置 get_time / echo，真实工具请在后端 agent 里实现`;
}

/** assistant 轮的内容块：本轮文本 + 本轮全部 tool_call（回灌历史用）。 */
export function assistant_content(text, calls) {
  const blocks = [];
  if (text) blocks.push({ type: "text", text });
  for (const call of calls) {
    blocks.push({
      type: "tool_call",
      id: call.id ?? "",
      name: call.name,
      arguments: call.arguments ?? {},
    });
  }
  return blocks;
}

/**
 * 工具结果消息：``role=tool`` + ``tool_result`` 块。
 *
 * ``tool_call_id`` 必须与发起调用对得上，否则会被适配层的成对性归一化当成孤儿结果丢掉。
 */
export function tool_message(call, result) {
  return {
    role: "tool",
    content: [{ type: "tool_result", tool_call_id: call.id ?? "", content: result }],
  };
}

/**
 * 从 ``done`` 帧里取本轮工具调用汇总。
 *
 * ``done.message`` 是**原始 AssistantMessage 形状**（网关 ``_event_payload`` 用
 * ``asdict`` 展开，字段名不转驼峰），因此工具调用在 ``content[]`` 里、块类型是
 * ``toolCall``；顶层并没有 ``tool_calls``。
 */
export function tool_calls_from_done(payload) {
  return (payload?.message?.content ?? [])
    .filter((block) => block?.type === "toolCall")
    .map((block) => ({
      id: block.id ?? "",
      name: block.name ?? "",
      arguments: block.arguments ?? {},
    }));
}

/**
 * Chat 页：模仿一个简单的后端 agent Loop，直连网关的 agent 接口。
 *
 * 需求"管理与交互层"第 7 条要求 Chat 按**后端 agent 需要遵守的 schema**与网关
 * 沟通，因此这里不再走控制台自己的 ChatRequest（该契约已删除），而是按 Task
 * schema 组装请求体，POST 到 ``/v1/tasks:stream``。
 *
 * **多轮由本页编排**：网关刻意不跑工具循环（它只以"拒绝超限请求"的方式表达
 * ``max_tool_rounds`` 护栏），所以"收到 tool_call → 本地执行 → 把 tool_result
 * 塞回历史 → 再调一次"这三步必须由 agent 侧完成，也就是这里。一次工具循环里的
 * 多次通讯共用同一个 ``task_id``，网关的通讯日志才会把它们归到同一组。
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
  const [toolsOn, setToolsOn] = useState(false);
  const [toolsText, setToolsText] = useState(DEFAULT_TOOLS);
  // 会话级 task_id：换会话才换 id，见上面 docstring 里"同一 task 归组"的说明。
  const [taskId, setTaskId] = useState(() => random_id("chat"));
  // 口令状态：从 localStorage 恢复（服务端静态渲染时没有 localStorage，兜底空串）。
  const [password, setPassword] = useState(() => read_stored_password());
  const [settings, setSettings] = useState(null);
  const abortRef = useRef(null);
  // 真正回灌给模型的 agent 历史（含 tool_call / tool_result 块），与用于展示的 turns 分开。
  const messagesRef = useRef([]);

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

  /** 新会话：换 task_id 并清空历史（同一会话内多轮工具循环才共用一个 id）。 */
  const new_session = () => {
    abortRef.current?.abort();
    messagesRef.current = [];
    setTurns([]);
    setTaskId(random_id("chat"));
    setError("");
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;

    const { tools, error: toolsError } = toolsOn ? parse_tools(toolsText) : { tools: [], error: "" };
    if (toolsError) {
      setError(toolsError);
      return;
    }

    messagesRef.current = [...messagesRef.current, { role: "user", content: text }];
    setTurns((prev) => [...prev, { role: "user", text }, empty_assistant()]);
    setInput("");
    setBusy(true);
    setError("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // 每轮一次通讯；模型不再要工具（或终态不是 done）就收工。轮数上限兜底见常量说明。
      for (let round = 0; ; round += 1) {
        if (round > 0) setTurns((prev) => [...prev, empty_assistant()]);

        // agent Task schema：task_id 必填且非空，input.messages 至少一条。
        const body = {
          task_id: taskId,
          profile: profile || null,
          input: {
            messages: messagesRef.current,
            system: system || null,
            stream: true,
            ...(tools.length ? { tools } : {}),
          },
          metadata: {},
        };
        if (maxTokens !== "") body.input.max_tokens = Number(maxTokens);
        if (temperature !== "") body.input.temperature = Number(temperature);

        const outcome = await stream_once(body, {
          password,
          traceId: random_id("trace"),
          signal: controller.signal,
          onProgress: patch_last,
        });
        patch_last({
          text: outcome.text,
          thinking: outcome.thinking,
          terminal: outcome.terminal,
          error: outcome.error,
        });

        if (outcome.terminal !== "done" || !outcome.calls.length) break;
        if (round >= MAX_TOOL_ROUNDS) {
          patch_last({ note: `已达前端工具轮数上限（${MAX_TOOL_ROUNDS} 轮），停止续跑` });
          break;
        }

        const results = outcome.calls.map((call) => ({
          call,
          result: run_local_tool(call.name, call.arguments),
        }));
        messagesRef.current = [
          ...messagesRef.current,
          { role: "assistant", content: assistant_content(outcome.text, outcome.calls) },
          ...results.map(({ call, result }) => tool_message(call, result)),
        ];
        patch_last({ results });
      }
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
        description="按 agent schema 直连网关 /v1/tasks:stream 的流式对话，可在本页编排工具调用循环，验证多轮 agent 与终态语义。"
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
          <Field
            label="task_id"
            className="min-w-[240px] flex-1"
            hint="同一会话复用，通讯日志按 task_id 两级分组"
          >
            <Input readOnly value={taskId} className="font-mono text-xs" />
          </Field>
          <Button variant="secondary" onClick={new_session} disabled={busy}>
            <Plus size={14} />
            新会话
          </Button>
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

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>工具与多轮循环</CardTitle>
          <CardDescription>
            网关不编排工具循环，只把 tool_call 吐出来：执行工具、把 tool_result 塞回历史、
            再调一次由本页完成，最多 {MAX_TOOL_ROUNDS} 轮。
          </CardDescription>
        </CardHeader>
        <CheckboxField
          checked={toolsOn}
          onChange={(e) => setToolsOn(e.target.checked)}
          title="勾选后请求体带上 input.tools"
        >
          带上 tools（模型可发起工具调用）
        </CheckboxField>
        {toolsOn && (
          <Field
            label="tools（ToolSpec 数组）"
            className="mt-3"
            hint="内置 get_time / echo 两个本地假工具，可直接改；结构不对会被网关 422 拒绝"
          >
            <Textarea
              rows="8"
              className="font-mono text-xs"
              value={toolsText}
              onChange={(e) => setToolsText(e.target.value)}
            />
          </Field>
        )}
      </Card>

      <div className="chat-log my-4">
        {turns.length === 0 && <p className="text-sm text-muted">输入一句话开始对话。</p>}
        {turns.map((turn, index) => (
          <div key={index} className={`bubble bubble-${turn.role}`}>
            <div className="mb-1 flex items-center gap-2">
              <span className="text-xs uppercase tracking-wider text-muted">{turn.role}</span>
              {turn.terminal && <TerminalBadge terminal={turn.terminal} />}
              {turn.results?.length > 0 && (
                <Badge tone="accent">{turn.results.length} 次工具调用</Badge>
              )}
            </div>
            {turn.thinking && <pre className="thinking">{turn.thinking}</pre>}
            <div className="text-sm text-fg">
              {turn.text || (busy && index === turns.length - 1 ? "…" : "")}
            </div>
            {turn.results?.map(({ call, result }, i) => (
              <div key={i} className="mt-2 text-xs text-faint">
                <span className="font-mono text-fg2">
                  {call.name}({JSON.stringify(call.arguments ?? {})})
                </span>
                <span className="ml-2">→ {result}</span>
              </div>
            ))}
            {turn.note && <div className="mt-2 text-sm text-[#ffd479]">{turn.note}</div>}
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

/** 助手气泡的初始状态（每轮一个，工具循环会追加多个）。 */
function empty_assistant() {
  return { role: "assistant", text: "", thinking: "", terminal: null, error: "", note: "", results: [] };
}

/**
 * 跑一轮：向 ``/v1/tasks:stream`` 发一次请求，回收本轮文本、思考、工具调用与终态。
 *
 * 工具调用的首选来源是 ``toolcall_end`` 事件（网关在参数流结束时补发的完整快照），
 * ``done`` 事件里的汇总作兜底。注意 ``done.message`` 是**原始 AssistantMessage 形状**
 * （``_event_payload`` 用 ``asdict`` 展开，字段名不转驼峰），工具调用位于
 * ``content[]`` 里、块类型是 ``toolCall``，而不是顶层 ``tool_calls``。
 */
async function stream_once(body, { password, traceId, signal, onProgress }) {
  let text = "";
  let thinking = "";
  const ends = [];
  let calls = [];
  let terminal = null;
  let error = "";

  await streamTask(body, {
    password,
    traceId,
    signal,
    onEvent: (event) => {
      const payload = safe_parse(event.data);
      if (!payload) return;
      if (payload.type === "text_delta") {
        text += payload.delta ?? "";
        onProgress({ text, thinking });
      } else if (payload.type === "thinking_delta") {
        thinking += payload.delta ?? "";
        onProgress({ text, thinking });
      } else if (payload.type === "toolcall_end") {
        ends.push({
          id: payload.id ?? "",
          name: payload.name ?? "",
          arguments: payload.arguments ?? {},
        });
      } else if (payload.type === "done") {
        terminal = "done";
        calls = tool_calls_from_done(payload);
      } else if (payload.type === "error") {
        terminal = "error";
        error = payload.error_message ?? payload.error?.error_message ?? "上游错误";
      } else if (payload.type === "cancelled") {
        terminal = "cancelled";
        error = "已取消";
      }
    },
  });

  return { text, thinking, terminal, error, calls: calls.length ? calls : ends };
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