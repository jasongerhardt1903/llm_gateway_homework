/**
 * 高级配置项勾选语义的测试（需求"模型管理层"第 1c 条）。
 *
 * 这里只测纯函数：界面没有 jsdom，勾选与提交的语义却必须可验证——"取消勾选后
 * 不发送该参数""互斥项只能选一个"正是这条需求的两处易错点。
 */

import { describe, expect, it } from "vitest";
import {
  DEFAULT_ADVANCED_ITEMS,
  build_advanced,
  initial_enabled,
  item_checked,
  toggle_choice,
  toggle_item,
} from "../lib/model-catalog.js";

const THINKING = DEFAULT_ADVANCED_ITEMS.find((item) => item.kind === "choice");
const TEMPERATURE = DEFAULT_ADVANCED_ITEMS.find((item) => item.key === "temperature");

describe("高级配置项勾选", () => {
  it("既有值视为已勾选，未配置项默认不勾选", () => {
    expect(initial_enabled({ temperature: 0.7 })).toEqual({ temperature: true });
    // 0 是合法取值（确定性采样），不能当成"未配置"。
    expect(initial_enabled({ temperature: 0 })).toEqual({ temperature: true });
    expect(initial_enabled({ temperature: null })).toEqual({ temperature: false });
    expect(initial_enabled({ temperature: "" })).toEqual({ temperature: false });
  });

  it("取消勾选会清空输入框，避免看不见的值被提交", () => {
    const on = toggle_item({}, {}, "temperature", true);
    expect(on.enabled.temperature).toBe(true);

    const typed = { value: 0.5, enabled: on.enabled, key: "temperature" };
    const off = toggle_item({ temperature: typed.value }, typed.enabled, typed.key, false);
    expect(off.enabled.temperature).toBe(false);
    expect(off.advanced.temperature).toBe("");
  });

  it("未勾选的项提交为 null（= 不发送），而不是 0", () => {
    const advanced = build_advanced(
      DEFAULT_ADVANCED_ITEMS,
      { temperature: 0.5, top_p: 0.9, thinking_mode: "on" },
      { temperature: true }
    );
    expect(advanced.temperature).toBe(0.5);
    expect(advanced.top_p).toBe(null);
    expect(advanced.top_k).toBe(null);
    expect(advanced.max_tool_rounds).toBe(null);
    // 互斥项不参与"勾选开关"，它的值本身就是发送与否的依据。
    expect(advanced.thinking_mode).toBe("on");
  });

  it("互斥项：选中一个即取消另一个，再点一次回到默认", () => {
    const on = toggle_choice({}, "thinking_mode", "on");
    expect(on.thinking_mode).toBe("on");

    // 选"关闭"时"开启"自动失效——同一字段只有一个值。
    const off = toggle_choice(on, "thinking_mode", "off");
    expect(off.thinking_mode).toBe("off");

    const back = toggle_choice(off, "thinking_mode", "off");
    expect(back.thinking_mode).toBe("default");
  });

  it("兜底清单里每项都可勾选，且思考模式是互斥选项", () => {
    for (const item of DEFAULT_ADVANCED_ITEMS) {
      expect(item.key).toBeTruthy();
      expect(["number", "choice"]).toContain(item.kind);
    }
    expect(item_checked(TEMPERATURE, { temperature: true })).toBe(true);
    expect(item_checked(TEMPERATURE, {})).toBe(false);
    expect(THINKING.choices.map((choice) => choice.value)).toEqual(["on", "off"]);
  });
});