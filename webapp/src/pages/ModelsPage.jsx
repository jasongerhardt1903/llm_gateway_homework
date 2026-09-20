import { useEffect, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { createModel, deleteModel, listModels, listProviders, updateModel } from "../api.js";
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
 * 模型定义页。
 *
 * 覆盖需求第 30 行对模型的两类配置：
 * 1. **模型身份**：名称、供应商（下拉菜单）、base URL、上下文窗口、能力、成本；
 * 2. **高级配置项**：temperature / top_p / top_k、思考模式、工具调用轮数、
 *    模型 tag，以及可在界面录入的 API key（只写不回显）。
 *
 * profile 的编组与路由配置在「Profile」页，这里只维护模型本身。
 */
export default function ModelsPage() {
  const [providers, setProviders] = useState([]);
  const [models, setModels] = useState([]);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const [prov, mods] = await Promise.all([listProviders(), listModels()]);
      setProviders(prov);
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

  const handleDelete = async (model) => {
    if (!window.confirm(`删除模型 ${model.provider}/${model.id}？`)) return;
    try {
      await deleteModel(model.provider, model.id);
      await load();
      flash("已删除模型");
    } catch (err) {
      setError(err.message);
    }
  };

  const handleSaveModel = async (payload) => {
    try {
      if (editing) await updateModel(editing.provider, editing.id, payload);
      else await createModel(payload);
      setEditing(null);
      await load();
      flash("已保存模型");
    } catch (err) {
      setError(err.message);
    }
  };

  const columns = [
    {
      id: "provider",
      header: "供应商",
      accessorFn: (model) => model.display_provider || model.provider,
      cell: ({ row }) => <span className="text-fg">{row.original.display_provider || row.original.provider}</span>,
    },
    { accessorKey: "name", header: "名称" },
    {
      accessorKey: "id",
      header: "实际模型",
      cell: ({ getValue }) => <span className="font-mono text-xs text-fg2">{getValue()}</span>,
    },
    {
      accessorKey: "tag",
      header: "Tag",
      cell: ({ getValue }) => getValue() || <span className="text-faint">—</span>,
    },
    { accessorKey: "api", header: "协议" },
    {
      id: "capabilities",
      header: "能力",
      enableSorting: false,
      accessorFn: (model) => capability_list(model.capabilities).length,
      cell: ({ row }) => <CapabilityBadges capabilities={row.original.capabilities} />,
    },
    {
      id: "api_key_set",
      header: "密钥",
      accessorFn: (model) => (model.api_key_set ? 1 : 0),
      cell: ({ row }) =>
        row.original.api_key_set ? (
          <Badge tone="ok">已配置</Badge>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => {
        const model = row.original;
        const isEditing = editing?.provider === model.provider && editing?.id === model.id;
        return (
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(isEditing ? null : model)}
            >
              <Pencil size={12} />
              {isEditing ? "取消" : "编辑"}
            </Button>
            <Button variant="danger" size="sm" onClick={() => handleDelete(model)}>
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
        title="模型定义"
        description="维护网关可路由的模型：连接信息、能力、成本与高级配置项。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}
      {message && (
        <Alert tone="ok" className="mb-3">
          {message}
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>模型清单</CardTitle>
          <CardDescription>
            {models.length ? `共 ${models.length} 个模型，点表头可排序` : "尚未定义模型"}
          </CardDescription>
        </CardHeader>
        <DataTable
          columns={columns}
          data={models}
          getRowKey={(model) => `${model.provider}/${model.id}`}
          empty="尚未定义模型，从下方供应商中选择创建一个。"
        />
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{editing ? "编辑模型" : "新增模型"}</CardTitle>
          <CardDescription>
            {editing ? `正在编辑 ${editing.provider}/${editing.id}` : "选择一个供应商作为起点"}
          </CardDescription>
        </CardHeader>
        <ModelForm
          key={editing ? `${editing.provider}/${editing.id}` : "new"}
          providers={providers}
          initial={editing ?? {}}
          submitLabel={editing ? "保存修改" : "新增模型"}
          onSubmit={handleSaveModel}
        />
      </Card>
    </div>
  );
}

const CAPABILITIES = ["sse", "streaming", "tools", "json_schema", "vision", "reasoning"];

/** 能力用一行小徽标列出，保持表格宽度可控。 */
function CapabilityBadges({ capabilities = {} }) {
  const flags = capability_list(capabilities);
  if (!flags.length) return <span className="text-faint">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {flags.map((key) => (
        <Badge key={key} tone="accent">
          {key.replace("_", " ")}
        </Badge>
      ))}
    </span>
  );
}

function capability_list(capabilities = {}) {
  return CAPABILITIES.filter((key) => capabilities[key]);
}

/** 高级配置项的输入控件描述：``[字段名, 标签, input 类型]``。 */
const ADVANCED_NUMBERS = [
  ["temperature", "temperature", "number"],
  ["top_p", "top_p", "number"],
  ["top_k", "top_k", "number"],
  ["max_tool_rounds", "工具调用轮数", "number"],
];

/** 空串表示"不发送该字段"，因此这里要把空串还原成 ``null`` 而不是 0。 */
function number_or_null(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

function ModelForm({ providers, initial = {}, submitLabel, onSubmit }) {
  const [form, setForm] = useState({
    id: initial.id ?? "",
    name: initial.name ?? "",
    provider: initial.provider ?? "",
    api: initial.api ?? "openai",
    base_url: initial.base_url ?? "",
    context_window: initial.context_window ?? 0,
    max_tokens: initial.max_tokens ?? 0,
    cost: { ...(initial.cost ?? {}) },
    capabilities: { ...(initial.capabilities ?? {}) },
    display_provider: initial.display_provider ?? "",
    tag: initial.tag ?? "",
    advanced: { ...(initial.advanced ?? {}) },
  });
  // 密钥只写不回显：留空表示不修改已有密钥，勾选"清除"才发空串。
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);

  // 选中供应商后自动用 preset 的连接信息填 api / base_url。
  const selectProvider = (value) => {
    const preset = providers.find((p) => p.provider === value);
    setForm({
      ...form,
      provider: value,
      api: preset?.api ?? form.api,
      base_url: preset?.base_url ?? form.base_url,
    });
  };

  const setCapability = (key, value) =>
    setForm({ ...form, capabilities: { ...form.capabilities, [key]: value } });
  const setCost = (key, value) =>
    setForm({ ...form, cost: { ...form.cost, [key]: Number(value) } });
  const setAdvanced = (key, value) =>
    setForm({ ...form, advanced: { ...form.advanced, [key]: value } });

  const submit = (event) => {
    event.preventDefault();
    const payload = {
      ...form,
      name: form.name || form.id,
      advanced: {
        ...form.advanced,
        temperature: number_or_null(form.advanced.temperature),
        top_p: number_or_null(form.advanced.top_p),
        top_k: number_or_null(form.advanced.top_k),
        max_tool_rounds: number_or_null(form.advanced.max_tool_rounds),
      },
    };
    // 未填写且未勾选清除时不带 api_key 字段：后端把"缺省"理解为不修改。
    if (clearKey) payload.api_key = "";
    else if (apiKey) payload.api_key = apiKey;
    onSubmit(payload);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <FormRow>
        <Field label="供应商" className="w-[190px]">
          <Select value={form.provider} onChange={(e) => selectProvider(e.target.value)} required>
            <option value="">选择供应商…</option>
            {providers.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.display_name}（{p.provider}）
              </option>
            ))}
          </Select>
        </Field>
        <Field label="模型名称" className="w-[190px]">
          <Input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="例如 GPT-4o Mini"
          />
        </Field>
        <Field label="实际模型 ID" className="w-[190px]">
          <Input
            value={form.id}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
            required
          />
        </Field>
        <Field label="Tag" className="w-[150px]">
          <Input
            value={form.tag}
            onChange={(e) => setForm({ ...form, tag: e.target.value })}
            placeholder="例如 便宜 / 高质"
          />
        </Field>
      </FormRow>

      <FormRow>
        <Field label="协议" className="w-[150px]">
          <Select value={form.api} onChange={(e) => setForm({ ...form, api: e.target.value })}>
            <option value="openai">openai</option>
            <option value="anthropic">anthropic</option>
          </Select>
        </Field>
        <Field label="Base URL" className="min-w-[240px] flex-1">
          <Input
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            required
          />
        </Field>
        <Field label="上下文窗口" className="w-[130px]">
          <Input
            type="number"
            value={form.context_window}
            onChange={(e) => setForm({ ...form, context_window: Number(e.target.value) })}
          />
        </Field>
        <Field label="最大输出" className="w-[130px]">
          <Input
            type="number"
            value={form.max_tokens}
            onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })}
          />
        </Field>
      </FormRow>

      <FormRow className="items-center">
        <Field label="API Key" className="min-w-[240px] flex-1">
          <Input
            type="password"
            value={apiKey}
            disabled={clearKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={initial.api_key_set ? "已配置（留空表示不修改）" : "可留空，改用环境变量"}
          />
        </Field>
        <CheckboxField checked={clearKey} onChange={(e) => setClearKey(e.target.checked)}>
          清除已配置的密钥
        </CheckboxField>
      </FormRow>

      <div>
        <FieldLabel>能力（决定哪些任务可路由到该模型）</FieldLabel>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {CAPABILITIES.map((key) => (
            <CheckboxField
              key={key}
              checked={!!form.capabilities[key]}
              onChange={(e) => setCapability(key, e.target.checked)}
            >
              {key}
            </CheckboxField>
          ))}
        </div>
      </div>

      <div>
        <FieldLabel>成本（每 1K token 单价）</FieldLabel>
        <FormRow>
          {["input", "output", "cache_read", "cache_write"].map((key) => (
            <Field key={key} label={key} className="w-[150px]">
              <Input
                type="number"
                step="0.0001"
                value={form.cost[key] ?? ""}
                onChange={(e) => setCost(key, e.target.value)}
              />
            </Field>
          ))}
        </FormRow>
      </div>

      <div>
        <FieldLabel>高级配置项（留空表示不发送该参数，用供应商默认）</FieldLabel>
        <FormRow>
          {ADVANCED_NUMBERS.map(([key, label, type]) => (
            <Field key={key} label={label} className="w-[150px]">
              <Input
                type={type}
                step={key === "top_k" || key === "max_tool_rounds" ? "1" : "0.05"}
                value={form.advanced[key] ?? ""}
                onChange={(e) => setAdvanced(key, e.target.value)}
              />
            </Field>
          ))}
          <Field label="思考模式" className="w-[170px]">
            <Select
              value={form.advanced.thinking_mode ?? "default"}
              onChange={(e) => setAdvanced("thinking_mode", e.target.value)}
            >
              <option value="default">跟随模型默认</option>
              <option value="on">开启</option>
              <option value="off">关闭</option>
            </Select>
          </Field>
        </FormRow>
      </div>

      <div>
        <Button type="submit">
          {initial.id ? null : <Plus size={14} />}
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
