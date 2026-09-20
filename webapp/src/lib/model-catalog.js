/**
 * 模型高级配置项的界面状态计算（需求"模型管理层"第 1c 条）。
 *
 * 需求要求：高级配置项的每一项带一个勾选框，取消勾选后**向该模型发送请求时
 * 不再包含这个参数**；互斥项只能选一个，另一个自动取消勾选。
 *
 * 因此这里把"勾选状态"与"发送的值"分开表达：
 *
 * - ``enabled`` 记录勾选状态（界面意图）；
 * - 提交时由 :func:`build_advanced` 把未勾选项写成 ``null``。``null`` 的语义是
 *   "不发送该字段"，与 ``0`` 完全不同——``temperature=0`` 是确定性采样，把它
 *   当成"未配置"会静默改变模型行为。
 *
 * 抽成纯函数是为了在没有 DOM 的测试环境里直接验证这套语义。
 */

/** 拿不到供应商清单（自定义供应商、离线、上游报错）时的兜底项。 */
export const DEFAULT_ADVANCED_ITEMS = [
  { key: "temperature", label: "temperature", kind: "number", step: 0.05 },
  { key: "top_p", label: "top_p", kind: "number", step: 0.05 },
  { key: "top_k", label: "top_k", kind: "number", step: 1 },
  { key: "max_tool_rounds", label: "工具调用轮数", kind: "number", step: 1 },
  {
    key: "thinking_mode",
    label: "思考模式",
    kind: "choice",
    choices: [
      { value: "on", label: "开启" },
      { value: "off", label: "关闭" },
    ],
    note: "开启与关闭互斥；都不勾选则跟随模型默认。",
  },
];

/** 从已有的高级配置反推勾选状态：有值的项视为已勾选。 */
export function initial_enabled(advanced = {}) {
  const enabled = {};
  for (const [key, value] of Object.entries(advanced)) {
    enabled[key] = value !== null && value !== undefined && value !== "";
  }
  return enabled;
}

/** 勾选/取消一个数值项。取消时清空输入框，避免留下"看不见却会被提交"的值。 */
export function toggle_item(advanced = {}, enabled = {}, key, on) {
  return {
    advanced: { ...advanced, [key]: on ? (advanced[key] ?? "") : "" },
    enabled: { ...enabled, [key]: on },
  };
}

/** 提交值：未勾选的数值项一律写成 ``null``（= 不发送该参数）。 */
export function build_advanced(items = [], advanced = {}, enabled = {}) {
  const out = { ...advanced };
  for (const item of items) {
    if (item.kind === "number" && !enabled[item.key]) out[item.key] = null;
  }
  return out;
}

/**
 * 互斥选项的勾选/取消。
 *
 * 一个字段只可能持有一个值，因此"勾一个自动取消另一个"是天然成立的；
 * 再点一次已选中的选项则回到 ``default``（= 不发送，跟随供应商默认）。
 */
export function toggle_choice(advanced = {}, key, value) {
  const current = advanced[key] ?? "default";
  return { ...advanced, [key]: current === value ? "default" : value };
}

/** 数值项勾选后显示输入框；未勾选时该参数不会出现在请求里。 */
export function item_checked(item, enabled = {}) {
  return !!enabled[item.key];
}