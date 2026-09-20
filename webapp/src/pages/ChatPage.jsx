import { useEffect, useRef, useState } from "react";
import { Send, Square } from "lucide-react";
import { listProfiles, streamChat } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import { Field, FormRow, Input, Select, Textarea } from "../components/ui/field.jsx";

/**
 * Chat 页：消费网关的 SSE 流。
 *
 * 顶部选的是 **profile**（需求第 31 行）——它决定这次请求在哪些模型里路由，
 * 而不是直接指定某个模型。留空表示走全局模型池。
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
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef(null);

  useEffect(() => {
    listProfiles()
      .then((profs) => {
        setProfiles(profs);
        setProfile((current) => current || profs[0]?.name || "");
      })
      .catch((err) => setError(err.message));
  }, []);

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

    try {
      await streamChat(
        {
          profile: profile || null,
          messages: [...history, { role: "user", content: text }],
          system: system || null,
          stream: true,
        },
        {
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
        }
      );
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

  return (
    <div>
      <PageHeader
        title="Chat"
        description="按 profile 路由的一次真实流式对话，用于验证 SSE 链路与终态语义。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}

      <Card>
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

function safe_parse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
