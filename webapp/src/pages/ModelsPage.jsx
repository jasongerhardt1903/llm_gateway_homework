import { useEffect, useState } from "react";
import { deleteModel, createModel, listModels, listProviders, updateModel } from "../api.js";

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

  return (
    <div className="page">
      <h2>模型定义</h2>
      {error && <div className="banner banner-error">{error}</div>}
      {message && <div className="banner banner-ok">{message}</div>}

      <section>
        <h3>模型清单</h3>
        {models.length === 0 ? (
          <p className="muted">尚未定义模型，从下方供应商中选择创建一个。</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>供应商</th>
                <th>名称</th>
                <th>实际模型</th>
                <th>Tag</th>
                <th>协议</th>
                <th>能力</th>
                <th>密钥</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={`${model.provider}/${model.id}`}>
                  <td>{model.display_provider || model.provider}</td>
                  <td>{model.name}</td>
                  <td>{model.id}</td>
                  <td>{model.tag || "—"}</td>
                  <td>{model.api}</td>
                  <td>{capability_badge(model.capabilities)}</td>
                  <td>{model.api_key_set ? "已配置" : "—"}</td>
                  <td>
                    <div className="row-actions">
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        onClick={() => setEditing(editing?.provider === model.provider && editing?.id === model.id ? null : model)}
                      >
                        {editing?.provider === model.provider && editing?.id === model.id ? "取消" : "编辑"}
                      </button>
                      <button type="button" className="btn-danger btn-sm" onClick={() => handleDelete(model)}>
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ModelForm
        key={editing ? `${editing.provider}/${editing.id}` : "new"}
        providers={providers}
        initial={editing ?? {}}
        submitLabel={editing ? "保存修改" : "新增模型"}
        onSubmit={handleSaveModel}
      />
    </div>
  );
}

/** 能力用一行小字列出，保持表格宽度可控。 */
function capability_badge(caps = {}) {
  const flags = ["sse", "streaming", "tools", "json_schema", "vision", "reasoning"]
    .filter((k) => caps[k])
    .map((k) => k.replace("_", " "));
  return flags.join(" · ") || "—";
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

  const setCapability = (key, value) => setForm({ ...form, capabilities: { ...form.capabilities, [key]: value } });
  const setCost = (key, value) => setForm({ ...form, cost: { ...form.cost, [key]: Number(value) } });
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
    <section>
      <h3>{submitLabel}</h3>
      <form onSubmit={submit}>
        <div className="row">
          <label className="field">
            供应商
            <select value={form.provider} onChange={(e) => selectProvider(e.target.value)} required>
              <option value="">选择供应商…</option>
              {providers.map((p) => (
                <option key={p.provider} value={p.provider}>
                  {p.display_name}（{p.provider}）
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            模型名称
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如 GPT-4o Mini" />
          </label>
          <label className="field">
            实际模型 ID
            <input value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} required />
          </label>
          <label className="field">
            Tag
            <input value={form.tag} onChange={(e) => setForm({ ...form, tag: e.target.value })} placeholder="例如 便宜 / 高质" />
          </label>
        </div>

        <div className="row">
          <label className="field">
            协议
            <select value={form.api} onChange={(e) => setForm({ ...form, api: e.target.value })}>
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
            </select>
          </label>
          <label className="field grow">
            Base URL
            <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} required />
          </label>
          <label className="field">
            上下文窗口
            <input type="number" value={form.context_window} onChange={(e) => setForm({ ...form, context_window: Number(e.target.value) })} />
          </label>
          <label className="field">
            最大输出
            <input type="number" value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })} />
          </label>
        </div>

        <div className="row">
          <label className="field grow">
            API Key
            <input
              type="password"
              value={apiKey}
              disabled={clearKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={initial.api_key_set ? "已配置（留空表示不修改）" : "可留空，改用环境变量"}
            />
          </label>
          <label className="field checkbox">
            <input type="checkbox" checked={clearKey} onChange={(e) => setClearKey(e.target.checked)} />
            清除已配置的密钥
          </label>
        </div>

        <p className="field-label">能力（决定哪些任务可路由到该模型）</p>
        <div className="row">
          {["sse", "streaming", "tools", "json_schema", "vision", "reasoning"].map((key) => (
            <label key={key} className="field checkbox">
              <input type="checkbox" checked={!!form.capabilities[key]} onChange={(e) => setCapability(key, e.target.checked)} />
              {key}
            </label>
          ))}
        </div>

        <p className="field-label">成本（每 1K token 单价）</p>
        <div className="row">
          {["input", "output", "cache_read", "cache_write"].map((key) => (
            <label key={key} className="field">
              {key}
              <input type="number" step="0.0001" value={form.cost[key] ?? ""} onChange={(e) => setCost(key, e.target.value)} />
            </label>
          ))}
        </div>

        <p className="field-label">高级配置项（留空表示不发送该参数，用供应商默认）</p>
        <div className="row">
          {ADVANCED_NUMBERS.map(([key, label, type]) => (
            <label key={key} className="field">
              {label}
              <input
                type={type}
                step={key === "top_k" || key === "max_tool_rounds" ? "1" : "0.05"}
                value={form.advanced[key] ?? ""}
                onChange={(e) => setAdvanced(key, e.target.value)}
              />
            </label>
          ))}
          <label className="field">
            思考模式
            <select
              value={form.advanced.thinking_mode ?? "default"}
              onChange={(e) => setAdvanced("thinking_mode", e.target.value)}
            >
              <option value="default">跟随模型默认</option>
              <option value="on">开启</option>
              <option value="off">关闭</option>
            </select>
          </label>
        </div>

        <div>
          <button type="submit">{submitLabel}</button>
        </div>
      </form>
    </section>
  );
}
