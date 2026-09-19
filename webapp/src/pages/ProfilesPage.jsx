import { useEffect, useState } from "react";
import { createProfile, deleteProfile, listModels, listProfiles, updateProfile } from "../api.js";

/**
 * gwprofile 管理页（需求第 31 行）。
 *
 * 一个 profile 定义三件事：
 * 1. **模型编组**：包含哪些模型（下拉多选），并对每个模型选择"本模型配置优先于模版"；
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

  return (
    <div className="page">
      <h2>Profile（gwprofile）</h2>
      {error && <div className="banner banner-error">{error}</div>}
      {message && <div className="banner banner-ok">{message}</div>}

      <section>
        <h3>Profile 清单</h3>
        {profiles.length === 0 ? (
          <p className="muted">尚未配置 profile。未指定 profile 的请求将走全局模型池。</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>名称</th>
                <th>模型</th>
                <th>路由</th>
                <th>模版</th>
                <th>最大重试</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile) => (
                <tr key={profile.name}>
                  <td>{profile.display_name || profile.name}</td>
                  <td>{profile.models.map((m) => m.label).join(", ")}</td>
                  <td>
                    {profile.route_mode === "static" ? "静态" : "动态"}
                    {profile.route_mode === "static" && profile.static_order.length
                      ? `（${profile.static_order.join(" > ")}）`
                      : ""}
                  </td>
                  <td>{profile.template_enabled ? "已启用" : "未启用"}</td>
                  <td>{profile.retry_enabled ? profile.max_retries : "关闭"}</td>
                  <td>
                    <button type="button" onClick={() => setEditing(editing?.name === profile.name ? null : profile)}>
                      {editing?.name === profile.name ? "取消" : "编辑"}
                    </button>
                    <button type="button" className="danger" onClick={() => handleDelete(profile)}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ProfileForm
        key={editing ? editing.name : "new"}
        models={models}
        initial={editing ?? {}}
        submitLabel={editing ? "保存修改" : "新增 Profile"}
        onSubmit={handleSave}
      />
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
      prefer: prefer,
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
        form.route_mode === "static"
          ? static_text_to_list(staticText)
          : [],
    };
    onSubmit(payload);
  };

  return (
    <section>
      <h3>{submitLabel}</h3>
      <form onSubmit={submit}>
        <div className="row">
          <label className="field">
            Profile 名
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required disabled={!!initial.name} />
          </label>
          <label className="field grow">
            展示名称
            <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </label>
        </div>

        <p className="field-label">包含的模型（勾选加入，勾选"优先"表示该模型高级配置优先于模版）</p>
        <div className="row">
          {models.length === 0 ? (
            <p className="muted">先在「模型定义」页创建模型。</p>
          ) : (
            models.map((model) => {
              const label = `${model.provider}/${model.id}`;
              const included = form.model_labels.includes(label);
              return (
                <label key={label} className="field checkbox">
                  <input
                    type="checkbox"
                    checked={included}
                    onChange={() => toggleModel(label)}
                  />
                  {model.name || model.id}
                  {included && (
                    <input
                      type="checkbox"
                      checked={form.prefer.has(label)}
                      onChange={() => togglePrefer(label)}
                      title="本模型配置优先于模版"
                    />
                  )}
                </label>
              );
            })
          )}
        </div>

        <p className="field-label">高级配置模版（在 profile 内生效，勾选后按需启用）</p>
        <label className="field checkbox">
          <input
            type="checkbox"
            checked={form.template_enabled}
            onChange={(e) => setForm({ ...form, template_enabled: e.target.checked })}
          />
          启用模版
        </label>
        <div className="row">
          {TEMPLATE_NUMBERS.map(([key, label]) => (
            <label key={key} className="field">
              {label}
              <input
                type="number"
                step={key === "top_k" || key === "max_tool_rounds" ? "1" : "0.05"}
                value={form.template[key] ?? ""}
                onChange={(e) => setForm({ ...form, template: { ...form.template, [key]: e.target.value } })}
              />
            </label>
          ))}
          <label className="field">
            思考模式
            <select
              value={form.template.thinking_mode ?? "default"}
              onChange={(e) => setForm({ ...form, template: { ...form.template, thinking_mode: e.target.value } })}
            >
              <option value="default">跟随模型默认</option>
              <option value="on">开启</option>
              <option value="off">关闭</option>
            </select>
          </label>
        </div>

        <p className="field-label">路由配置（需求第 31 行：动态 / 静态二选一）</p>
        <div className="row">
          <label className="field">
            路由模式
            <select value={form.route_mode} onChange={(e) => setForm({ ...form, route_mode: e.target.value })}>
              <option value="dynamic">动态路由</option>
              <option value="static">静态路由</option>
            </select>
          </label>
          {form.route_mode === "static" && (
            <label className="field grow">
              优先顺序（逗号分隔，主 → 备）
              <input
                value={staticText}
                onChange={(e) => setStaticText(e.target.value)}
                placeholder="openai/gpt-4o-mini, deepseek/deepseek-chat"
              />
            </label>
          )}
        </div>

        <p className="field-label">重试策略（需求第 128 行）</p>
        <div className="row">
          <label className="field">
            最大重试次数
            <input
              type="number"
              min="0"
              max="10"
              value={form.max_retries}
              onChange={(e) => setForm({ ...form, max_retries: Number(e.target.value) })}
            />
          </label>
          <label className="field checkbox">
            <input
              type="checkbox"
              checked={form.retry_enabled}
              onChange={(e) => setForm({ ...form, retry_enabled: e.target.checked })}
            />
            启用重试
          </label>
        </div>

        <div>
          <button type="submit">{submitLabel}</button>
        </div>
      </form>
    </section>
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