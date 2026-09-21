import { useEffect, useState } from "react";
import { Save, Trash2 } from "lucide-react";
import { getSettings, setAgentPassword } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import { Field, FormRow, Input } from "../components/ui/field.jsx";

/** 口令来源文案与配色，与后端 ``agent_password_source`` 一一对应。 */
const SOURCE_LABEL = { env: "环境变量", console: "网页配置", none: "未配置" };
const SOURCE_TONE = { env: "accent", console: "ok", none: "neutral" };

/**
 * 设置页：agent 接口口令的网页配置（需求 Harness 层第 1 条）。
 *
 * 安全约定——口令**只写不回显**：页面从不尝试把口令读回来，保存/清除后只用
 * 接口返回的"来源状态"刷新界面。接口也只回 ``env`` / ``console`` / ``none``。
 *
 * 环境变量优先于网页配置：``env`` 情况下写入网页口令不会生效，界面如实提示。
 */
export default function SettingsPage() {
  const [settings, setSettings] = useState(null);
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      setSettings(await getSettings());
      setError("");
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (text) => {
    setMessage(text);
    setTimeout(() => setMessage(""), 2500);
  };

  const submit = async (value, successText) => {
    try {
      setSettings(await setAgentPassword(value));
      setPassword("");
      setError("");
      flash(successText);
    } catch (err) {
      setError(err.message);
    }
  };

  const source = settings?.agent_password_source ?? null;

  return (
    <div>
      <PageHeader
        title="设置"
        description="配置 agent 接口口令，并查看当前口令来源。控制台自身的 /api/* 不受口令保护。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}
      {message && (
        <Alert tone="ok" className="mb-3">
          {message}
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Agent 接口口令</CardTitle>
          <CardDescription>
            当前来源：
            <Badge tone={SOURCE_TONE[source] ?? "neutral"}>{SOURCE_LABEL[source] ?? "—"}</Badge>
            {settings?.env_key && (
              <span className="ml-2 font-mono text-faint">{settings.env_key}</span>
            )}
          </CardDescription>
        </CardHeader>

        <p className="mb-3 text-sm text-muted">
          环境变量优先于网页配置：只要设置了环境变量，这里保存的口令不会生效。
          未配置口令时 agent 接口放行；配置后必须带
          <span className="font-mono text-fg2"> Authorization: Bearer &lt;password&gt;</span>。
          口令只写不回显，保存后仅刷新来源状态。
        </p>

        <FormRow className="items-end">
          <Field label="口令" className="min-w-[260px] flex-1" hint="留空保存等同于清除口令。">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入新口令"
            />
          </Field>
          <Button onClick={() => submit(password, "已保存 agent 口令")} disabled={!password}>
            <Save size={14} />
            保存
          </Button>
          <Button variant="danger" onClick={() => submit("", "已清除 agent 口令")}>
            <Trash2 size={14} />
            清除
          </Button>
        </FormRow>
      </Card>
    </div>
  );
}