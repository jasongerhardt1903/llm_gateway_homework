import { useEffect, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { createProfile, deleteProfile, listModels, listProfiles, updateProfile } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card.jsx";
import { DataTable } from "../components/ui/data-table.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import {
  CheckboxField,
  Field,
  FieldLabel,
  FormRow,
  Input,
  Select,
} from "../components/ui/field.jsx";

/**
 * gwprofile 管理页（需求第 31 行）。
 *
 * 一个 profile 定义三件事：
 * 1. **模型编组**：包含哪些模型（勾选加入），并对每个模型选择"本模型配置优先于模版"；
 * 2. **高级配置模版**：在 profile 内生效的统一配置，可整份启用/停用；
 * 3. **路由配置**：动态 / 静态二选一，静态用逗号分隔的优先顺序。
 *
 * 重试次数也挂在 profile 上（需求第 128 行），因此一并在这里维护。
 */
export default function ProfilesPage() {
  const [profiles, setProfiles] = useState([]);
  const [models, setModels] = useState([]);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const [profs, mods] = await Promise.all([listProfiles(), listModels()]);
      setProfiles(profs);
      setModels(mods);
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

  const handleDelete = async (profile) => {
    if (!window.confirm(`删除 profile ${profile.name}？`)) return;
    try {
      await deleteProfile(profile.name);
      await load();
      flash("已删除 profile");
    } catch (err) {
      setError(err.message);
    }
  };

  const handleSave = async (payload) => {
    try {
      if (editing) await updateProfile(editing.name, payload);
      else await createProfile(payload);
      setEditing(null);
      await load();
      flash("已保存 profile");
    } catch (err) {
      setError(err.message);
    }
  };

  const columns = [
    {
      id: "name",
      header: "名称",
      accessorFn: (profile) => profile.display_name || profile.name,
      cell: ({ row }) => (
        <span className="text-fg">{row.original.display_name || row.original.name}</span>
      ),
    },
    {
      id: "models",
      header: "模型",
      enableSorting: false,
      accessorFn: (profile) => (profile.models ?? []).length,
      cell: ({ row }) => {
        const list = row.original.models ?? [];
        if (!list.length) return <span className="text-faint">—</span>;
        return (
          <span className="flex flex-wrap gap-1">
            {list.map((model) => (
              <Badge key={model.label} tone={model.prefer_own_config ? "accent" : "neutral"}>
                {model.label}
                {model.prefer_own_config ? " · 优先" : ""}
              </Badge>
            ))}
          </span>
        );
      },
    },
    {
      id: "route",
      header: "路由",
      accessorFn: (profile) => profile.route_mode,
      cell: ({ row }) => {
        const profile = row.original;
        const isStatic = profile.route_mode === "static";
        return (
          <span className="flex flex-wrap items-center gap-1.5">
            <Badge tone={isStatic ? "warn" : "ok"}>{isStatic ? "静态" : "动态"}</Badge>
            {isStatic && (profile.static_order ?? []).length > 0 && (
              <span className="font-mono text-xs text-muted">
                {profile.static_order.join(" > ")}
              </span>
            )}
          </span>
        );
      },
    },
    {
      id: "template",
      header: "模版",
      accessorFn: (profile) => (profile.template_enabled ? 1 : 0),
      cell: ({ row }) =>
        row.original.template_enabled ? (
          <Badge tone="ok">已启用</Badge>
        ) : (
          <span className="text-faint">未启用</span>
        ),
    },
    {
      id: "retry",
      header: "最大重试",
      accessorFn: (profile) => (profile.retry_enabled ? Number(profile.max_retries ?? 0) : -1),
      cell: ({ row }) =>
        row.original.retry_enabled ? (
          <span className="tabular">{row.original.max_retries}</span>
        ) : (
          <span className="text-faint">关闭</span>
        ),
    },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => {
        const profile = row.original;
        const isEditing = editing?.name === profile.name;
        return (
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing(isEditing ? null : profile)}>
              <Pencil size={12} />
              {isEditing ? "取消" : "编辑"}
            </Button>
            <Button variant="danger" size="sm" onClick={() => handleDelete(profile)}>
              <Trash2 size={12} />
              删除
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader
        title="Profile（gwprofile）"
        description="把模型编成一组，并定义该组内的模版配置、路由方式与重试策略。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}
      {message && (
        <Alert tone="ok" className="mb-3">
          {message}
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Profile 清单</CardTitle>
          <CardDescription>
            {profiles.length ? `共 ${profiles.length} 个 profile` : "尚未配置 profile"}
          </CardDescription>
        </CardHeader>
        <DataTable
          columns={columns}
          data={profiles}
          getRowKey={(profile) => profile.name}
          empty="尚未配置 profile。未指定 profile 的请求将走全局模型池。"
        />
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{editing ? "编辑 Profile" : "新增 Profile"}</CardTitle>
          <CardDescription>
            {editing ? `正在编辑 ${editing.name}` : "先勾选模型，再决定模版与路由"}
          </CardDescription>
        </CardHeader>
        <ProfileForm
          key={editing ? editing.name : "new"}
          models={models}
          initial={editing ?? {}}
          submitLabel={editing ? "保存修改" : "新增 Profile"}
          onSubmit={handleSave}
        />
      </Card>
    </div>
  );
}

/** 数字型高级配置项：``[字段名, 标签]``。 */
const TEMPLATE_NUMBERS = [
  ["temperature", "temperature"],
  ["top_p", "top_p"],
  ["top_k", "top_k"],
  ["max_tool_rounds", "工具调用轮数"],
];

function ProfileForm({ models, initial = {}, submitLabel, onSubmit }) {
  // 先把编辑态的一种表达（checked / unchecked 都没关系）转成并列表单状态。
  const [form, setForm] = useState(() => normalize(initial));
  // 静态顺序用独立文本状态：边输入边解析会把用户正在敲的逗号吃掉。
  const [staticText, setStaticText] = useState(() => (initial.static_order ?? []).join(", "));

  function normalize(p) {
    const selected = (p.models ?? []).map((m) => m.label);
    const prefer = new Set(
      (p.models ?? []).filter((m) => m.prefer_own_config).map((m) => m.label)
    );
    return {
      name: p.name ?? "",
      display_name: p.display_name ?? "",
      model_labels: selected,
      prefer,
      template_enabled: p.template_enabled ?? false,
      template: { ...(p.template ?? {}) },
      route_mode: p.route_mode ?? "dynamic",
      retry_enabled: p.retry_enabled ?? true,
      max_retries: p.max_retries ?? 3,
    };
  }

  const toggleModel = (label) => {
    const selected = new Set(form.model_labels);
    if (selected.has(label)) selected.delete(label);
    else selected.add(label);
    setForm({ ...form, model_labels: [...selected] });
  };

  const togglePrefer = (label) => {
    const prefer = new Set(form.prefer);
    if (prefer.has(label)) prefer.delete(label);
    else prefer.add(label);
    setForm({ ...form, prefer });
  };

  const setTemplate = (key, value) =>
    setForm({ ...form, template: { ...form.template, [key]: value } });

  const submit = (event) => {
    event.preventDefault();
    const payload = {
      name: form.name || form.display_name,
      display_name: form.display_name,
      models: form.model_labels.map((label) => ({
        label,
        prefer_own_config: form.prefer.has(label),
      })),
      template_enabled: form.template_enabled,
      template: {
        ...form.template,
        temperature: number_or_null(form.template.temperature),
        top_p: number_or_null(form.template.top_p),
        top_k: number_or_null(form.template.top_k),
        max_tool_rounds: number_or_null(form.template.max_tool_rounds),
      },
      route_mode: form.route_mode,
      retry_enabled: form.retry_enabled,
      max_retries: form.max_retries,
      static_order:
        form.route_mode === "static" ? static_text_to_list(staticText) : [],
    };
    onSubmit(payload);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <FormRow>
        <Field label="Profile 名" className="w-[200px]">
          <Input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            disabled={!!initial.name}
          />
        </Field>
        <Field label="展示名称" className="min-w-[200px] flex-1">
          <Input
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
          />
        </Field>
      </FormRow>

      <div>
        <FieldLabel>包含的模型（勾选加入；右侧方框勾选表示该模型高级配置优先于模版）</FieldLabel>
        {models.length === 0 ? (
          <p className="text-sm text-muted">先在「模型定义」页创建模型。</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {models.map((model) => {
              const label = `${model.provider}/${model.id}`;
              const included = form.model_labels.includes(label);
              return (
                <div
                  key={label}
                  className="flex items-center gap-2 rounded-md border border-border-soft bg-raised px-2.5 py-1.5"
                >
                  <CheckboxField checked={included} onChange={() => toggleModel(label)}>
                    <span className="text-fg2">{model.name || model.id}</span>
                  </CheckboxField>
                  <span className="font-mono text-xs text-faint">{label}</span>
                  {included && (
                    <CheckboxField
                      className="ml-auto"
                      checked={form.prefer.has(label)}
                      onChange={() => togglePrefer(label)}
                      title="本模型配置优先于模版"
                    >
                      <span className="text-xs text-muted">优先</span>
                    </CheckboxField>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <FieldLabel>高级配置模版（在 profile 内生效，勾选后按需启用）</FieldLabel>
        <CheckboxField
          checked={form.template_enabled}
          onChange={(e) => setForm({ ...form, template_enabled: e.target.checked })}
        >
          启用模版
        </CheckboxField>
        <FormRow className="mt-3">
          {TEMPLATE_NUMBERS.map(([key, label]) => (
            <Field key={key} label={label} className="w-[150px]">
              <Input
                type="number"
                step={key === "top_k" || key === "max_tool_rounds" ? "1" : "0.05"}
                value={form.template[key] ?? ""}
                onChange={(e) => setTemplate(key, e.target.value)}
              />
            </Field>
          ))}
          <Field label="思考模式" className="w-[170px]">
            <Select
              value={form.template.thinking_mode ?? "default"}
              onChange={(e) => setTemplate("thinking_mode", e.target.value)}
            >
              <option value="default">跟随模型默认</option>
              <option value="on">开启</option>
              <option value="off">关闭</option>
            </Select>
          </Field>
        </FormRow>
      </div>

      <div>
        <FieldLabel>路由配置（需求第 31 行：动态 / 静态二选一）</FieldLabel>
        <FormRow>
          <Field label="路由模式" className="w-[170px]">
            <Select
              value={form.route_mode}
              onChange={(e) => setForm({ ...form, route_mode: e.target.value })}
            >
              <option value="dynamic">动态路由</option>
              <option value="static">静态路由</option>
            </Select>
          </Field>
          {form.route_mode === "static" && (
            <Field label="优先顺序（逗号分隔，主 → 备）" className="min-w-[280px] flex-1">
              <Input
                value={staticText}
                onChange={(e) => setStaticText(e.target.value)}
                placeholder="openai/gpt-4o-mini, deepseek/deepseek-chat"
              />
            </Field>
          )}
        </FormRow>
      </div>

      <div>
        <FieldLabel>重试策略（需求第 128 行）</FieldLabel>
        <FormRow className="items-center">
          <Field label="最大重试次数" className="w-[150px]">
            <Input
              type="number"
              min="0"
              max="10"
              value={form.max_retries}
              onChange={(e) => setForm({ ...form, max_retries: Number(e.target.value) })}
            />
          </Field>
          <CheckboxField
            checked={form.retry_enabled}
            onChange={(e) => setForm({ ...form, retry_enabled: e.target.checked })}
          >
            启用重试
          </CheckboxField>
        </FormRow>
      </div>

      <div>
        <Button type="submit">
          {initial.name ? null : <Plus size={14} />}
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

function number_or_null(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

/** "a, b，c" → ["a","b","c"]：全角/半角逗号与顿号都算分隔符。 */
function static_text_to_list(text) {
  return String(text ?? "")
    .replace(/[，、]/g, ",")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}
