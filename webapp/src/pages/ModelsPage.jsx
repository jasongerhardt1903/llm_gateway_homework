import { useEffect, useState } from "react";
import { Pencil, Plus, Trash2, Zap } from "lucide-react";
import {
  createModel,
  deleteModel,
  listModels,
  listProviderModels,
  listProviders,
  testModel,
  updateModel,
} from "../api.js";
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
import {
  DEFAULT_ADVANCED_ITEMS,
  build_advanced,
  initial_enabled,
  item_checked,
  toggle_choice,
  toggle_item,
} from "../lib/model-catalog.js";

/**
 * 模型定义页。
 *
 * 覆盖需求"管理与交互层"第 2、6 条与"模型管理层"第 1、2 条：
 * 1. **模型身份**：供应商（下拉菜单）、模型名称（向供应商实时查询后下拉选择）、
 *    显示名称、API key（只写不回显）、base URL、上下文窗口、成本；
 * 2. **能力**：选中模型后按供应商返回的能力自动带出勾选项；
 * 3. **高级配置项**：按供应商情况展示，每项一个勾选框，互斥项自动互斥；
 * 4. **连接测试**：保存前后都可以发一次最小对话验证连通性。
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

/** 空串表示"不发送该字段"，因此这里要把空串还原成 ``null`` 而不是 0。 */
function number_or_null(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

function ModelForm({ providers, initial = {}, submitLabel, onSubmit }) {
  const [form, setForm] = useState({
    id: initial.id ?? "",
    name: initial.name ?? "",
    provider: initial.provider ?? "",
    api: initial.api ?? "openai-completions",
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
  // 供应商可选模型与能力清单（需求第 1a/1b/1c 条）。null 表示尚未拉到或拉取失败。
  const [catalog, setCatalog] = useState(null);
  const [catalogError, setCatalogError] = useState("");
  // 高级配置项的勾选状态：未勾选的项在提交时写成 null（= 不发送该参数）。
  const [advancedEnabled, setAdvancedEnabled] = useState(() => initial_enabled(initial.advanced));
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const advancedItems = catalog?.advanced?.items?.length
    ? catalog.advanced.items
    : DEFAULT_ADVANCED_ITEMS;

  // 选中供应商后向它查询可选模型与能力（需求：向模型 / 供应商查询）。
  useEffect(() => {
    if (!form.provider) {
      setCatalog(null);
      setCatalogError("");
      return;
    }
    let cancelled = false;
    listProviderModels(form.provider)
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setCatalogError(data.error || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setCatalog(null);
        setCatalogError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [form.provider]);

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

  /** 选中清单里的模型：把它的能力与价格带出来，用户不必逐个手填（需求 1a）。 */
  const selectModelId = (value) => {
    const spec = catalog?.models?.find((m) => m.id === value);
    if (!spec) {
      setForm({ ...form, id: value });
      return;
    }
    setForm({
      ...form,
      id: value,
      name: spec.name || value,
      display_provider: spec.display_provider || form.display_provider,
      api: spec.api || form.api,
      base_url: spec.base_url || form.base_url,
      context_window: spec.context_window ?? form.context_window,
      max_tokens: spec.max_tokens ?? form.max_tokens,
      cost: { ...form.cost, ...spec.cost },
      capabilities: { ...form.capabilities, ...spec.capabilities },
      // 高级配置项保留用户已填的值，只换模型不该丢掉手填的参数。
    });
  };

  const setCapability = (key, value) =>
    setForm({ ...form, capabilities: { ...form.capabilities, [key]: value } });
  const setCost = (key, value) =>
    setForm({ ...form, cost: { ...form.cost, [key]: Number(value) } });
  const setAdvanced = (key, value) =>
    setForm({ ...form, advanced: { ...form.advanced, [key]: value } });

  const toggleAdvancedItem = (item, on) => {
    const next = toggle_item(form.advanced, advancedEnabled, item.key, on);
    setForm({ ...form, advanced: next.advanced });
    setAdvancedEnabled(next.enabled);
  };

  /** 互斥项：选中一个即写入该值（同字段只能有一个值），再点一次回到默认。 */
  const toggleAdvancedChoice = (key, value) =>
    setForm({ ...form, advanced: toggle_choice(form.advanced, key, value) });

  /** 组装提交体：未勾选的高级项写 null，空串数字也还原成 null。 */
  const build_payload = () => {
    const advanced = build_advanced(advancedItems, form.advanced, advancedEnabled);
    const payload = {
      ...form,
      name: form.name || form.id,
      advanced: {
        ...advanced,
        temperature: number_or_null(advanced.temperature),
        top_p: number_or_null(advanced.top_p),
        top_k: number_or_null(advanced.top_k),
        max_tool_rounds: number_or_null(advanced.max_tool_rounds),
      },
    };
    // 未填写且未勾选清除时不带 api_key 字段：后端把"缺省"理解为不修改。
    if (clearKey) payload.api_key = "";
    else if (apiKey) payload.api_key = apiKey;
    return payload;
  };

  const submit = (event) => {
    event.preventDefault();
    onSubmit(build_payload());
  };

  /** 连接测试发给后端的是同一份提交体，因此"测通了"就等于"存下来能用"。 */
  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await testModel(build_payload()));
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
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
        <Field label="模型名称" className="w-[220px]" hint="调用模型时传给供应商的字段值">
          <Input
            list="provider-model-options"
            value={form.id}
            onChange={(e) => selectModelId(e.target.value)}
            placeholder={catalog?.models?.length ? "从下拉选择…" : "手动填写"}
            required
          />
          <datalist id="provider-model-options">
            {(catalog?.models ?? []).map((spec) => (
              <option key={spec.id} value={spec.id}>
                {spec.name}
              </option>
            ))}
          </datalist>
        </Field>
        <Field label="显示名称" className="w-[190px]">
          <Input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="例如 GPT-4o Mini"
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

      {catalogError && (
        <Alert>未能从供应商拉取模型清单，已回退内置清单：{catalogError}</Alert>
      )}

      <FormRow>
        {/* 协议由供应商决定（见需求"模型管理层"第 1c 条），选项值必须是 adapter 的协议 ID。 */}
        <Field label="协议" className="w-[190px]">
          <Select value={form.api} onChange={(e) => setForm({ ...form, api: e.target.value })}>
            <option value="openai-completions">openai-completions</option>
            <option value="anthropic-messages">anthropic-messages</option>
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
        <FieldLabel>
          能力（决定哪些任务可路由到该模型；选中模型后按供应商返回的能力自动带出）
        </FieldLabel>
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
        <FieldLabel>
          高级配置项（勾选后才生效；取消勾选则调用该模型时不发送这一项）
        </FieldLabel>
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          {advancedItems.map((item) => (
            <AdvancedItem
              key={item.key}
              item={item}
              advanced={form.advanced}
              checked={item_checked(item, advancedEnabled)}
              onToggle={toggleAdvancedItem}
              onChange={setAdvanced}
              onChoice={toggleAdvancedChoice}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit">
          {initial.id ? null : <Plus size={14} />}
          {submitLabel}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={runTest}
          disabled={testing || !form.id || !form.provider}
          title="先选择供应商并填写模型名称，再测试连接"
        >
          <Zap size={14} />
          {testing ? "测试中…" : "测试连接"}
        </Button>
      </div>

      {testResult && (
        <Alert tone={testResult.ok ? "ok" : "error"}>
          {testResult.ok
            ? `连接成功（${testResult.latency_ms} ms）`
            : `连接失败：${testResult.code ? `${testResult.code}：` : ""}${testResult.message}`}
        </Alert>
      )}
    </form>
  );
}

/**
 * 一个高级配置项。
 *
 * ``number`` 项 = 勾选框 + 输入框；``choice`` 项 = 一组互斥勾选框，选一个即
 * 自动取消另一个（需求："有的能力可能互斥，这个时候用户只能选择一个"）。
 */
function AdvancedItem({ item, advanced, checked, onToggle, onChange, onChoice }) {
  if (item.kind === "choice") {
    return (
      <div className="flex flex-col gap-1.5">
        <span className="text-xs text-muted">{item.label}</span>
        <div className="flex items-center gap-4">
          {item.choices.map((choice) => (
            <CheckboxField
              key={choice.value}
              checked={advanced[item.key] === choice.value}
              onChange={() => onChoice(item.key, choice.value)}
            >
              {choice.label}
            </CheckboxField>
          ))}
        </div>
        {item.note && <span className="text-xs text-faint">{item.note}</span>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <CheckboxField checked={checked} onChange={(e) => onToggle(item, e.target.checked)}>
        {item.label}
      </CheckboxField>
      <Input
        type="number"
        step={item.step ?? "0.05"}
        className="w-[130px]"
        disabled={!checked}
        value={advanced[item.key] ?? ""}
        onChange={(e) => onChange(item.key, e.target.value)}
      />
    </div>
  );
}
